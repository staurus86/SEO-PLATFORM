"""
Site Audit Pro router.
"""
import json
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import Field, field_validator

from app.validators import URLModel
from app.api.routers._task_store import (
    create_task_pending,
    update_task_state,
    append_task_artifact,
)
from app.core.scan_token import capture_scan_token_from_request, scan_token_context

router = APIRouter(tags=["SEO Tools"])


def check_site_audit_pro(
    url: str,
    task_id: str,
    mode: str = "quick",
    max_pages: int = 5,
    batch_mode: bool = False,
    batch_urls: Optional[List[str]] = None,
    extended_hreflang_checks: bool = False,
    progress_callback=None,
    use_proxy: bool = False,
) -> Dict[str, Any]:
    """Feature-flagged Site Audit Pro entrypoint."""
    from app.tools.site_pro.service import SiteAuditProService

    service = SiteAuditProService()
    return service.run(
        url=url,
        task_id=task_id,
        mode=mode,
        max_pages=max_pages,
        batch_mode=batch_mode,
        batch_urls=batch_urls or [],
        extended_hreflang_checks=extended_hreflang_checks,
        progress_callback=progress_callback,
        use_proxy=use_proxy,
    )


class SiteAuditProRequest(URLModel):
    url: Optional[str] = None
    mode: Optional[str] = "quick"
    max_pages: int = Field(default=100, ge=1, le=1500)
    batch_mode: bool = False
    batch_urls: Optional[List[str]] = Field(default=None, max_length=1500)
    extended_hreflang_checks: bool = False
    use_proxy: bool = False

    @field_validator("batch_urls", mode="before")
    @classmethod
    def _normalize_batch_urls(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []


@router.post("/tasks/site-audit-pro")
async def create_site_audit_pro(data: SiteAuditProRequest, background_tasks: BackgroundTasks, request: Request):
    """Site Audit Pro queued as isolated background task."""
    from app.config import settings
    if not getattr(settings, "SITE_AUDIT_PRO_ENABLED", True):
        return {"error": "Site Audit Pro is disabled by feature flag"}

    raw_url = (data.url or "").strip()
    default_mode = (getattr(settings, "SITE_AUDIT_PRO_DEFAULT_MODE", "quick") or "quick").lower()
    mode = (data.mode or default_mode).lower()
    if mode not in ("quick", "full"):
        mode = "quick"
    batch_mode = bool(getattr(data, "batch_mode", False))
    extended_hreflang_checks = bool(getattr(data, "extended_hreflang_checks", False))
    if batch_mode:
        mode = "full"
    base_limit = int(getattr(settings, "SITE_AUDIT_PRO_MAX_PAGES_LIMIT", 1500) or 1500)
    quick_limit = int(getattr(settings, "SITE_AUDIT_PRO_MAX_PAGES_LIMIT_QUICK", min(base_limit, 200)) or min(base_limit, 200))
    full_limit = int(getattr(settings, "SITE_AUDIT_PRO_MAX_PAGES_LIMIT_FULL", max(base_limit, 1500)) or max(base_limit, 1500))
    mode_limit = full_limit if mode == "full" else quick_limit
    effective_max_pages_limit = 1500 if batch_mode else mode_limit
    mode_default_pages = 100 if mode == "full" else 50
    max_pages = max(1, min(int(data.max_pages or mode_default_pages), effective_max_pages_limit))

    raw_batch_urls = list(getattr(data, "batch_urls", []) or [])
    normalized_batch_urls: List[str] = []
    seen_batch_urls: set[str] = set()
    for item in raw_batch_urls:
        candidate = str(item or "").strip()
        if not candidate:
            continue
        if not candidate.startswith(("http://", "https://")):
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        if not parsed.scheme or not parsed.netloc:
            continue
        if candidate in seen_batch_urls:
            continue
        seen_batch_urls.add(candidate)
        normalized_batch_urls.append(candidate)
        if len(normalized_batch_urls) >= 1500:
            break

    if batch_mode and not normalized_batch_urls:
        raise HTTPException(status_code=422, detail="Batch mode requires at least one valid URL")

    if batch_mode:
        url = normalized_batch_urls[0]
    else:
        url = raw_url
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise HTTPException(status_code=422, detail="A valid site URL is required in crawl mode")

    print(
        f"[API] Site Audit Pro queued for: {url} "
        f"(mode={mode}, max_pages={max_pages}, batch_mode={batch_mode}, batch_urls={len(normalized_batch_urls)}, "
        f"extended_hreflang_checks={extended_hreflang_checks})"
    )
    task_id = f"sitepro-{datetime.now().timestamp()}"
    create_task_pending(task_id, "site_audit_pro", url, status_message="Site Audit Pro queued")
    scan_token = capture_scan_token_from_request(request)

    def _run_site_audit_pro_task() -> None:
        with scan_token_context(scan_token):
            t0 = time.perf_counter()
            print(
                "[SITE_PRO] "
                + json.dumps(
                    {
                        "event": "task_started",
                        "task_id": task_id,
                        "tool": "site_audit_pro",
                        "url": url,
                        "mode": mode,
                        "max_pages": max_pages,
                        "batch_mode": batch_mode,
                        "batch_urls_count": len(normalized_batch_urls),
                        "extended_hreflang_checks": extended_hreflang_checks,
                    },
                    ensure_ascii=False,
                )
            )
            try:
                update_task_state(
                    task_id,
                    status="RUNNING",
                    progress=5,
                    status_message="Preparing Site Audit Pro",
                    progress_meta={
                        "processed_pages": 0,
                        "total_pages": len(normalized_batch_urls) if batch_mode else max_pages,
                        "queue_size": len(normalized_batch_urls) if batch_mode else 1,
                        "batch_mode": batch_mode,
                        "current_url": url,
                    },
                )

                def _progress(progress: int, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
                    update_task_state(
                        task_id,
                        status="RUNNING",
                        progress=progress,
                        status_message=message,
                        progress_meta=meta or {},
                    )

                result = check_site_audit_pro(
                    url=url,
                    task_id=task_id,
                    mode=mode,
                    max_pages=max_pages,
                    batch_mode=batch_mode,
                    batch_urls=normalized_batch_urls,
                    extended_hreflang_checks=extended_hreflang_checks,
                    progress_callback=_progress,
                    use_proxy=bool(data.use_proxy),
                )
                chunk_manifest = (((result or {}).get("results") or {}).get("artifacts") or {}).get("chunk_manifest", {})
                for chunk in (chunk_manifest.get("chunks") or []):
                    for file_meta in (chunk.get("files") or []):
                        file_path = file_meta.get("path")
                        if file_path:
                            append_task_artifact(task_id, file_path, kind="site_pro_chunk")
                update_task_state(
                    task_id,
                    status="SUCCESS",
                    progress=100,
                    status_message="Site Audit Pro completed",
                    progress_meta={
                        "processed_pages": (((result or {}).get("results") or {}).get("summary") or {}).get("total_pages", 0),
                        "total_pages": (((result or {}).get("results") or {}).get("summary") or {}).get("total_pages", 0),
                        "queue_size": 0,
                        "batch_mode": batch_mode,
                        "current_url": "",
                    },
                    result=result,
                    error=None,
                )
                duration_ms = int((time.perf_counter() - t0) * 1000)
                summary = ((result or {}).get("results") or {}).get("summary", {})
                print(
                    "[SITE_PRO] "
                    + json.dumps(
                        {
                            "event": "task_completed",
                            "task_id": task_id,
                            "tool": "site_audit_pro",
                            "status": "SUCCESS",
                            "duration_ms": duration_ms,
                            "pages": summary.get("total_pages", 0),
                            "issues_total": summary.get("issues_total", 0),
                        },
                        ensure_ascii=False,
                    )
                )
            except Exception as exc:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="Site Audit Pro failed",
                    error=str(exc),
                )
                duration_ms = int((time.perf_counter() - t0) * 1000)
                print(
                    "[SITE_PRO] "
                    + json.dumps(
                        {
                            "event": "task_completed",
                            "task_id": task_id,
                            "tool": "site_audit_pro",
                            "status": "FAILURE",
                            "duration_ms": duration_ms,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                )

    background_tasks.add_task(_run_site_audit_pro_task)
    return {"task_id": task_id, "status": "PENDING", "message": "Site Audit Pro started"}
