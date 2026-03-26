"""Unified Full SEO Audit router."""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Request
from app.validators import URLModel
from app.api.routers._task_store import create_task_pending, update_task_state, create_task_result
from app.core.scan_token import capture_scan_token_from_request, scan_token_context

router = APIRouter(tags=["Unified Audit"])


class UnifiedAuditRequest(URLModel):
    url: str
    use_proxy: bool = False
    skip_tools: List[str] = []


@router.post("/tasks/unified-audit")
async def create_unified_audit(data: UnifiedAuditRequest, background_tasks: BackgroundTasks, request: Request):
    url = (data.url or "").strip()
    skip = [s.strip().lower() for s in (data.skip_tools or []) if s.strip()]
    task_id = f"unified-{datetime.now().timestamp()}"
    create_task_pending(task_id, "unified_audit", url, status_message="Unified SEO Audit queued")
    scan_token = capture_scan_token_from_request(request)

    def _run():
        with scan_token_context(scan_token):
            try:
                update_task_state(task_id, status="RUNNING", progress=5, status_message="Starting unified audit...")

                def _progress_cb(**kwargs):
                    update_task_state(task_id, status="RUNNING", **kwargs)

                from app.tools.unified import run_unified_audit
                result = run_unified_audit(url=url, use_proxy=data.use_proxy,
                                           skip_tools=skip, progress_callback=_progress_cb)

                create_task_result(task_id, "unified_audit", url, result)
            except Exception as e:
                update_task_state(task_id, status="FAILURE", progress=100,
                                status_message=f"Error: {str(e)[:200]}")

    background_tasks.add_task(_run)
    return {"task_id": task_id, "url": url}
