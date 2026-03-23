"""
Task management API endpoints.

Covers: GET/DELETE /tasks/{task_id}, artifact serving,
stale-artifact cleanup, rate-limit info, and Celery status.
"""
from urllib.parse import urlsplit, urlunsplit
from typing import Optional

from fastapi import APIRouter, Request

from app.api.routers._task_store import (
    _utc_now_iso,
    delete_task_result,
    get_task_result,
    get_task_store_compaction_stats,
    get_task_store_memory_stats,
    cleanup_task_results_memory,
    get_redis_client as get_task_store_redis,
)
from app.config import settings
from app.core.ops_gate import require_ops_access
from app.core.memory_guard import get_process_memory_snapshot, get_memory_guard_status
from app.core.ops_observability import get_ops_observability_stats
from app.core.progress import progress_tracker
from app.tools.llmCrawler.queue import get_compaction_stats as get_llm_crawler_compaction_stats
from app.tools.llmCrawler.queue import get_worker_heartbeat, queue_depth
from app.tools.site_pro.service import get_site_pro_compaction_stats

router = APIRouter(tags=["Tasks"])


def _mask_redis_url(url: Optional[str]) -> Optional[str]:
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        db = parsed.path or ""
        user = parsed.username or ""
        auth = f"{user}:***@" if user else ""
        return urlunsplit((parsed.scheme, f"{auth}{host}{port}", db, "", ""))
    except Exception:
        return "***"


# ─── task CRUD ─────────────────────────────────────────────────────────────


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Get task result."""
    print(f"[API] Getting status for: {task_id}")

    data = get_task_result(task_id)
    if data:
        return {
            "task_id": task_id,
            "status": data.get("status", "SUCCESS"),
            "progress": data.get("progress", 100),
            "status_message": data.get("status_message", ""),
            "progress_meta": data.get("progress_meta", {}),
            "task_type": data.get("task_type"),
            "url": data.get("url", ""),
            "created_at": data.get("created_at"),
            "started_at": data.get("started_at"),
            "updated_at": data.get("updated_at"),
            "completed_at": data.get("completed_at"),
            "result": data.get("result"),
            "error": data.get("error"),
            "can_continue": False,
        }

    return {
        "task_id": task_id,
        "status": "PENDING",
        "progress": 0,
        "progress_meta": {},
        "status_message": "Задача пока не найдена",
        "task_type": "site_analyze",
        "url": "",
        "created_at": _utc_now_iso(),
        "started_at": None,
        "updated_at": None,
        "completed_at": None,
        "result": None,
        "error": "Задача не найдена",
        "can_continue": False,
    }


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete task result and linked artifact files."""
    from app.core.task_cleanup import delete_task_artifacts

    task = get_task_result(task_id)
    if not task:
        return {"task_id": task_id, "deleted": False, "error": "Задача не найдена"}

    cleanup = delete_task_artifacts(task)
    removed = delete_task_result(task_id)
    return {
        "task_id": task_id,
        "deleted": bool(removed),
        "artifacts_cleanup": cleanup,
    }


@router.post("/tasks/cleanup-stale-artifacts")
async def cleanup_stale_artifacts(request: Request, days: Optional[int] = None):
    """Trigger stale report artifacts cleanup under REPORTS_DIR."""
    require_ops_access(request)
    from app.core.task_cleanup import prune_stale_report_artifacts

    summary = prune_stale_report_artifacts(max_age_days=days)
    return {"status": "SUCCESS", "cleanup": summary}


# ─── artifact serving ──────────────────────────────────────────────────────


@router.get("/mobile-artifacts/{task_id}/{filename}")
async def get_mobile_artifact(task_id: str, filename: str):
    """Serve mobile screenshot artifact for UI gallery."""
    from pathlib import Path
    from fastapi.responses import FileResponse

    try:
        task = get_task_result(task_id)
        if not task:
            return {"error": "Задача не найдена", "task_id": task_id}
        results = (task.get("result", {}) or {}).get("results", task.get("result", {})) or {}
        for item in results.get("device_results", []) or []:
            if item.get("screenshot_name") == filename:
                shot_path = item.get("screenshot_path")
                if shot_path and Path(shot_path).exists():
                    return FileResponse(shot_path)
        return {"error": "Артефакт не найден"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/render-artifacts/{task_id}/{filename}")
async def get_render_artifact(task_id: str, filename: str):
    """Serve render audit screenshot artifact for UI gallery."""
    from pathlib import Path
    from fastapi.responses import FileResponse

    try:
        task = get_task_result(task_id)
        if not task:
            return {"error": "Задача не найдена", "task_id": task_id}
        results = (task.get("result", {}) or {}).get("results", task.get("result", {})) or {}
        for variant in results.get("variants", []) or []:
            for shot in (variant.get("screenshots", {}) or {}).values():
                if isinstance(shot, dict) and shot.get("name") == filename:
                    shot_path = shot.get("path")
                    if shot_path and Path(shot_path).exists():
                        return FileResponse(shot_path)
        return {"error": "Артефакт не найден"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/site-pro-artifacts/{task_id}/manifest")
async def get_site_pro_artifact_manifest(task_id: str):
    """Return Site Audit Pro chunk manifest and compact payload meta."""
    try:
        task = get_task_result(task_id)
        if not task:
            return {"error": "Задача не найдена", "task_id": task_id}
        results = (task.get("result", {}) or {}).get("results", task.get("result", {})) or {}
        artifacts = results.get("artifacts", {}) or {}
        return {
            "task_id": task_id,
            "payload_compacted": bool(artifacts.get("payload_compacted", False)),
            "inline_limits": artifacts.get("inline_limits", {}),
            "omitted_counts": artifacts.get("omitted_counts", {}),
            "chunk_manifest": artifacts.get("chunk_manifest", {}),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/site-pro-artifacts/{task_id}/{filename}")
async def get_site_pro_artifact(task_id: str, filename: str):
    """Serve Site Audit Pro chunk artifact files."""
    from pathlib import Path
    from fastapi.responses import FileResponse
    from app.config import settings

    try:
        task = get_task_result(task_id)
        if not task:
            return {"error": "Задача не найдена", "task_id": task_id}
        results = (task.get("result", {}) or {}).get("results", task.get("result", {})) or {}
        artifacts = results.get("artifacts", {}) or {}
        chunks = (artifacts.get("chunk_manifest", {}) or {}).get("chunks", []) or []
        for chunk in chunks:
            for file_meta in (chunk.get("files") or []):
                if file_meta.get("filename") != filename:
                    continue
                file_path = file_meta.get("path")
                if not file_path:
                    continue
                p = Path(file_path)
                if not p.exists():
                    continue
                reports_root = Path(settings.REPORTS_DIR).resolve()
                resolved = p.resolve()
                if reports_root not in resolved.parents and resolved != reports_root:
                    continue
                return FileResponse(str(resolved), media_type="application/x-ndjson", filename=filename)
        return {"error": "Артефакт не найден"}
    except Exception as e:
        return {"error": str(e)}


# ─── utility ───────────────────────────────────────────────────────────────


@router.get("/rate-limit")
async def get_rate_limit():
    """Rate limit info (placeholder — real limits enforced by middleware)."""
    from app.config import settings

    return {
        "allowed": True,
        "remaining": settings.RATE_LIMIT_PER_HOUR,
        "reset_in": settings.RATE_LIMIT_WINDOW,
        "limit": settings.RATE_LIMIT_PER_HOUR,
    }


@router.get("/celery-status")
async def celery_status():
    """Celery worker status check."""
    try:
        from app.core.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=2)
        active = inspect.active()
        return {
            "status": "online" if active else "offline",
            "workers": list(active.keys()) if active else [],
        }
    except Exception as e:
        return {"status": "offline", "error": str(e)}


@router.get("/memory/status")
async def task_memory_status(request: Request):
    """Inspect in-process memory usage and fallback stores."""
    require_ops_access(request)

    return {
        "status": "SUCCESS",
        "process_memory": get_process_memory_snapshot(),
        "memory_guard": get_memory_guard_status(),
        "task_store": get_task_store_memory_stats(),
        "progress_store": progress_tracker.get_memory_stats(),
    }


@router.get("/ops/status")
async def ops_status(request: Request):
    """Operational status summary for Redis, workers, queues, and fallback stores."""
    require_ops_access(request)
    redis_ok = False
    redis_error = None
    try:
        redis_ok = bool(get_task_store_redis())
    except Exception as exc:
        redis_error = str(exc)

    heartbeat = None
    heartbeat_age_sec = None
    worker_healthy = False
    worker_error = None
    queue_size = None

    try:
        heartbeat = get_worker_heartbeat()
        queue_size = queue_depth()
        if heartbeat and heartbeat.get("updatedAt"):
            from datetime import datetime, timezone

            ts = datetime.fromisoformat(str(heartbeat["updatedAt"]).replace("Z", "+00:00"))
            heartbeat_age_sec = int((datetime.now(timezone.utc) - ts).total_seconds())
            worker_healthy = heartbeat_age_sec <= max(
                60, int(getattr(settings, "LLM_CRAWLER_WORKER_HEARTBEAT_TTL_SEC", 120) or 120) * 2
            )
    except Exception as exc:
        worker_error = str(exc)

    return {
        "status": "healthy" if redis_ok else "degraded",
        "redis": {
            "ok": redis_ok,
            "error": redis_error,
            "urls": {
                "task_store": _mask_redis_url(settings.TASK_STORE_REDIS_URL),
                "progress": _mask_redis_url(settings.PROGRESS_REDIS_URL),
                "rate_limit": _mask_redis_url(settings.RATE_LIMIT_REDIS_URL),
                "llm_crawler": _mask_redis_url(settings.LLM_CRAWLER_REDIS_URL),
                "celery_broker": _mask_redis_url(settings.CELERY_BROKER_URL),
                "celery_backend": _mask_redis_url(settings.CELERY_RESULT_BACKEND),
            },
            "prefixes": {
                "task_store": settings.TASK_STORE_REDIS_PREFIX,
                "progress": settings.PROGRESS_REDIS_PREFIX,
                "rate_limit": settings.RATE_LIMIT_REDIS_PREFIX,
                "llm_crawler": settings.LLM_CRAWLER_REDIS_PREFIX,
            },
        },
        "llm_worker": {
            "healthy": worker_healthy,
            "heartbeat": heartbeat,
            "heartbeat_age_sec": heartbeat_age_sec,
            "queue_depth": queue_size,
            "error": worker_error,
        },
        "memory": {
            "process": get_process_memory_snapshot(),
            "guard": get_memory_guard_status(),
            "task_store": get_task_store_memory_stats(),
            "progress_store": progress_tracker.get_memory_stats(),
        },
        "payload_compaction": {
            "task_store": get_task_store_compaction_stats(),
            "progress": progress_tracker.get_compaction_stats(),
            "llm_crawler": get_llm_crawler_compaction_stats(),
            "site_audit_pro": get_site_pro_compaction_stats(),
        },
        "observability": get_ops_observability_stats(),
    }


@router.post("/memory/cleanup")
async def cleanup_memory(request: Request, aggressive: bool = True):
    """Force cleanup of in-memory fallback stores."""
    require_ops_access(request)
    from app.core.progress import progress_tracker
    from app.core.memory_guard import run_memory_cleanup_now

    idle_seconds = 999999 if aggressive else 0
    task_cleanup = cleanup_task_results_memory(idle_seconds=idle_seconds, aggressive=aggressive)
    progress_cleanup = progress_tracker.cleanup_memory(idle_seconds=idle_seconds, aggressive=aggressive)
    guard_cleanup = run_memory_cleanup_now(force_gc=aggressive)
    return {
        "status": "SUCCESS",
        "aggressive": aggressive,
        "task_store": task_cleanup,
        "progress_store": progress_cleanup,
        "guard_cleanup": guard_cleanup,
    }


@router.post("/maintenance/run")
async def run_maintenance_now(request: Request, days: Optional[int] = None, force_gc: bool = True):
    """Run consolidated maintenance routine without relying on script imports."""
    require_ops_access(request)
    from app.core.memory_guard import run_memory_cleanup_now
    from app.core.task_cleanup import prune_stale_report_artifacts
    from app.tools.llmCrawler.queue import cleanup_expired_jobs

    stale_days = max(1, int(days or 7))
    artifacts = prune_stale_report_artifacts(max_age_days=stale_days)
    cleanup_expired_jobs()
    memory = run_memory_cleanup_now(force_gc=bool(force_gc))
    heartbeat = get_worker_heartbeat()
    queue_size = queue_depth()
    return {
        "status": "SUCCESS",
        "artifacts": artifacts,
        "memory": memory,
        "llm_queue_depth": queue_size,
        "llm_worker_heartbeat": heartbeat,
    }
