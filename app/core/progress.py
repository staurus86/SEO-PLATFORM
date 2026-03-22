"""
Progress tracking for long-running tasks
"""
import json
import logging
import threading
import time
from typing import Optional, Dict, Any
from copy import deepcopy
from app.config import settings
from app.core.memory_guard import mark_activity, register_cleanup_callback

logger = logging.getLogger(__name__)
_HEAVY_PROGRESS_KEYS = {
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


class ProgressTracker:
    """Отслеживание прогресса задач через Redis (with fallback to memory)"""
    
    def __init__(self):
        self._redis_client = None
        self._redis_next_retry_ts = 0.0
        self._memory_store = {}  # Fallback storage
        self._memory_updated_at = {}
        self._memory_last_access_at = {}
        self._memory_payload_size_bytes = {}
        self._memory_lock = threading.RLock()
        self.ttl = 3600 * 2  # 2 hours
    
    @property
    def redis_client(self):
        """Lazy initialization of Redis client"""
        if self._redis_client is not None:
            return self._redis_client
        if time.time() < self._redis_next_retry_ts:
            return None
        cooldown = max(5, int(getattr(settings, "REDIS_RETRY_COOLDOWN_SEC", 30) or 30))
        if self._redis_client is None:
            try:
                import redis

                self._redis_client = redis.from_url(settings.PROGRESS_REDIS_URL, decode_responses=True)
                self._redis_client.ping()
                logger.info("Progress tracker: Redis connection established")
            except Exception as e:
                logger.warning("Progress tracker: Redis unavailable, fallback mode (%s), retry in %ss", e, cooldown)
                self._redis_client = None
                self._redis_next_retry_ts = time.time() + cooldown
        return self._redis_client
    
    def _get_key(self, task_id: str) -> str:
        prefix = str(getattr(settings, "PROGRESS_REDIS_PREFIX", "task_progress") or "task_progress").strip(": ")
        return f"{prefix}:{task_id}"

    def _mark_redis_unavailable(self, exc: Exception, where: str) -> None:
        cooldown = max(5, int(getattr(settings, "REDIS_RETRY_COOLDOWN_SEC", 30) or 30))
        self._redis_client = None
        self._redis_next_retry_ts = time.time() + cooldown
        logger.warning("Progress tracker Redis error (%s): %s. Retry in %ss", where, exc, cooldown)

    def _estimate_bytes(self, payload: Dict[str, Any]) -> int:
        try:
            return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            return 0

    def _compact_extra(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        threshold = max(8 * 1024, int(getattr(settings, "PROGRESS_COMPACT_THRESHOLD_BYTES", 128 * 1024) or (128 * 1024)))
        original = {"extra": extra}
        original_size = self._estimate_bytes(original)
        if original_size <= threshold:
            return extra

        removed: list[str] = []

        def _walk(value: Any, path: str = "") -> Any:
            if isinstance(value, dict):
                compacted: Dict[str, Any] = {}
                for key, item in value.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    if key in _HEAVY_PROGRESS_KEYS:
                        removed.append(child_path)
                        continue
                    compacted[key] = _walk(item, child_path)
                return compacted
            if isinstance(value, list):
                return [_walk(item, f"{path}[{idx}]") for idx, item in enumerate(value)]
            return value

        compacted = _walk(deepcopy(extra))
        compacted_size = self._estimate_bytes({"extra": compacted})
        if removed:
            compacted["_storage_meta"] = {
                "payload_compacted": True,
                "original_bytes": original_size,
                "stored_bytes": compacted_size,
                "removed_fields": removed[:20],
                "removed_fields_count": len(removed),
            }
            logger.info("Progress payload compacted: %s -> %s bytes; removed=%s", original_size, compacted_size, len(removed))
        return compacted

    def cleanup_memory(self, idle_seconds: float = 0.0, aggressive: bool = False) -> Dict[str, Any]:
        ttl_sec = max(60, int(getattr(settings, "PROGRESS_MEMORY_TTL_SEC", self.ttl) or self.ttl))
        max_items = max(10, int(getattr(settings, "PROGRESS_MEMORY_MAX_ITEMS", 2000) or 2000))
        max_bytes = max(1024, int(getattr(settings, "PROGRESS_MEMORY_MAX_BYTES", 8 * 1024 * 1024) or (8 * 1024 * 1024)))
        idle_keep_sec = max(60, int(getattr(settings, "PROGRESS_IDLE_KEEP_SEC", 900) or 900))
        now = time.time()
        removed_expired = 0
        removed_idle = 0
        removed_overflow = 0
        removed_oversize = 0

        def _drop(task_id: str) -> None:
            self._memory_store.pop(task_id, None)
            self._memory_updated_at.pop(task_id, None)
            self._memory_last_access_at.pop(task_id, None)
            self._memory_payload_size_bytes.pop(task_id, None)

        with self._memory_lock:
            for task_id, updated_at in list(self._memory_updated_at.items()):
                if now - updated_at > ttl_sec:
                    _drop(task_id)
                    removed_expired += 1

            if aggressive and idle_seconds >= idle_keep_sec:
                for task_id in list(self._memory_store.keys()):
                    last_access = self._memory_last_access_at.get(task_id, self._memory_updated_at.get(task_id, now))
                    if now - last_access >= idle_keep_sec:
                        _drop(task_id)
                        removed_idle += 1

            if len(self._memory_store) > max_items:
                overflow = len(self._memory_store) - max_items
                eviction_order = sorted(
                    list(self._memory_store.keys()),
                    key=lambda tid: self._memory_last_access_at.get(tid, self._memory_updated_at.get(tid, now)),
                )
                for task_id in eviction_order:
                    if overflow <= 0:
                        break
                    _drop(task_id)
                    removed_overflow += 1
                    overflow -= 1

            total_bytes = int(sum(self._memory_payload_size_bytes.values()))
            if total_bytes > max_bytes:
                eviction_order = sorted(
                    list(self._memory_store.keys()),
                    key=lambda tid: self._memory_last_access_at.get(tid, self._memory_updated_at.get(tid, now)),
                )
                for task_id in eviction_order:
                    if total_bytes <= max_bytes:
                        break
                    total_bytes -= int(self._memory_payload_size_bytes.get(task_id, 0))
                    _drop(task_id)
                    removed_oversize += 1

            items_total = len(self._memory_store)
            total_bytes = int(sum(self._memory_payload_size_bytes.values()))

        return {
            "removed_expired": removed_expired,
            "removed_idle": removed_idle,
            "removed_overflow": removed_overflow,
            "removed_oversize": removed_oversize,
            "removed_total": removed_expired + removed_idle + removed_overflow + removed_oversize,
            "items_total": items_total,
            "bytes_total": total_bytes,
            "ttl_sec": ttl_sec,
            "max_items": max_items,
            "max_bytes": max_bytes,
        }

    def get_memory_stats(self) -> Dict[str, Any]:
        return self.cleanup_memory(idle_seconds=0.0, aggressive=False)
    
    def update_progress(
        self,
        task_id: str,
        current: int,
        total: int,
        message: str = "",
        extra: Optional[Dict[str, Any]] = None
    ):
        """Обновляет прогресс задачи"""
        data = {
            "current": current,
            "total": total,
            "percentage": round((current / total * 100), 2) if total > 0 else 0,
            "message": message,
            "extra": self._compact_extra(extra or {})
        }
        mark_activity("progress:update")
        
        if self.redis_client:
            try:
                key = self._get_key(task_id)
                self.redis_client.setex(key, self.ttl, json.dumps(data))
                return
            except Exception as e:
                self._mark_redis_unavailable(e, "update")
        
        # Fallback to memory
        now = time.time()
        with self._memory_lock:
            self._memory_store[task_id] = data
            self._memory_updated_at[task_id] = now
            self._memory_last_access_at[task_id] = now
            self._memory_payload_size_bytes[task_id] = self._estimate_bytes(data)
        self.cleanup_memory(idle_seconds=0.0, aggressive=False)
    
    def get_progress(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Получает текущий прогресс задачи"""
        mark_activity("progress:get")
        if self.redis_client:
            try:
                key = self._get_key(task_id)
                data = self.redis_client.get(key)
                if data:
                    return json.loads(data)
            except Exception as e:
                self._mark_redis_unavailable(e, "get")
        
        # Fallback to memory
        with self._memory_lock:
            item = self._memory_store.get(task_id)
            if item is not None:
                self._memory_last_access_at[task_id] = time.time()
            return item
    
    def clear_progress(self, task_id: str):
        """Очищает прогресс задачи"""
        mark_activity("progress:clear")
        if self.redis_client:
            try:
                key = self._get_key(task_id)
                self.redis_client.delete(key)
            except Exception as e:
                self._mark_redis_unavailable(e, "clear")
        
        # Clear from memory fallback
        with self._memory_lock:
            if task_id in self._memory_store:
                del self._memory_store[task_id]
            self._memory_updated_at.pop(task_id, None)
            self._memory_last_access_at.pop(task_id, None)
            self._memory_payload_size_bytes.pop(task_id, None)


# Singleton
progress_tracker = ProgressTracker()
register_cleanup_callback("progress_memory", progress_tracker.cleanup_memory)
