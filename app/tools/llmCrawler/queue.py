"""Redis queue and job storage for LLM Crawler."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import gzip
import base64

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    redis = None

from app.config import settings


_redis_client: Optional[Any] = None
_redis_retry_after_ts: float = 0.0
_mem_jobs: Dict[str, Dict[str, Any]] = {}
_mem_queue: list[Dict[str, Any]] = []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_redis_client() -> Optional[Any]:
    global _redis_client, _redis_retry_after_ts
    if redis is None:
        return None
    now_ts = time.time()
    if _redis_client is not None:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception:
            _redis_client = None
            _redis_retry_after_ts = now_ts + 5.0
            return None
    if now_ts < _redis_retry_after_ts:
        return None
    try:
        _redis_client = redis.from_url(settings.LLM_CRAWLER_REDIS_URL, decode_responses=True)
        _redis_client.ping()
        _redis_retry_after_ts = 0.0
    except Exception:
        _redis_client = None
        _redis_retry_after_ts = now_ts + 5.0
    return _redis_client


def _llm_prefix() -> str:
    return str(getattr(settings, "LLM_CRAWLER_REDIS_PREFIX", "llmCrawler") or "llmCrawler").strip(": ")


def queue_key() -> str:
    explicit = str(getattr(settings, "LLM_CRAWLER_QUEUE_KEY", "") or "").strip()
    if explicit:
        return explicit
    return f"{_llm_prefix()}:queue"


def job_key(job_id: str) -> str:
    return f"{_llm_prefix()}:job:{job_id}"


def _job_ttl() -> int:
    return max(3600, int(getattr(settings, "LLM_CRAWLER_JOB_TTL_SECONDS", getattr(settings, "LLM_CRAWLER_JOB_TTL_SEC", 72 * 3600)) or (72 * 3600)))


def _max_job_bytes() -> int:
    return int(getattr(settings, "LLM_CRAWLER_MAX_JOB_BYTES", 1_500_000) or 1_500_000)


def _compress_enabled() -> bool:
    return bool(getattr(settings, "LLM_CRAWLER_COMPRESS_RESULTS", True))


def _maybe_compress_result(job: Dict[str, Any]) -> Dict[str, Any]:
    if not _compress_enabled():
        return job
    result = job.get("result")
    if not result:
        return job
    try:
        payload = json.dumps(result).encode("utf-8")
        if len(payload) < 50_000:
            return job
        gz = gzip.compress(payload, compresslevel=5)
        job = dict(job)
        job["result"] = {
            "__compressed": True,
            "encoding": "gzip+base64",
            "data": base64.b64encode(gz).decode("ascii"),
        }
    except Exception:
        return job
    return job


def _maybe_decompress_result(job: Dict[str, Any]) -> Dict[str, Any]:
    result = job.get("result")
    if not isinstance(result, dict):
        return job
    if not result.get("__compressed"):
        return job
    try:
        data_b64 = result.get("data") or ""
        raw = gzip.decompress(base64.b64decode(data_b64))
        job = dict(job)
        job["result"] = json.loads(raw.decode("utf-8"))
    except Exception:
        return job
    return job


def _truncate_heavy_fields(job: Dict[str, Any]) -> Dict[str, Any]:
    # remove overly large text blobs to respect max size
    result = job.get("result")
    if not isinstance(result, dict):
        return job
    snapshots = []
    for key in ("nojs", "rendered"):
        snap = result.get(key)
        if isinstance(snap, dict):
            snapshots.append(snap)
    for snap in snapshots:
        content = snap.get("content") or {}
        for field in ("readability_text", "trafilatura_text", "main_text_preview"):
            if field in content and isinstance(content[field], str) and len(content[field]) > 200_000:
                content[field] = content[field][:200_000]
        if not bool((result.get("options") or {}).get("include_raw_html", False)):
            snap.pop("raw_html", None)
        if not bool((result.get("options") or {}).get("include_rendered_html", False)):
            snap.pop("rendered_html", None)
    for key, limit in (
        ("content_segments", 20),
        ("segment_tree", 30),
        ("main_content_nodes", 40),
        ("noise_nodes", 60),
        ("chunk_ranking_debug", 20),
    ):
        items = result.get(key)
        if isinstance(items, list) and len(items) > limit:
            result[key] = items[:limit]
            result.setdefault("storage_meta", {})[f"{key}_omitted"] = len(items) - limit
    segmentation = result.get("segmentation")
    if isinstance(segmentation, dict):
        for key, limit in (
            ("content_segments", 20),
            ("segment_tree", 30),
            ("main_content_nodes", 40),
            ("noise_nodes", 60),
        ):
            items = segmentation.get(key)
            if isinstance(items, list) and len(items) > limit:
                segmentation[key] = items[:limit]
                result.setdefault("storage_meta", {})[f"segmentation_{key}_omitted"] = len(items) - limit
    recommendation_diagnostics = result.get("recommendation_diagnostics")
    if isinstance(recommendation_diagnostics, dict):
        for key, limit in (("strengths", 10), ("warnings", 10), ("actions", 10)):
            items = recommendation_diagnostics.get(key)
            if isinstance(items, list) and len(items) > limit:
                recommendation_diagnostics[key] = items[:limit]
                result.setdefault("storage_meta", {})[f"recommendation_diagnostics_{key}_omitted"] = len(items) - limit
    job["result"] = result
    return job


def new_job_id() -> str:
    return f"llmcrawler-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def create_job_record(
    *,
    job_id: str,
    request_id: str,
    requested_url: str,
    options: Dict[str, Any],
    status_message: str = "Queued",
    subject: str = "",
) -> Dict[str, Any]:
    return {
        "jobId": job_id,
        "requestId": request_id,
        "status": "queued",
        "progress": 0,
        "status_message": status_message,
        "requested_url": requested_url,
        "options": options,
        "subject": subject,
        "result": None,
        "error": None,
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
    }


def save_job_record(job: Dict[str, Any]) -> None:
    client = get_redis_client()
    payload = dict(job)
    payload["updatedAt"] = _utc_now()
    payload = _truncate_heavy_fields(payload)
    payload = _maybe_compress_result(payload)
    try:
        raw = json.dumps(payload)
        if len(raw.encode("utf-8")) > _max_job_bytes():
            # last resort: drop result completely
            payload = dict(payload)
            payload["result"] = None
            raw = json.dumps(payload)
    except Exception:
        raw = json.dumps(payload)
    if not client:
        _mem_jobs[str(job.get("jobId") or "")] = payload
        return
    client.setex(job_key(str(job.get("jobId") or "")), _job_ttl(), raw)


def get_job_record(job_id: str) -> Optional[Dict[str, Any]]:
    client = get_redis_client()
    if not client:
        return _mem_jobs.get(job_id)
    try:
        raw = client.get(job_key(job_id))
        if not raw:
            return _mem_jobs.get(job_id)
        job = json.loads(raw)
        job = _maybe_decompress_result(job)
        return job
    except Exception:
        return _mem_jobs.get(job_id)


def update_job_record(job_id: str, **fields: Any) -> Dict[str, Any]:
    current = get_job_record(job_id) or {
        "jobId": job_id,
        "status": "queued",
        "progress": 0,
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
    }
    current.update(fields)
    if "progress" in current:
        try:
            current["progress"] = max(0, min(100, int(current["progress"])))
        except Exception:
            current["progress"] = 0
    save_job_record(current)
    return current


def enqueue_job(job: Dict[str, Any]) -> str:
    client = get_redis_client()
    save_job_record(job)
    if not client:
        _mem_queue.append({"jobId": job["jobId"], "requestId": job.get("requestId", "")})
        return str(job["jobId"])
    client.lpush(queue_key(), json.dumps({"jobId": job["jobId"], "requestId": job.get("requestId", "")}))
    return str(job["jobId"])


def pop_job(timeout_sec: int = 5) -> Optional[Dict[str, Any]]:
    client = get_redis_client()
    if not client:
        return _mem_queue.pop(0) if _mem_queue else None
    try:
        item = client.brpop(queue_key(), timeout=max(1, int(timeout_sec)))
        if not item:
            return None
        return json.loads(item[1])
    except Exception:
        return None


def cleanup_expired_jobs() -> None:
    client = get_redis_client()
    if not client:
        return
    try:
        cursor = 0
        pattern = job_key("*")
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=200)
            for k in keys:
                ttl = client.ttl(k)
                if ttl is not None and ttl <= 0:
                    client.delete(k)
            if cursor == 0:
                break
    except Exception:
        return


def queue_depth() -> int:
    client = get_redis_client()
    if not client:
        return len(_mem_queue)
    try:
        return int(client.llen(queue_key()) or 0)
    except Exception:
        return len(_mem_queue)


def inc_subject(subject: str) -> int:
    client = get_redis_client()
    if not client:
        return 0
    key = f"{_llm_prefix()}:concurrent:{subject}"
    try:
        val = client.incr(key)
        client.expire(key, 600)
        return int(val)
    except Exception:
        return 0


def dec_subject(subject: str) -> None:
    client = get_redis_client()
    if not client:
        return
    key = f"{_llm_prefix()}:concurrent:{subject}"
    try:
        client.decr(key)
        client.expire(key, 600)
    except Exception:
        return


def check_rate_limit(subject: str, bucket: str, limit: int, window_sec: int) -> Dict[str, Any]:
    client = get_redis_client()
    if not client:
        return {"allowed": True, "remaining": limit, "reset_in": window_sec}
    safe_limit = max(1, int(limit))
    safe_window = max(1, int(window_sec))
    key = f"{_llm_prefix()}:rate:{bucket}:{subject}"
    try:
        current = int(client.incr(key))
        if current == 1:
            client.expire(key, safe_window)
        ttl = int(client.ttl(key) or safe_window)
        return {
            "allowed": current <= safe_limit,
            "remaining": max(0, safe_limit - current),
            "reset_in": max(0, ttl),
        }
    except Exception:
        return {"allowed": True, "remaining": safe_limit, "reset_in": safe_window}


def set_worker_heartbeat(extra: Optional[Dict[str, Any]] = None) -> None:
    client = get_redis_client()
    if not client:
        return
    payload: Dict[str, Any] = {"updatedAt": _utc_now()}
    if extra:
        payload.update(extra)
    ttl = max(30, int(getattr(settings, "LLM_CRAWLER_WORKER_HEARTBEAT_TTL_SEC", 120) or 120))
    try:
        explicit = str(getattr(settings, "LLM_CRAWLER_WORKER_HEARTBEAT_KEY", "") or "").strip()
        key = explicit or f"{_llm_prefix()}:worker:heartbeat"
        client.setex(key, ttl, json.dumps(payload))
    except Exception:
        return


def get_worker_heartbeat() -> Optional[Dict[str, Any]]:
    client = get_redis_client()
    if not client:
        return None
    explicit = str(getattr(settings, "LLM_CRAWLER_WORKER_HEARTBEAT_KEY", "") or "").strip()
    key = explicit or f"{_llm_prefix()}:worker:heartbeat"
    try:
        raw = client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None
