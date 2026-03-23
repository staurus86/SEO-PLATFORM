"""Lightweight in-process observability counters for ops/status."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Any, Dict, Optional

_lock = threading.RLock()
_WINDOWS = {"recent_15m": 15 * 60, "recent_60m": 60 * 60}
_MAX_EVENTS = 512


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _empty_duration_stats() -> Dict[str, Any]:
    return {
        "count": 0,
        "total_ms": 0,
        "avg_ms": 0,
        "max_ms": 0,
        "last_ms": None,
        "last_at": None,
        "events": deque(maxlen=_MAX_EVENTS),
    }


def _empty_size_stats() -> Dict[str, Any]:
    return {
        "count": 0,
        "original_bytes_total": 0,
        "stored_bytes_total": 0,
        "bytes_saved_total": 0,
        "avg_original_bytes": 0,
        "avg_stored_bytes": 0,
        "avg_bytes_saved": 0,
        "max_original_bytes": 0,
        "max_stored_bytes": 0,
        "last_original_bytes": None,
        "last_stored_bytes": None,
        "last_at": None,
        "events": deque(maxlen=_MAX_EVENTS),
    }


_stats: Dict[str, Any] = {
    "tasks": {
        "queue_wait_ms": _empty_duration_stats(),
        "run_duration_ms": _empty_duration_stats(),
        "end_to_end_ms": _empty_duration_stats(),
        "result_payload_bytes": _empty_size_stats(),
    },
    "llm_jobs": {
        "queue_wait_ms": _empty_duration_stats(),
        "run_duration_ms": _empty_duration_stats(),
        "end_to_end_ms": _empty_duration_stats(),
        "result_payload_bytes": _empty_size_stats(),
    },
    "exports": {
        "generation_ms": _empty_duration_stats(),
        "file_size_bytes": {
            "count": 0,
            "total_bytes": 0,
            "avg_bytes": 0,
            "max_bytes": 0,
            "last_bytes": None,
            "last_format": None,
            "last_at": None,
            "events": deque(maxlen=_MAX_EVENTS),
        },
    },
}


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _record_duration(bucket: Dict[str, Any], value_ms: int) -> None:
    safe = max(0, int(value_ms or 0))
    now_iso = _utc_now_iso()
    bucket["count"] = int(bucket["count"]) + 1
    bucket["total_ms"] = int(bucket["total_ms"]) + safe
    bucket["avg_ms"] = int(bucket["total_ms"] / max(1, int(bucket["count"])))
    bucket["max_ms"] = max(int(bucket["max_ms"]), safe)
    bucket["last_ms"] = safe
    bucket["last_at"] = now_iso
    bucket["events"].append({"ts": now_iso, "value_ms": safe})


def _record_size(bucket: Dict[str, Any], original_bytes: int, stored_bytes: int) -> None:
    original = max(0, int(original_bytes or 0))
    stored = max(0, int(stored_bytes or 0))
    saved = max(0, original - stored)
    now_iso = _utc_now_iso()
    bucket["count"] = int(bucket["count"]) + 1
    bucket["original_bytes_total"] = int(bucket["original_bytes_total"]) + original
    bucket["stored_bytes_total"] = int(bucket["stored_bytes_total"]) + stored
    bucket["bytes_saved_total"] = int(bucket["bytes_saved_total"]) + saved
    bucket["avg_original_bytes"] = int(bucket["original_bytes_total"] / max(1, int(bucket["count"])))
    bucket["avg_stored_bytes"] = int(bucket["stored_bytes_total"] / max(1, int(bucket["count"])))
    bucket["avg_bytes_saved"] = int(bucket["bytes_saved_total"] / max(1, int(bucket["count"])))
    bucket["max_original_bytes"] = max(int(bucket["max_original_bytes"]), original)
    bucket["max_stored_bytes"] = max(int(bucket["max_stored_bytes"]), stored)
    bucket["last_original_bytes"] = original
    bucket["last_stored_bytes"] = stored
    bucket["last_at"] = now_iso
    bucket["events"].append(
        {
            "ts": now_iso,
            "original_bytes": original,
            "stored_bytes": stored,
            "bytes_saved": saved,
        }
    )


def _serialize_duration_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: value for key, value in bucket.items() if key != "events"}
    now = _utc_now_dt()
    events = list(bucket.get("events") or [])
    for label, window_sec in _WINDOWS.items():
        filtered = []
        for event in events:
            ts = _parse_iso(event.get("ts"))
            if ts and (now - ts).total_seconds() <= window_sec:
                filtered.append(int(event.get("value_ms") or 0))
        out[label] = {
            "count": len(filtered),
            "avg_ms": int(sum(filtered) / len(filtered)) if filtered else 0,
            "max_ms": max(filtered) if filtered else 0,
        }
    return out


def _serialize_size_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: value for key, value in bucket.items() if key != "events"}
    now = _utc_now_dt()
    events = list(bucket.get("events") or [])
    for label, window_sec in _WINDOWS.items():
        filtered = []
        for event in events:
            ts = _parse_iso(event.get("ts"))
            if ts and (now - ts).total_seconds() <= window_sec:
                filtered.append(event)
        out[label] = {
            "count": len(filtered),
            "avg_original_bytes": int(sum(int(item.get("original_bytes") or 0) for item in filtered) / len(filtered)) if filtered else 0,
            "avg_stored_bytes": int(sum(int(item.get("stored_bytes") or 0) for item in filtered) / len(filtered)) if filtered else 0,
            "avg_bytes_saved": int(sum(int(item.get("bytes_saved") or 0) for item in filtered) / len(filtered)) if filtered else 0,
            "max_original_bytes": max((int(item.get("original_bytes") or 0) for item in filtered), default=0),
            "max_stored_bytes": max((int(item.get("stored_bytes") or 0) for item in filtered), default=0),
        }
    return out


def _serialize_export_file_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: value for key, value in bucket.items() if key != "events"}
    now = _utc_now_dt()
    events = list(bucket.get("events") or [])
    for label, window_sec in _WINDOWS.items():
        filtered = []
        for event in events:
            ts = _parse_iso(event.get("ts"))
            if ts and (now - ts).total_seconds() <= window_sec:
                filtered.append(int(event.get("bytes") or 0))
        out[label] = {
            "count": len(filtered),
            "avg_bytes": int(sum(filtered) / len(filtered)) if filtered else 0,
            "max_bytes": max(filtered) if filtered else 0,
        }
    return out


def record_task_started(created_at: Any, started_at: Any) -> None:
    created = _parse_iso(created_at)
    started = _parse_iso(started_at)
    if not created or not started:
        return
    with _lock:
        _record_duration(_stats["tasks"]["queue_wait_ms"], int((started - created).total_seconds() * 1000))


def record_task_completed(created_at: Any, started_at: Any, completed_at: Any) -> None:
    created = _parse_iso(created_at)
    started = _parse_iso(started_at)
    completed = _parse_iso(completed_at)
    if not completed:
        return
    with _lock:
        if started:
            _record_duration(_stats["tasks"]["run_duration_ms"], int((completed - started).total_seconds() * 1000))
        if created:
            _record_duration(_stats["tasks"]["end_to_end_ms"], int((completed - created).total_seconds() * 1000))


def record_task_result_size(original_bytes: int, stored_bytes: int) -> None:
    with _lock:
        _record_size(_stats["tasks"]["result_payload_bytes"], original_bytes, stored_bytes)


def record_llm_job_started(created_at: Any, started_at: Any) -> None:
    created = _parse_iso(created_at)
    started = _parse_iso(started_at)
    if not created or not started:
        return
    with _lock:
        _record_duration(_stats["llm_jobs"]["queue_wait_ms"], int((started - created).total_seconds() * 1000))


def record_llm_job_completed(created_at: Any, started_at: Any, duration_ms: int) -> None:
    created = _parse_iso(created_at)
    started = _parse_iso(started_at)
    safe_duration = max(0, int(duration_ms or 0))
    with _lock:
        _record_duration(_stats["llm_jobs"]["run_duration_ms"], safe_duration)
        if created and started:
            _record_duration(
                _stats["llm_jobs"]["end_to_end_ms"],
                int((started - created).total_seconds() * 1000) + safe_duration,
            )


def record_llm_result_size(original_bytes: int, stored_bytes: int) -> None:
    with _lock:
        _record_size(_stats["llm_jobs"]["result_payload_bytes"], original_bytes, stored_bytes)


def record_export_generation(duration_ms: int, file_size_bytes: int, export_format: str) -> None:
    safe_size = max(0, int(file_size_bytes or 0))
    now_iso = _utc_now_iso()
    with _lock:
        _record_duration(_stats["exports"]["generation_ms"], duration_ms)
        bucket = _stats["exports"]["file_size_bytes"]
        bucket["count"] = int(bucket["count"]) + 1
        bucket["total_bytes"] = int(bucket["total_bytes"]) + safe_size
        bucket["avg_bytes"] = int(bucket["total_bytes"] / max(1, int(bucket["count"])))
        bucket["max_bytes"] = max(int(bucket["max_bytes"]), safe_size)
        bucket["last_bytes"] = safe_size
        bucket["last_format"] = str(export_format or "")
        bucket["last_at"] = now_iso
        bucket["events"].append({"ts": now_iso, "bytes": safe_size, "format": str(export_format or "")})


def get_ops_observability_stats() -> Dict[str, Any]:
    with _lock:
        return {
            "tasks": {
                "queue_wait_ms": _serialize_duration_bucket(_stats["tasks"]["queue_wait_ms"]),
                "run_duration_ms": _serialize_duration_bucket(_stats["tasks"]["run_duration_ms"]),
                "end_to_end_ms": _serialize_duration_bucket(_stats["tasks"]["end_to_end_ms"]),
                "result_payload_bytes": _serialize_size_bucket(_stats["tasks"]["result_payload_bytes"]),
            },
            "llm_jobs": {
                "queue_wait_ms": _serialize_duration_bucket(_stats["llm_jobs"]["queue_wait_ms"]),
                "run_duration_ms": _serialize_duration_bucket(_stats["llm_jobs"]["run_duration_ms"]),
                "end_to_end_ms": _serialize_duration_bucket(_stats["llm_jobs"]["end_to_end_ms"]),
                "result_payload_bytes": _serialize_size_bucket(_stats["llm_jobs"]["result_payload_bytes"]),
            },
            "exports": {
                "generation_ms": _serialize_duration_bucket(_stats["exports"]["generation_ms"]),
                "file_size_bytes": _serialize_export_file_bucket(_stats["exports"]["file_size_bytes"]),
            },
        }
