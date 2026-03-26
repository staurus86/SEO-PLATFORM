"""FastAPI routes for LLM Crawler Simulation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.config import settings

from .feature_gate import is_llm_crawler_enabled_for_request, request_subject
from .quality import run_quality_gate_from_file
from .queue import (
    check_rate_limit,
    create_job_record,
    dec_subject,
    enqueue_job,
    get_job_record,
    get_worker_heartbeat,
    new_job_id,
    queue_depth,
    update_job_record,
    inc_subject,
)
from .schemas import LlmCrawlerJobStatusResponse, LlmCrawlerRunRequest
from app.core.scan_token import capture_scan_token_from_request, scan_token_context
from fastapi.responses import HTMLResponse
from fastapi.responses import Response


router = APIRouter(prefix="/api/tools/llm-crawler", tags=["LLM Crawler Simulation"])


def _ensure_feature_enabled(request: Request) -> None:
    if not is_llm_crawler_enabled_for_request(request):
        raise HTTPException(
            status_code=403,
            detail="LLM Crawler Simulation is disabled by feature flag",
        )


def _request_id(request: Request) -> str:
    header_id = str(request.headers.get("x-request-id", "") or "").strip()
    return header_id or f"req-{uuid.uuid4().hex[:12]}"


def _heartbeat_age_sec(heartbeat: Dict[str, Any] | None) -> int | None:
    if not heartbeat or not heartbeat.get("updatedAt"):
        return None
    try:
        ts = datetime.fromisoformat(str(heartbeat["updatedAt"]).replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


def _worker_is_healthy() -> bool:
    heartbeat = get_worker_heartbeat()
    age_sec = _heartbeat_age_sec(heartbeat)
    if heartbeat is None or age_sec is None:
        return False
    ttl = max(30, int(getattr(settings, "LLM_CRAWLER_WORKER_HEARTBEAT_TTL_SEC", 120) or 120))
    return age_sec <= max(60, ttl * 2)


def _ensure_worker_available() -> None:
    if not bool(getattr(settings, "LLM_CRAWLER_REQUIRE_HEALTHY_WORKER", True)):
        return
    if _worker_is_healthy():
        return
    raise HTTPException(
        status_code=503,
        detail="LLM worker is unavailable or stale. Check worker deployment, /api/ops/status, and the Redis env split (LLM_CRAWLER_REDIS_URL / CELERY_BROKER_URL), then retry.",
    )


def _job_age_sec(job: Dict[str, Any]) -> int | None:
    raw = job.get("createdAt") or job.get("updatedAt")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return None


def _normalize_targets(payload: LlmCrawlerRunRequest) -> list[str]:
    mode = str(payload.mode or "single_url").lower()
    urls: list[str] = []
    if mode == "url_list":
        urls = [str(u).strip() for u in (payload.urls or []) if str(u).strip()]
    elif mode == "sitemap":
        if payload.sitemap_url:
            try:
                import requests
                resp = requests.get(payload.sitemap_url, timeout=8)
                if resp.ok:
                    import re
                    urls = re.findall(r"<loc>([^<]+)</loc>", resp.text, flags=re.I)
            except Exception:
                urls = []
    else:
        if payload.url:
            urls = [str(payload.url).strip()]
    # Failsafe: unique, preserve order
    seen = set()
    normalized = []
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        normalized.append(u)
    return normalized


@router.post("/run")
async def run_llm_crawler(payload: LlmCrawlerRunRequest, request: Request) -> Dict[str, Any]:
    _ensure_feature_enabled(request)
    request_id = _request_id(request)
    scan_token = capture_scan_token_from_request(request)

    limits_enabled = bool(getattr(settings, "LLM_CRAWLER_LIMITS_ENABLED", False))
    subject = request_subject(request)
    options = payload.options.model_dump()
    options["use_proxy"] = bool(payload.use_proxy)
    if limits_enabled:
        tool_limit = max(1, int(getattr(settings, "LLM_CRAWLER_RATE_LIMIT_PER_MINUTE", 999) or 999))
        tool_rate = check_rate_limit(subject, "tool-minute", tool_limit, 60)
        if not tool_rate.get("allowed", True):
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "LLM crawler rate limit exceeded (tool).",
                    "reset_in": tool_rate.get("reset_in", 60),
                    "remaining": tool_rate.get("remaining", 0),
                },
            )
        if bool(options.get("renderJs")):
            render_limit = max(1, int(getattr(settings, "LLM_CRAWLER_RENDER_RATE_LIMIT_PER_DAY", 999) or 999))
            render_rate = check_rate_limit(subject, "render-day", render_limit, 86400)
            if not render_rate.get("allowed", True):
                raise HTTPException(
                    status_code=429,
                    detail={
                        "message": "Rendered fetch daily limit exceeded.",
                        "reset_in": render_rate.get("reset_in", 86400),
                        "remaining": render_rate.get("remaining", 0),
                    },
                )

    worker_ok = _worker_is_healthy()
    # Inline Playwright fallback запрещаем для изоляции: если воркер недоступен — вернём 503
    inline_allowed = False

    targets = _normalize_targets(payload)
    if not targets:
        raise HTTPException(status_code=400, detail="Provide url / urls or sitemap_url")

    # For now limits on batch size are disabled unless flag on; still cap extreme to 200 to protect service.
    hard_cap = 200
    if len(targets) > hard_cap:
        targets = targets[:hard_cap]

    job_ids: list[str] = []

    # Enforce per-subject rate limits when enabled
    if limits_enabled:
        per_minute_limit = max(1, int(getattr(settings, "MAX_JOBS_PER_MINUTE", 10) or 10))
        per_minute_rate = check_rate_limit(subject, "subject-minute", per_minute_limit, 60)
        if not per_minute_rate.get("allowed", True):
            raise HTTPException(
                status_code=429,
                detail={"message": "Too many jobs per minute for this subject", "retry_after": per_minute_rate.get("reset_in")},
            )

    if not worker_ok:
        raise HTTPException(
            status_code=503,
            detail="LLM worker unavailable; please retry when worker is healthy",
        )

    with scan_token_context(scan_token):
        for idx, target_url in enumerate(targets):
            _ensure_worker_available()

            job_id = new_job_id()
            try:
                if limits_enabled:
                    concurrent = inc_subject(subject)
                    if concurrent > int(getattr(settings, "MAX_CONCURRENT_JOBS", 2) or 2):
                        dec_subject(subject)
                        raise HTTPException(
                            status_code=429,
                            detail={"message": "Too many concurrent jobs for subject"},
                        )
                job = create_job_record(
                    job_id=job_id,
                    request_id=request_id,
                    requested_url=target_url,
                    options=options,
                    subject=subject,
                    status_message="Queued",
                )
                enqueue_job(job)
                job_ids.append(job_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"LLM crawler temporarily unavailable: {exc}",
                ) from exc

    return {"jobId": job_ids[0], "jobIds": job_ids, "total": len(job_ids), "status": "queued"}


@router.get("/jobs/{job_id}", response_model=LlmCrawlerJobStatusResponse)
async def get_llm_crawler_job(job_id: str, request: Request) -> Dict[str, Any]:
    _ensure_feature_enabled(request)
    job = get_job_record(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    status = str(job.get("status") or "queued")
    if status in {"queued", "running"}:
        timeout_sec = max(60, int(getattr(settings, "LLM_CRAWLER_STUCK_JOB_TIMEOUT_SEC", 300) or 300))
        age_sec = _job_age_sec(job)
        if age_sec is not None and age_sec >= timeout_sec and not _worker_is_healthy():
            job = update_job_record(
                job_id,
                status="error",
                progress=100,
                error="Job timed out in queue: worker unavailable.",
            )
    return {
        "jobId": str(job.get("jobId") or job_id),
        "requestId": str(job.get("requestId") or ""),
        "status": str(job.get("status") or "queued"),
        "progress": int(job.get("progress") or 0),
        "status_message": job.get("status_message"),
        "result": job.get("result"),
        "render_status": ((job.get("result") or {}).get("render_status") if isinstance(job.get("result"), dict) else None),
        "error": job.get("error"),
    }


@router.get("/worker-health")
async def llm_worker_health(request: Request) -> Dict[str, Any]:
    _ensure_feature_enabled(request)
    heartbeat = get_worker_heartbeat()
    queue_size = queue_depth()
    age_sec = _heartbeat_age_sec(heartbeat)
    return {
        "queue_depth": queue_size,
        "worker_heartbeat": heartbeat,
        "worker_heartbeat_age_sec": age_sec,
        "status": "healthy" if _worker_is_healthy() else "unknown",
    }


@router.get("/quality-gate")
async def llm_crawler_quality_gate(request: Request) -> Dict[str, Any]:
    _ensure_feature_enabled(request)
    role = str(request.headers.get("x-role", "") or "").strip().lower()
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required for quality gate endpoint")
    try:
        return run_quality_gate_from_file()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Quality gate failed: {exc}") from exc


@router.get("/jobs/{job_id}/report", response_class=HTMLResponse)
async def llm_crawler_report(job_id: str, request: Request) -> HTMLResponse:
    _ensure_feature_enabled(request)
    job = get_job_record(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job not found")
    report_v3_enabled = bool(getattr(settings, "LLM_REPORT_V3_ENABLED", False))
    result = job.get("result") or {}
    title = f"LLM Crawler Report — {result.get('final_url') or result.get('requested_url') or job_id}"
    score = (result.get("score") or {}).get("total", "-")
    recs = result.get("recommendations") or []
    ai = result.get("ai_understanding") or {}
    preview = result.get("ai_answer_preview") or {}
    metrics = result.get("metrics_bytes") or {}
    noise = result.get("noise_breakdown") or (((result.get("nojs") or {}).get("segmentation") or {}).get("noise_breakdown") or {})
    main_conf = result.get("main_content_confidence") or (((result.get("nojs") or {}).get("segmentation") or {}).get("main_content_confidence") or {})
    dedupe = result.get("chunk_dedupe") or (((result.get("nojs") or {}).get("content") or {}).get("chunk_dedupe") or {})
    wf = result.get("projected_score_waterfall") or {}
    ai_blocks = result.get("ai_blocks") or {}
    ai_directives = result.get("ai_directives") or {}
    detection_issues = result.get("detection_issues") or []
    improvement_library = result.get("improvement_library") or {}
    quality_profile = result.get("quality_profile") or {}
    quality_gates = result.get("quality_gates") or {}
    detector_calibration = result.get("detector_calibration") or {}
    quality_checks = quality_gates.get("checks") or []
    quality_section = f"""
    <div class="card"><h3>Runtime Quality Profile</h3>
      <p>Status: <b>{quality_profile.get("status","not_evaluated")}</b></p>
      <p>Profile: {quality_profile.get("profile_id", detector_calibration.get("profile_id","-"))} | Page type: {quality_profile.get("page_type", result.get("page_type","-"))}</p>
      <p>Detector coverage: {quality_profile.get("coverage_ratio","-")}</p>
      <p>Avg detector confidence: {quality_profile.get("avg_detector_confidence","-")}</p>
      <p>Retrieval confidence: {quality_profile.get("retrieval_confidence","-")} | Variance: {quality_profile.get("retrieval_variance","-")}</p>
      <p>Citation calibration error: {quality_profile.get("citation_calibration_error","-")}</p>
      <p>Drift flags: {', '.join(quality_profile.get("drift_flags") or []) or 'none'}</p>
    </div>
    <div class="card"><h3>Quality Gates</h3>
      <p>Status: <b>{quality_gates.get("status","not_evaluated")}</b> | Passed: {quality_gates.get("passed","-")} / {quality_gates.get("total","-")}</p>
      <table><thead><tr><th>Metric</th><th>Value</th><th>Threshold</th><th>Pass</th></tr></thead><tbody>
        {''.join([f"<tr><td>{c.get('metric','-')}</td><td>{c.get('value','-')}</td><td>{c.get('threshold','-')}</td><td>{'✅' if c.get('pass') else '❌'}</td></tr>" for c in quality_checks]) or '<tr><td colspan="4">No checks</td></tr>'}
      </tbody></table>
    </div>
    <div class="card"><h3>Detection Issues</h3><ul>
    {''.join([f"<li>{x}</li>" for x in detection_issues]) or '<li>None</li>'}
    </ul></div>
    <div class="card"><h3>Improvement Library</h3><ul>
    {''.join([f"<li>{i.get('title','-')}: {i.get('reason','-')}</li>" for i in (improvement_library.get('missing') or [])]) or '<li>None</li>'}
    </ul></div>
    """ if report_v3_enabled else """
    <div class="card"><h3>Runtime Quality Profile</h3>
      <p>Extended v3 report blocks are disabled by feature flag <b>LLM_REPORT_V3_ENABLED=false</b>.</p>
    </div>
    """
    body = f"""
    <html><head><meta charset="utf-8"><title>{title}</title>
    <style>
    body{{font-family:Arial,sans-serif;max-width:1080px;margin:24px auto;padding:0 14px;background:#f7fbff;color:#0f172a}}
    h1{{margin-bottom:4px}} .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .card{{background:#fff;border:1px solid #dbe5f3;border-radius:12px;padding:14px;margin:10px 0}}
    .pill{{display:inline-block;padding:3px 10px;border-radius:999px;background:#e0f2fe;color:#0c4a6e;font-size:12px;font-weight:700}}
    table{{width:100%;border-collapse:collapse}} th,td{{border:1px solid #e2e8f0;padding:8px;font-size:13px;text-align:left}}
    th{{background:#f1f5f9}}
    </style></head><body>
    <h1>{title}</h1>
    <p><span class="pill">Score {score}/100</span></p>
    <div class="grid">
      <div class="card"><h3>AI Understanding</h3><p><b>Topic:</b> {ai.get("topic","-")}</p><p><b>Confidence:</b> {ai.get("topic_confidence","-")}</p><p><b>Preview:</b> {preview.get("answer","-")}</p></div>
      <div class="card"><h3>Metrics Consistency</h3><p>HTML bytes: {metrics.get("html_bytes","-")}</p><p>Text bytes: {metrics.get("text_bytes","-")}</p><p>Text/HTML ratio: {metrics.get("text_html_ratio","-")}</p></div>
    </div>
    <div class="grid">
      <div class="card"><h3>Noise & Segmentation</h3><p>Main: {noise.get("main_pct","-")}% | Ads: {noise.get("ads_pct","-")}%</p><p>Live: {noise.get("live_pct","-")}% | Nav: {noise.get("nav_pct","-")}%</p><p><b>Confidence:</b> {(main_conf or {}).get("level","-")} ({', '.join((main_conf or {}).get("reasons") or [])})</p></div>
      <div class="card"><h3>Chunk Dedupe</h3><p>Total: {dedupe.get("chunks_total","-")}</p><p>Unique: {dedupe.get("chunks_unique","-")}</p><p>Removed duplicates: {dedupe.get("removed_duplicates","-")} ({dedupe.get("dedupe_ratio","-")}%)</p></div>
    </div>
    <div class="card"><h3>Projected Waterfall</h3>
      <table><thead><tr><th>Step</th><th>Score</th></tr></thead><tbody>
        <tr><td>Baseline</td><td>{wf.get("baseline","-")}</td></tr>
        {''.join([f"<tr><td>{s.get('label','Step')} (+{s.get('delta',0)})</td><td>{s.get('value','-')}</td></tr>" for s in (wf.get('steps') or [])])}
        <tr><td>Projected</td><td>{wf.get("target","-")}</td></tr>
      </tbody></table>
    </div>
    <div class="card"><h3>AI Preview</h3><p><b>Q:</b> {preview.get("question","What is this page about?")}</p><p><b>A:</b> {preview.get("answer","-")}</p><p><small>Warning: {preview.get("warning","none")}</small></p></div>
    <div class="card"><h3>Recommendations with Evidence</h3><ul>
    {''.join([f"<li><b>{r.get('priority','')}</b> {r.get('title','')}<br><small>Evidence: {', '.join((r.get('evidence') or [])[:3])}</small><br><small>Citation effect: {r.get('expected_citation_effect','-')}</small></li>" for r in recs]) or '<li>None</li>'}
    </ul></div>
    <div class="card"><h3>Detection Coverage</h3>
      <p>Coverage: {ai_blocks.get("coverage_percent","-")}%</p>
      <p>Missing critical: {', '.join(ai_blocks.get("missing_critical") or []) or 'none'}</p>
      <p>Directive restricted tokens: {', '.join(ai_directives.get("meta_restrictive_tokens") or []) or 'none'}</p>
    </div>
    {quality_section}
    </body></html>
    """
    return HTMLResponse(content=body)


@router.get("/jobs/{job_id}/report.docx")
async def llm_crawler_report_docx(job_id: str, request: Request) -> Response:
    _ensure_feature_enabled(request)
    job = get_job_record(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job not found")
    from app.tools.llmCrawler.report_docx import build_docx_v2
    report_v3_enabled = bool(getattr(settings, "LLM_REPORT_V3_ENABLED", False))
    payload = build_docx_v2(job, job_id, wow_enabled=report_v3_enabled)
    return Response(
        content=payload.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="llm_crawler_{job_id}.docx"'},
    )
