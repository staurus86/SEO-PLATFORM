"""
Shared task storage utilities — Redis + in-memory fallback.

All tool endpoints import from here to read/write task results.
"""
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from copy import deepcopy

from app.core.memory_guard import mark_activity, register_cleanup_callback
from app.core.ops_observability import (
    record_task_completed,
    record_task_result_size,
    record_task_started,
)

# Redis-based storage for task results
_redis_client = None
_redis_next_retry_ts = 0.0

# Memory fallback storage (used when Redis is unavailable)
task_results_memory: Dict[str, Any] = {}
_task_updated_at: Dict[str, float] = {}
_task_last_access_at: Dict[str, float] = {}
_task_payload_size_bytes: Dict[str, int] = {}
_task_lock = threading.RLock()
_last_memory_prune_ts = 0.0
_task_compaction_stats: Dict[str, Any] = {
    "compactions_total": 0,
    "original_bytes_total": 0,
    "stored_bytes_total": 0,
    "bytes_saved_total": 0,
    "last_compacted_at": None,
}

_TERMINAL_STATUSES = {"SUCCESS", "FAILURE"}
_HEAVY_DEBUG_KEYS = {
    "raw_html",
    "rendered_html",
    "page_source",
    "dom_html",
    "full_html",
    "network_log",
    "raw_response",
    "response_body",
    "html_body",
}


def _compact_console_log(value: Dict[str, Any], removed: list[str], path: str) -> Dict[str, Any]:
    compacted = dict(value)
    for key in ("errors", "warnings"):
        items = compacted.get(key)
        if isinstance(items, list) and len(items) > 5:
            omitted = len(items) - 5
            compacted[key] = items[:5]
            compacted[f"{key}_omitted"] = omitted
            removed.append(f"{path}.{key}[{omitted}]")
    return compacted


def _compact_heavy_value(value: Any, removed: list[str], path: str = "") -> Any:
    if isinstance(value, dict):
        compacted: Dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in _HEAVY_DEBUG_KEYS:
                removed.append(child_path)
                continue
            if key == "console_log" and isinstance(item, dict):
                compacted[key] = _compact_console_log(item, removed, child_path)
                continue
            compacted[key] = _compact_heavy_value(item, removed, child_path)
        return compacted
    if isinstance(value, list):
        return [_compact_heavy_value(item, removed, f"{path}[{idx}]") for idx, item in enumerate(value)]
    return value


def _compact_task_payload(task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    from app.config import settings

    original_size = _estimate_payload_size_bytes(data)
    threshold = max(32 * 1024, int(getattr(settings, "TASK_STORE_COMPACT_THRESHOLD_BYTES", 768 * 1024) or (768 * 1024)))
    if original_size <= threshold:
        return data

    removed: list[str] = []
    compacted = _compact_heavy_value(deepcopy(data), removed)
    if not removed:
        return data

    compacted_size = _estimate_payload_size_bytes(compacted)
    storage_meta = dict(compacted.get("storage_meta") or {})
    storage_meta.update(
        {
            "payload_compacted": True,
            "original_bytes": original_size,
            "stored_bytes": compacted_size,
            "removed_fields": removed[:50],
            "removed_fields_count": len(removed),
        }
    )
    compacted["storage_meta"] = storage_meta
    with _task_lock:
        _task_compaction_stats["compactions_total"] = int(_task_compaction_stats["compactions_total"]) + 1
        _task_compaction_stats["original_bytes_total"] = int(_task_compaction_stats["original_bytes_total"]) + int(original_size)
        _task_compaction_stats["stored_bytes_total"] = int(_task_compaction_stats["stored_bytes_total"]) + int(compacted_size)
        _task_compaction_stats["bytes_saved_total"] = int(_task_compaction_stats["bytes_saved_total"]) + max(0, int(original_size) - int(compacted_size))
        _task_compaction_stats["last_compacted_at"] = _utc_now_iso()
    print(f"[API] Compacted task payload {task_id}: {original_size} -> {compacted_size} bytes; removed {len(removed)} fields")
    return compacted


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_redis_unavailable(exc: Exception, where: str) -> None:
    global _redis_client, _redis_next_retry_ts
    from app.config import settings

    _redis_client = None
    cooldown = max(5, int(getattr(settings, "REDIS_RETRY_COOLDOWN_SEC", 30) or 30))
    _redis_next_retry_ts = time.time() + cooldown
    print(f"[API] Redis unavailable for task results ({where}): {exc}; retry in {cooldown}s")


def _estimate_payload_size_bytes(payload: Dict[str, Any]) -> int:
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def _drop_task_from_memory(task_id: str) -> None:
    task_results_memory.pop(task_id, None)
    _task_updated_at.pop(task_id, None)
    _task_last_access_at.pop(task_id, None)
    _task_payload_size_bytes.pop(task_id, None)


def get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if time.time() < _redis_next_retry_ts:
        return None
    try:
        import redis
        from app.config import settings

        _redis_client = redis.from_url(settings.TASK_STORE_REDIS_URL, decode_responses=True)
        _redis_client.ping()
        print("[API] Redis connection established for task results")
    except Exception as exc:
        _mark_redis_unavailable(exc, "connect")
    return _redis_client


def _task_key(task_id: str) -> str:
    from app.config import settings

    prefix = str(getattr(settings, "TASK_STORE_REDIS_PREFIX", "task") or "task").strip(": ")
    return f"{prefix}:{task_id}"


def cleanup_task_results_memory(idle_seconds: float = 0.0, aggressive: bool = False) -> Dict[str, Any]:
    from app.config import settings

    ttl_sec = max(60, int(getattr(settings, "TASK_STORE_MEMORY_TTL_SEC", 7200) or 7200))
    max_items = max(10, int(getattr(settings, "TASK_STORE_MEMORY_MAX_ITEMS", 200) or 200))
    max_bytes = max(1024, int(getattr(settings, "TASK_STORE_MEMORY_MAX_BYTES", 64 * 1024 * 1024) or (64 * 1024 * 1024)))
    idle_keep_sec = max(60, int(getattr(settings, "TASK_STORE_IDLE_KEEP_SEC", 900) or 900))
    now = time.time()

    removed_expired = 0
    removed_idle = 0
    removed_overflow = 0
    removed_oversize = 0

    with _task_lock:
        for task_id, updated_at in list(_task_updated_at.items()):
            if now - updated_at > ttl_sec:
                _drop_task_from_memory(task_id)
                removed_expired += 1

        if aggressive and idle_seconds >= idle_keep_sec:
            for task_id, payload in list(task_results_memory.items()):
                status = str((payload or {}).get("status", "")).upper()
                if status not in _TERMINAL_STATUSES:
                    continue
                last_access = _task_last_access_at.get(task_id, _task_updated_at.get(task_id, now))
                if now - last_access >= idle_keep_sec:
                    _drop_task_from_memory(task_id)
                    removed_idle += 1

        if len(task_results_memory) > max_items:
            overflow = len(task_results_memory) - max_items
            eviction_order = sorted(
                list(task_results_memory.keys()),
                key=lambda tid: (
                    0 if str((task_results_memory.get(tid) or {}).get("status", "")).upper() in _TERMINAL_STATUSES else 1,
                    _task_last_access_at.get(tid, _task_updated_at.get(tid, now)),
                ),
            )
            for task_id in eviction_order:
                if overflow <= 0:
                    break
                _drop_task_from_memory(task_id)
                removed_overflow += 1
                overflow -= 1

        total_bytes = int(sum(_task_payload_size_bytes.values()))
        if total_bytes > max_bytes:
            eviction_order = sorted(
                list(task_results_memory.keys()),
                key=lambda tid: (
                    0 if str((task_results_memory.get(tid) or {}).get("status", "")).upper() in _TERMINAL_STATUSES else 1,
                    _task_last_access_at.get(tid, _task_updated_at.get(tid, now)),
                ),
            )
            for task_id in eviction_order:
                if total_bytes <= max_bytes:
                    break
                total_bytes -= int(_task_payload_size_bytes.get(task_id, 0))
                _drop_task_from_memory(task_id)
                removed_oversize += 1

        terminal_count = 0
        active_count = 0
        for payload in task_results_memory.values():
            status = str((payload or {}).get("status", "")).upper()
            if status in _TERMINAL_STATUSES:
                terminal_count += 1
            else:
                active_count += 1

        total_bytes = int(sum(_task_payload_size_bytes.values()))
        items_total = len(task_results_memory)
        oldest_age_sec = (
            round(now - min(_task_updated_at.values()), 2)
            if _task_updated_at
            else 0.0
        )

    return {
        "removed_expired": removed_expired,
        "removed_idle": removed_idle,
        "removed_overflow": removed_overflow,
        "removed_oversize": removed_oversize,
        "removed_total": removed_expired + removed_idle + removed_overflow + removed_oversize,
        "items_total": items_total,
        "items_active": active_count,
        "items_terminal": terminal_count,
        "bytes_total": total_bytes,
        "ttl_sec": ttl_sec,
        "max_items": max_items,
        "max_bytes": max_bytes,
        "oldest_age_sec": oldest_age_sec,
    }


def get_task_store_memory_stats() -> Dict[str, Any]:
    return cleanup_task_results_memory(idle_seconds=0.0, aggressive=False)


def get_task_store_compaction_stats() -> Dict[str, Any]:
    from app.config import settings

    with _task_lock:
        stats = dict(_task_compaction_stats)
    stats["threshold_bytes"] = max(
        32 * 1024,
        int(getattr(settings, "TASK_STORE_COMPACT_THRESHOLD_BYTES", 768 * 1024) or (768 * 1024)),
    )
    return stats


def _maybe_prune_memory() -> None:
    global _last_memory_prune_ts
    now = time.time()
    if now - _last_memory_prune_ts < 15:
        return
    _last_memory_prune_ts = now
    cleanup_task_results_memory(idle_seconds=0.0, aggressive=False)


def get_task_result(task_id: str) -> Optional[Dict[str, Any]]:
    """Get task result from Redis or memory fallback."""
    mark_activity("task_store:get")
    _maybe_prune_memory()

    redis_client = get_redis_client()
    if redis_client:
        try:
            data = redis_client.get(_task_key(task_id))
            if data:
                return json.loads(data)
        except Exception as exc:
            _mark_redis_unavailable(exc, "get")

    # Fallback to memory (for development without Redis)
    with _task_lock:
        task = task_results_memory.get(task_id)
        if task is not None:
            _task_last_access_at[task_id] = time.time()
        return task


def _save_task_payload(task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Persist task payload in Redis (24h TTL) or memory fallback."""
    mark_activity("task_store:save")
    _maybe_prune_memory()
    original_payload_size = _estimate_payload_size_bytes(data)
    data = _compact_task_payload(task_id, data)
    stored_payload_size = _estimate_payload_size_bytes(data)

    redis_client = get_redis_client()
    if redis_client:
        try:
            redis_client.setex(_task_key(task_id), 86400, json.dumps(data))
            return data
        except Exception as exc:
            _mark_redis_unavailable(exc, "set")

    now = time.time()
    with _task_lock:
        task_results_memory[task_id] = data
        _task_updated_at[task_id] = now
        _task_last_access_at[task_id] = now
        _task_payload_size_bytes[task_id] = stored_payload_size

    cleanup_task_results_memory(idle_seconds=0.0, aggressive=False)
    if str(data.get("status") or "").upper() in _TERMINAL_STATUSES:
        record_task_result_size(original_payload_size, stored_payload_size)
    return data


def create_task_result(task_id: str, task_type: str, url: str, result: Dict[str, Any]):
    """Store task result in Redis with 24 hour TTL."""
    now = _utc_now_iso()
    data = {
        "task_id": task_id,
        "task_type": task_type,
        "url": url,
        "status": "SUCCESS",
        "progress": 100,
        "status_message": "Completed",
        "error": None,
        "created_at": now,
        "started_at": now,
        "updated_at": now,
        "result": result,
        "completed_at": now,
    }
    saved = _save_task_payload(task_id, data)
    record_task_started(saved.get("created_at"), saved.get("started_at"))
    record_task_completed(saved.get("created_at"), saved.get("started_at"), saved.get("completed_at"))
    print(f"[API] Task {task_id} stored")


def create_task_pending(
    task_id: str, task_type: str, url: str, status_message: str = "Queued"
) -> None:
    """Create task record in pending state."""
    now = _utc_now_iso()
    data = {
        "task_id": task_id,
        "task_type": task_type,
        "url": url,
        "status": "PENDING",
        "progress": 0,
        "progress_meta": {},
        "status_message": status_message,
        "error": None,
        "created_at": now,
        "started_at": None,
        "updated_at": now,
        "completed_at": None,
        "result": None,
    }
    _save_task_payload(task_id, data)


def update_task_state(
    task_id: str,
    *,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    status_message: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    progress_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Update task fields while preserving existing payload."""
    task = get_task_result(task_id)
    if not task:
        return
    now = _utc_now_iso()
    previous_started_at = task.get("started_at")
    previous_completed_at = task.get("completed_at")
    if status is not None:
        task["status"] = status
        if status == "RUNNING" and not task.get("started_at"):
            task["started_at"] = now
    if progress is not None:
        task["progress"] = max(0, min(100, int(progress)))
    if status_message is not None:
        task["status_message"] = status_message
    if result is not None:
        task["result"] = result
    if error is not None:
        task["error"] = error
    if progress_meta is not None:
        task["progress_meta"] = progress_meta
    task["updated_at"] = now
    if status in ("SUCCESS", "FAILURE"):
        if not task.get("started_at"):
            task["started_at"] = now
        task["completed_at"] = now
    saved = _save_task_payload(task_id, task)
    if status == "RUNNING" and not previous_started_at and saved.get("started_at"):
        record_task_started(saved.get("created_at"), saved.get("started_at"))
    if status in ("SUCCESS", "FAILURE") and not previous_completed_at and saved.get("completed_at"):
        record_task_completed(saved.get("created_at"), saved.get("started_at"), saved.get("completed_at"))

    # Broadcast update via WebSocket (best-effort, fire-and-forget)
    try:
        import asyncio as _aio
        from app.main import ws_manager  # lazy import to avoid circular dependency

        broadcast_data = {"task_id": task_id}
        if status is not None:
            broadcast_data["status"] = status
        if progress is not None:
            broadcast_data["progress"] = task["progress"]
        if status_message is not None:
            broadcast_data["status_message"] = status_message
        if result is not None:
            broadcast_data["result"] = result
        if error is not None:
            broadcast_data["error"] = error
        if progress_meta is not None:
            broadcast_data["progress_meta"] = progress_meta
        broadcast_data["updated_at"] = task.get("updated_at")

        loop = _aio.get_event_loop()
        if loop.is_running():
            _aio.ensure_future(ws_manager.broadcast(task_id, broadcast_data))
    except Exception:
        pass  # WebSocket broadcast is best-effort


def append_task_artifact(task_id: str, artifact_path: str, kind: str = "report") -> None:
    """Attach generated artifact path to task payload for future cleanup."""
    task = get_task_result(task_id)
    if not task:
        return
    bucket = task.setdefault("artifacts", {})
    by_kind = bucket.setdefault(kind, [])
    if artifact_path not in by_kind:
        by_kind.append(artifact_path)
    _save_task_payload(task_id, task)


def delete_task_result(task_id: str) -> bool:
    """Delete task result from Redis/memory storage."""
    mark_activity("task_store:delete")

    deleted = False
    redis_client = get_redis_client()
    if redis_client:
        try:
            deleted = bool(redis_client.delete(_task_key(task_id))) or deleted
        except Exception as exc:
            _mark_redis_unavailable(exc, "delete")
    with _task_lock:
        if task_id in task_results_memory:
            _drop_task_from_memory(task_id)
            deleted = True
    return deleted


register_cleanup_callback("task_store_memory", cleanup_task_results_memory)
