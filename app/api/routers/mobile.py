"""
Mobile Check router.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, BackgroundTasks, Request

from app.validators import URLModel
from app.api.routers._task_store import create_task_pending, update_task_state
from app.core.scan_token import capture_scan_token_from_request, scan_token_context

router = APIRouter(tags=["SEO Tools"])


def check_mobile_simple(url: str) -> Dict[str, Any]:
    """Simple mobile check - viewport and responsive indicators"""
    import requests
    from bs4 import BeautifulSoup
    
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)"})
        soup = BeautifulSoup(response.text, 'html.parser')
        
        issues = []
        
        # Check viewport meta tag
        viewport = soup.find('meta', attrs={'name': 'viewport'})
        if not viewport:
            issues.append("Отсутствует мета-тег viewport")
        else:
            content = viewport.get('content', '')
            if 'width=device-width' not in content:
                issues.append("В viewport не задано width=device-width")
        
        # Check for responsive images
        images = soup.find_all('img')
        large_images = []
        for img in images:
            src = img.get('src', '')
            if any(size in src for size in ['large', 'big', 'full', 'original']):
                large_images.append(src)
        
        if large_images:
            issues.append(f"Found {len(large_images)} potentially large images")
        
        # Check tap targets
        buttons = soup.find_all(['button', 'a'])
        small_buttons = []
        for btn in buttons:
            text = btn.get_text(strip=True)
            if text and len(text) > 20:
                small_buttons.append(text[:20])
        
        # Mobile-friendly indicators
        mobile_friendly = len(issues) == 0
        
        return {
            "task_type": "mobile_check",
            "url": url,
            "completed_at": datetime.utcnow().isoformat(),
            "results": {
                "status_code": response.status_code,
                "viewport_found": bool(viewport),
                "viewport_content": viewport.get('content') if viewport else None,
                "total_images": len(images),
                "issues": issues,
                "issues_count": len(issues),
                "mobile_friendly": mobile_friendly,
                "score": max(0, 100 - len(issues) * 20),
                "recommendations": [
                    "Use responsive design with flexible layouts",
                    "Ensure tap targets are at least 48x48 pixels",
                    "Use appropriate font sizes (16px minimum)",
                    "Test with Google Mobile-Friendly Test"
                ]
            }
        }
    except Exception as e:
        return {
            "task_type": "mobile_check",
            "url": url,
            "completed_at": datetime.utcnow().isoformat(),
            "results": {
                "error": str(e)
            }
        }


def check_mobile_full(
    url: str,
    task_id: str,
    mode: str = "full",
    devices: Optional[List[str]] = None,
    progress_callback=None,
    use_proxy: bool = False,
    throttle: str = "none",
) -> Dict[str, Any]:
    """Feature-flagged mobile check with v2 service fallback."""
    from app.config import settings

    engine = (getattr(settings, "MOBILE_CHECK_ENGINE", "v2") or "v2").lower()
    if engine == "v2":
        try:
            from app.tools.mobile.service_v2 import MobileCheckServiceV2

            checker = MobileCheckServiceV2(timeout=getattr(settings, "MOBILE_CHECK_TIMEOUT", 20), use_proxy=use_proxy, throttle=throttle)
            selected_mode = (mode or getattr(settings, "MOBILE_CHECK_MODE", "quick") or "quick").lower()
            if selected_mode not in ("quick", "full"):
                selected_mode = "full"
            return checker.run(
                url=url,
                task_id=task_id,
                mode=selected_mode,
                selected_devices=devices,
                throttle=throttle,
                progress_callback=progress_callback,
            )
        except Exception as e:
            print(f"[API] mobile v2 failed, fallback to simple: {e}")
            fallback = check_mobile_simple(url)
            fallback_results = fallback.get("results", {})
            fallback_results["engine"] = "legacy-fallback"
            fallback_results["engine_error"] = str(e)
            fallback_results["mobile_friendly"] = False
            fallback_results["score"] = None
            fallback_results["issues"] = [
                {
                    "severity": "critical",
                    "code": "mobile_engine_error",
                    "title": "Ошибка движка mobile v2",
                    "details": str(e),
                }
            ]
            fallback_results["issues_count"] = 1
            return fallback

    legacy = check_mobile_simple(url)
    legacy_results = legacy.get("results", {})
    legacy_results["engine"] = "legacy"
    return legacy


class MobileCheckRequest(URLModel):
    url: str
    mode: Optional[str] = "quick"
    devices: Optional[List[str]] = None
    use_proxy: bool = False
    throttle: str = "none"


@router.post("/tasks/mobile-check")
async def create_mobile_check(data: MobileCheckRequest, background_tasks: BackgroundTasks, request: Request):
    """Mobile check with background progress updates."""
    url = data.url
    from app.config import settings
    mode = data.mode or getattr(settings, "MOBILE_CHECK_MODE", "quick") or "quick"
    devices = data.devices
    throttle = data.throttle if data.throttle in ("none", "3g", "4g") else "none"

    print(f"[API] Mobile check queued for: {url}")
    task_id = f"mobile-{datetime.now().timestamp()}"
    create_task_pending(task_id, "mobile_check", url, status_message="Задача поставлена в очередь")
    scan_token = capture_scan_token_from_request(request)

    def _run_mobile_task() -> None:
        with scan_token_context(scan_token):
            try:
                update_task_state(task_id, status="RUNNING", progress=5, status_message="Подготовка мобильного аудита")

                def _progress(progress: int, message: str) -> None:
                    update_task_state(task_id, status="RUNNING", progress=progress, status_message=message)

                result = check_mobile_full(
                    url,
                    task_id=task_id,
                    mode=mode,
                    devices=devices,
                    progress_callback=_progress,
                    use_proxy=bool(data.use_proxy),
                    throttle=throttle,
                )

                results = result.get("results", {}) if isinstance(result, dict) else {}
                engine = (results.get("engine") or "").lower()
                has_engine_error = bool(results.get("engine_error")) or engine in ("legacy-fallback", "")

                if has_engine_error:
                    error_message = results.get("engine_error") or "Ошибка движка mobile"
                    update_task_state(
                        task_id,
                        status="FAILURE",
                        progress=100,
                        status_message="Мобильный аудит завершился с ошибкой",
                        result=result,
                        error=error_message,
                    )
                    return

                update_task_state(
                    task_id,
                    status="SUCCESS",
                    progress=100,
                    status_message="Мобильный аудит завершен",
                    result=result,
                    error=None,
                )
            except Exception as e:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="Мобильный аудит завершился с ошибкой",
                    error=str(e),
                )

    background_tasks.add_task(_run_mobile_task)
    return {"task_id": task_id, "status": "PENDING", "message": "Проверка мобильной версии запущена"}
