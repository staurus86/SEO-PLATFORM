"""
Render Audit router.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, BackgroundTasks, Request

from app.validators import URLModel
from app.api.routers._task_store import create_task_pending, update_task_state
from app.core.scan_token import capture_scan_token_from_request, scan_token_context

router = APIRouter(tags=["SEO Tools"])


def check_render_simple(url: str) -> Dict[str, Any]:
    """Simple render audit - basic HTML vs JS comparison"""
    import requests
    
    try:
        # Get page without JS
        response_no_js = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        
        # Basic comparison
        content_length = len(response_no_js.text)
        word_count = len(response_no_js.text.split())
        
        # Check for common JavaScript frameworks
        js_frameworks = []
        text_lower = response_no_js.text.lower()
        if 'react' in text_lower:
            js_frameworks.append("React")
        if 'vue' in text_lower:
            js_frameworks.append("Vue.js")
        if 'angular' in text_lower:
            js_frameworks.append("Angular")
        if 'jquery' in text_lower:
            js_frameworks.append("jQuery")
        
        # Check for SSR indicators
        is_ssr = bool(response_no_js.text.strip())
        
        return {
            "task_type": "render_audit",
            "url": url,
            "completed_at": datetime.utcnow().isoformat(),
            "results": {
                "status_code": response_no_js.status_code,
                "content_length": content_length,
                "word_count": word_count,
                "is_ssr": is_ssr,
                "js_frameworks": js_frameworks,
                "recommendations": [
                    "Ensure critical content is available without JavaScript",
                    "Use server-side rendering for better SEO",
                    "Test with tools like Google Search Console"
                ]
            }
        }
    except Exception as e:
        return {
            "task_type": "render_audit",
            "url": url,
            "completed_at": datetime.utcnow().isoformat(),
            "results": {
                "error": str(e)
            }
        }


def check_render_full(
    url: str,
    task_id: str,
    progress_callback=None,
    use_proxy: bool = False,
) -> Dict[str, Any]:
    """Feature-flagged render audit with v2 service fallback."""
    from app.config import settings
    debug_render = bool(getattr(settings, "RENDER_AUDIT_DEBUG", False) or getattr(settings, "DEBUG", False))

    def _ensure_render_profiles(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        results = payload.get("results")
        if not isinstance(results, dict):
            return payload

        variants = results.get("variants")
        if not isinstance(variants, list):
            variants = []
            results["variants"] = variants
        if debug_render:
            print(f"[RENDER-DEBUG][{task_id}] pre-normalize variants={len(variants)}")

        # Normalize profile identity/labels so mobile cannot be shown as desktop.
        normalized: List[Dict[str, Any]] = []
        for v in variants:
            if not isinstance(v, dict):
                continue
            variant_id = str(v.get("variant_id", "")).lower().strip()
            if not variant_id:
                profile_type = str(v.get("profile_type", "")).lower().strip()
                if profile_type == "mobile" or bool(v.get("mobile")):
                    variant_id = "googlebot_mobile"
                else:
                    variant_id = "googlebot_desktop"
                v["variant_id"] = variant_id

            if variant_id == "googlebot_mobile":
                v["variant_label"] = "Googlebot (мобильный)"
                v["mobile"] = True
                v["profile_type"] = "mobile"
            elif variant_id == "googlebot_desktop":
                v["variant_label"] = "Googlebot (ПК)"
                v["mobile"] = False
                v["profile_type"] = "desktop"
            normalized.append(v)
        variants = normalized
        results["variants"] = variants
        if debug_render:
            print(
                f"[RENDER-DEBUG][{task_id}] post-normalize variants="
                + ", ".join([f"{v.get('variant_id')}:{v.get('variant_label')}:{v.get('profile_type')}" for v in variants])
            )

        required = [
            ("googlebot_desktop", "Googlebot (ПК)", False),
            ("googlebot_mobile", "Googlebot (мобильный)", True),
        ]
        existing_ids = {str(v.get("variant_id", "")).lower() for v in variants if isinstance(v, dict)}

        for variant_id, label, is_mobile in required:
            if variant_id in existing_ids:
                continue
            fail_issue = {
                "severity": "critical",
                "code": "variant_missing_in_result",
                "title": "Профиль рендеринга отсутствует в результате",
                "details": f"Профиль {label} не вернулся из движка и добавлен как ошибка.",
            }
            variants.append(
                {
                    "variant_id": variant_id,
                    "variant_label": label,
                    "mobile": is_mobile,
                    "profile_type": "mobile" if is_mobile else "desktop",
                    "raw": {},
                    "rendered": {},
                    "missing": {"visible_text": [], "headings": [], "links": [], "images": [], "structured_data": []},
                    "meta_non_seo": {"raw": {}, "rendered": {}, "comparison": {"total": 0, "same": 0, "changed": 0, "only_rendered": 0, "only_raw": 0, "items": []}},
                    "seo_required": {"total": 0, "pass": 0, "warn": 0, "fail": 0, "items": []},
                    "metrics": {"total_missing": 0.0, "rendered_total": 0.0, "missing_pct": 0.0, "score": 0.0},
                    "timings": {"raw_s": 0.0, "rendered_s": 0.0},
                    "timing_nojs_ms": {},
                    "timing_js_ms": {},
                    "issues": [fail_issue],
                    "recommendations": ["Проверьте логи рендер-движка и окружение Playwright для этого профиля."],
                    "screenshots": {},
                }
            )
            issues = results.get("issues")
            if not isinstance(issues, list):
                issues = []
                results["issues"] = issues
            issues.append({**fail_issue, "variant": label})

        summary = results.get("summary")
        if not isinstance(summary, dict):
            summary = {}
            results["summary"] = summary
        summary["variants_total"] = len(results.get("variants", []))
        results["issues_count"] = len(results.get("issues", []) or [])
        if debug_render:
            print(
                f"[RENDER-DEBUG][{task_id}] ensured variants_total={summary['variants_total']} "
                f"issues_count={results['issues_count']}"
            )
        return payload

    engine = (getattr(settings, "RENDER_AUDIT_ENGINE", "v2") or "v2").lower()
    if engine == "v2":
        try:
            from app.tools.render.service_v2 import RenderAuditServiceV2

            checker = RenderAuditServiceV2(timeout=getattr(settings, "RENDER_AUDIT_TIMEOUT", 35), use_proxy=use_proxy)
            result = checker.run(url=url, task_id=task_id, progress_callback=progress_callback)
            ensured = _ensure_render_profiles(result)
            if debug_render:
                ensured_results = ensured.get("results", {}) if isinstance(ensured, dict) else {}
                ensured_variants = ensured_results.get("variants", []) if isinstance(ensured_results, dict) else []
                print(
                    f"[RENDER-DEBUG][{task_id}] return variants="
                    + ", ".join([f"{v.get('variant_id')}:{v.get('variant_label')}:{v.get('profile_type')}" for v in ensured_variants if isinstance(v, dict)])
                )
            return ensured
        except Exception as e:
            print(f"[API] render v2 failed, fallback to simple: {e}")
            fallback = check_render_simple(url)
            fallback_results = fallback.get("results", {}) if isinstance(fallback, dict) else {}
            fallback_results["engine"] = "legacy-fallback"
            fallback_results["engine_error"] = str(e)
            fallback_results["issues"] = [
                {
                    "severity": "critical",
                    "code": "render_engine_error",
                    "title": "Ошибка движка render v2",
                    "details": str(e),
                }
            ]
            fallback_results["issues_count"] = 1
            fallback_results["summary"] = {
                "variants_total": 0,
                "critical_issues": 1,
                "warning_issues": 0,
                "info_issues": 0,
                "score": None,
                "missing_total": 0,
                "avg_missing_pct": 0,
                "avg_raw_load_ms": 0,
                "avg_js_load_ms": 0,
            }
            fallback_results["variants"] = []
            fallback_results["recommendations"] = ["Движок v2 недоступен, проверьте окружение Playwright."]
            return fallback

    legacy = check_render_simple(url)
    legacy_results = legacy.get("results", {}) if isinstance(legacy, dict) else {}
    legacy_results["engine"] = "legacy"
    return legacy


class RenderAuditRequest(URLModel):
    url: str
    use_proxy: bool = False


@router.post("/tasks/render-audit")
async def create_render_audit(data: RenderAuditRequest, background_tasks: BackgroundTasks, request: Request):
    """Render audit with background progress updates."""
    url = data.url
    from app.config import settings
    debug_render = bool(getattr(settings, "RENDER_AUDIT_DEBUG", False) or getattr(settings, "DEBUG", False))

    print(f"[API] Render audit queued for: {url}")
    task_id = f"render-{datetime.now().timestamp()}"
    create_task_pending(task_id, "render_audit", url, status_message="Задача поставлена в очередь")
    scan_token = capture_scan_token_from_request(request)

    def _run_render_task() -> None:
        with scan_token_context(scan_token):
            try:
                update_task_state(task_id, status="RUNNING", progress=5, status_message="Подготовка рендер-аудита")

                def _progress(progress: int, message: str) -> None:
                    update_task_state(task_id, status="RUNNING", progress=progress, status_message=message)

                result = check_render_full(url, task_id=task_id, progress_callback=_progress, use_proxy=bool(data.use_proxy))
                results = result.get("results", {}) if isinstance(result, dict) else {}
                if debug_render:
                    variants = results.get("variants", []) if isinstance(results, dict) else []
                    print(
                        f"[RENDER-DEBUG][{task_id}] background-result variants="
                        + ", ".join(
                            [
                                f"{v.get('variant_id')}:{v.get('variant_label')}:{v.get('profile_type')}:{v.get('mobile')}"
                                for v in variants
                                if isinstance(v, dict)
                            ]
                        )
                    )
                engine = (results.get("engine") or "").lower()
                has_engine_error = bool(results.get("engine_error")) or engine in ("legacy-fallback", "")

                if has_engine_error:
                    error_message = results.get("engine_error") or "Ошибка движка render"
                    update_task_state(
                        task_id,
                        status="FAILURE",
                        progress=100,
                        status_message="Ошибка рендер-аудита",
                        result=result,
                        error=error_message,
                    )
                    return

                update_task_state(
                    task_id,
                    status="SUCCESS",
                    progress=100,
                    status_message="Рендер-аудит завершен",
                    result=result,
                )
            except Exception as exc:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="Ошибка рендер-аудита",
                    error=str(exc),
                )

    background_tasks.add_task(_run_render_task)
    return {
        "task_id": task_id,
        "status": "PENDING",
        "message": "Render audit queued",
    }
