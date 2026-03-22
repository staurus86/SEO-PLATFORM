"""
Shared task storage utilities — Redis + in-memory fallback.

All tool endpoints import from here to read/write task results.
"""
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.core.memory_guard import mark_activity, register_cleanup_callback

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

_TERMINAL_STATUSES = {"SUCCESS", "FAILURE"}


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


def _save_task_payload(task_id: str, data: Dict[str, Any]) -> None:
    """Persist task payload in Redis (24h TTL) or memory fallback."""
    mark_activity("task_store:save")
    _maybe_prune_memory()

    redis_client = get_redis_client()
    if redis_client:
        try:
            redis_client.setex(_task_key(task_id), 86400, json.dumps(data))
            return
        except Exception as exc:
            _mark_redis_unavailable(exc, "set")

    now = time.time()
    with _task_lock:
        task_results_memory[task_id] = data
        _task_updated_at[task_id] = now
        _task_last_access_at[task_id] = now
        _task_payload_size_bytes[task_id] = _estimate_payload_size_bytes(data)

    cleanup_task_results_memory(idle_seconds=0.0, aggressive=False)


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
    _save_task_payload(task_id, data)
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
    _save_task_payload(task_id, task)

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
