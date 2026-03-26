"""
OnPage Audit router.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import field_validator

from app.validators import URLModel
from app.api.routers._task_store import create_task_pending, update_task_state
from app.core.scan_token import capture_scan_token_from_request, scan_token_context

router = APIRouter(tags=["SEO Tools"])


def check_onpage_audit(
    *,
    url: str,
    keywords: Optional[List[str]] = None,
    language: str = "auto",
    min_word_count: int = 250,
    keyword_density_warn_pct: float = 3.0,
    keyword_density_critical_pct: float = 5.0,
    title_min_len: int = 30,
    title_max_len: int = 60,
    description_min_len: int = 120,
    description_max_len: int = 160,
    h1_required: bool = True,
    h1_max_count: int = 1,
    use_proxy: bool = False,
) -> Dict[str, Any]:
    """Single-page on-page audit."""
    from app.tools.onpage import OnPageAuditServiceV1

    service = OnPageAuditServiceV1()
    return service.run(
        url=url,
        keywords=keywords or [],
        language=language,
        min_word_count=min_word_count,
        keyword_density_warn_pct=keyword_density_warn_pct,
        keyword_density_critical_pct=keyword_density_critical_pct,
        title_min_len=title_min_len,
        title_max_len=title_max_len,
        description_min_len=description_min_len,
        description_max_len=description_max_len,
        h1_required=h1_required,
        h1_max_count=h1_max_count,
        use_proxy=use_proxy,
    )


class OnPageAuditRequest(URLModel):
    url: str
    keywords: Optional[List[str]] = None
    language: Optional[str] = "auto"
    min_word_count: int = 250
    keyword_density_warn_pct: float = 3.0
    keyword_density_critical_pct: float = 5.0
    title_min_len: int = 30
    title_max_len: int = 60
    description_min_len: int = 120
    description_max_len: int = 160
    h1_required: bool = True
    h1_max_count: int = 1
    use_proxy: bool = False

    @field_validator("keywords", mode="before")
    @classmethod
    def _normalize_keywords(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.replace("\r", "\n")
            parts = []
            for chunk in raw.split("\n"):
                parts.extend([x.strip() for x in chunk.split(",") if x.strip()])
            return parts
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []


@router.post("/tasks/onpage-audit")
async def create_onpage_audit(data: OnPageAuditRequest, background_tasks: BackgroundTasks, request: Request):
    """OnPage audit queued as isolated background task."""
    url = (data.url or "").strip()
    task_id = f"onpage-{datetime.now().timestamp()}"
    create_task_pending(task_id, "onpage_audit", url, status_message="Задача поставлена в очередь")

    scan_token = capture_scan_token_from_request(request)

    def _run_onpage_task() -> None:
        with scan_token_context(scan_token):
            try:
                update_task_state(task_id, status="RUNNING", progress=10, status_message="Анализ on-page факторов")
                result = check_onpage_audit(
                    url=url,
                    keywords=data.keywords or [],
                    language=(data.language or "auto"),
                    min_word_count=int(data.min_word_count or 250),
                    keyword_density_warn_pct=float(data.keyword_density_warn_pct or 3.0),
                    keyword_density_critical_pct=float(data.keyword_density_critical_pct or 5.0),
                    title_min_len=int(data.title_min_len or 30),
                    title_max_len=int(data.title_max_len or 60),
                    description_min_len=int(data.description_min_len or 120),
                    description_max_len=int(data.description_max_len or 160),
                    h1_required=bool(data.h1_required),
                    h1_max_count=int(data.h1_max_count or 1),
                    use_proxy=bool(data.use_proxy),
                )
                update_task_state(
                    task_id,
                    status="SUCCESS",
                    progress=100,
                    status_message="OnPage аудит завершен",
                    result=result,
                    error=None,
                )
            except Exception as exc:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="OnPage аудит завершился с ошибкой",
                    error=str(exc),
                )

    background_tasks.add_task(_run_onpage_task)
    return {"task_id": task_id, "status": "PENDING", "message": "OnPage аудит запущен"}


# ── Competitor Comparison ─────────────────────────────────────────────────────


class CompetitorCompareRequest(URLModel):
    url: str
    competitor_urls: List[str] = []
    keywords: Optional[List[str]] = None

    @field_validator("competitor_urls", mode="before")
    @classmethod
    def _normalize_competitor_urls(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.replace("\r", "\n")
            return [u.strip() for u in raw.split("\n") if u.strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    @field_validator("keywords", mode="before")
    @classmethod
    def _normalize_comp_keywords(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.replace("\r", "\n")
            parts = []
            for chunk in raw.split("\n"):
                parts.extend([x.strip() for x in chunk.split(",") if x.strip()])
            return parts
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []


@router.post("/tasks/competitor-compare")
async def create_competitor_compare(data: CompetitorCompareRequest, background_tasks: BackgroundTasks, request: Request):
    """Compare on-page SEO metrics of a URL against competitors."""
    from app.tools.onpage.service_v1 import run_competitor_comparison

    url = (data.url or "").strip()
    if not url:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="URL обязателен")

    comp_urls = [u for u in (data.competitor_urls or []) if u]
    if not comp_urls:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Добавьте хотя бы один URL конкурента")

    task_id = f"comp-compare-{datetime.now().timestamp()}"
    create_task_pending(task_id, "competitor_comparison", url, status_message="Задача поставлена в очередь")
    scan_token = capture_scan_token_from_request(request)

    def _run_compare_task() -> None:
        with scan_token_context(scan_token):
            try:
                total = 1 + len(comp_urls[:5])
                update_task_state(task_id, status="RUNNING", progress=5, status_message="Анализ целевой страницы")
                result = run_competitor_comparison(
                    url=url,
                    competitor_urls=comp_urls,
                    keywords=data.keywords or [],
                )
                update_task_state(
                    task_id,
                    status="SUCCESS",
                    progress=100,
                    status_message=f"Сравнение завершено: {total} страниц",
                    result={"task_type": "competitor_comparison", "results": result},
                    error=None,
                )
            except Exception as exc:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="Сравнение завершилось с ошибкой",
                    error=str(exc),
                )

    background_tasks.add_task(_run_compare_task)
    return {"task_id": task_id, "status": "PENDING", "message": "Сравнение с конкурентами запущено"}
