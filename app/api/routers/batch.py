"""Batch mode for SEO tools — process multiple URLs."""
import time
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from app.api.routers._task_store import (
    create_task_pending,
    create_task_result,
    update_task_state,
)
from app.core.scan_token import capture_scan_token_from_request, scan_token_context

router = APIRouter(tags=["Batch"])


class BatchRequest(BaseModel):
    urls: List[str]
    tool: str  # onpage|render|mobile|redirect|robots|cwv|bot-check
    use_proxy: bool = False
    # Tool-specific options
    options: dict = {}


@router.post("/tasks/batch")
async def create_batch_task(data: BatchRequest, background_tasks: BackgroundTasks, request: Request):
    urls = [u.strip() for u in data.urls if u.strip()][:50]  # max 50 URLs
    if not urls:
        return {"error": "No valid URLs provided"}

    task_id = f"batch-{data.tool}-{datetime.now().timestamp()}"
    create_task_pending(
        task_id,
        f"batch_{data.tool}",
        urls[0],
        status_message=f"Batch {data.tool}: {len(urls)} URLs queued",
    )
    scan_token = capture_scan_token_from_request(request)

    def _run_batch():
        with scan_token_context(scan_token):
            results: List[Dict[str, Any]] = []
            total = len(urls)

            for i, url in enumerate(urls):
                pct = int((i / total) * 100)
                update_task_state(
                    task_id,
                    status="RUNNING",
                    progress=pct,
                    status_message=f"Processing {i + 1}/{total}: {url[:60]}",
                )

                try:
                    result = _run_single_tool(data.tool, url, data.use_proxy, data.options)
                    results.append({"url": url, "status": "success", "result": result})
                except Exception as e:
                    results.append({"url": url, "status": "error", "error": str(e)})

            success_count = sum(1 for r in results if r["status"] == "success")
            summary = {
                "task_type": f"batch_{data.tool}",
                "tool": data.tool,
                "total_urls": total,
                "success": success_count,
                "errors": total - success_count,
                "completed_at": datetime.utcnow().isoformat(),
            }

            create_task_result(
                task_id,
                f"batch_{data.tool}",
                urls[0],
                {"summary": summary, "items": results},
            )

    background_tasks.add_task(_run_batch)
    return {"task_id": task_id, "urls_count": len(urls), "tool": data.tool}


def _run_single_tool(
    tool: str, url: str, use_proxy: bool, options: dict
) -> Dict[str, Any]:
    """Run a single tool on a single URL."""
    if tool == "onpage":
        from app.tools.onpage.service_v1 import OnPageAuditServiceV1

        svc = OnPageAuditServiceV1()
        return svc.run(url=url, keywords=options.get("keywords", []))

    elif tool == "render":
        from app.tools.render.service_v2 import RenderAuditServiceV2

        svc = RenderAuditServiceV2(use_proxy=use_proxy)
        return svc.run(url=url, task_id=f"batch-render-{time.time()}")

    elif tool == "mobile":
        from app.tools.mobile.service_v2 import MobileCheckServiceV2

        svc = MobileCheckServiceV2(use_proxy=use_proxy)
        return svc.run(url=url, task_id=f"batch-mobile-{time.time()}", mode="quick")

    elif tool == "redirect":
        from app.tools.redirect_checker.service_v1 import run_redirect_checker

        return run_redirect_checker(url=url, use_proxy=use_proxy)

    elif tool == "robots":
        import asyncio

        from app.api.routers.robots import check_robots_full_async

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                check_robots_full_async(url, use_proxy=use_proxy)
            )
        finally:
            loop.close()

    elif tool == "cwv":
        from app.tools.core_web_vitals.service_v1 import run_core_web_vitals

        return run_core_web_vitals(url=url, use_proxy=use_proxy)

    elif tool == "bot-check":
        from app.tools.bots.service_v2 import BotAccessibilityServiceV2

        svc = BotAccessibilityServiceV2(use_proxy=use_proxy)
        return svc.run(url)

    else:
        raise ValueError(f"Unknown tool: {tool}")
