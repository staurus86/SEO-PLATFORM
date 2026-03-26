"""Mobile checker v2 with multi-device screenshots and issue analysis."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import re
import time

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.core.scan_token import get_scan_token_headers
from app.tools.http_text import decode_response_text


@dataclass(frozen=True)
class MobileDevice:
    name: str
    category: str
    width: int
    height: int
    dpr: float
    user_agent: str


DEVICE_PROFILES: List[MobileDevice] = [
    MobileDevice(
        "iPhone 16 Pro",
        "phone",
        402,
        874,
        3.0,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    ),
    MobileDevice(
        "Samsung Galaxy S25",
        "phone",
        412,
        915,
        3.0,
        "Mozilla/5.0 (Linux; Android 15; SM-S931B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    ),
    MobileDevice(
        "Google Pixel 9 Pro",
        "phone",
        412,
        915,
        3.0,
        "Mozilla/5.0 (Linux; Android 15; Pixel 9 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    ),
    MobileDevice(
        "Samsung Galaxy A55",
        "phone",
        412,
        915,
        2.75,
        "Mozilla/5.0 (Linux; Android 14; SM-A556B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36",
    ),
    MobileDevice(
        "Samsung Galaxy Z Fold5",
        "phone",
        717,
        892,
        3.5,
        "Mozilla/5.0 (Linux; Android 13; SM-F946B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36",
    ),
    MobileDevice(
        "iPad Pro 11 (2024)",
        "tablet",
        834,
        1194,
        2.0,
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    ),
    MobileDevice(
        "Samsung Galaxy Tab S9",
        "tablet",
        960,
        1376,
        2.25,
        "Mozilla/5.0 (Linux; Android 14; SM-X716B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Safari/537.36",
    ),
]

DEFAULT_DEVICE_NAMES = [
    "iPhone 16 Pro",
    "Samsung Galaxy S25",
    "Google Pixel 9 Pro",
    "Samsung Galaxy A55",
    "Samsung Galaxy Z Fold5",
    "iPad Pro 11 (2024)",
    "Samsung Galaxy Tab S9",
]

QUICK_DEVICE_NAMES = [
    "iPhone 16 Pro",
    "Samsung Galaxy S25",
]


def _slug(text: str) -> str:
    val = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return val[:80] if len(val) > 80 else val


def _extract_meta_viewport(soup: BeautifulSoup) -> Optional[str]:
    meta = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    if not meta:
        return None
    return (meta.get("content") or "").strip() or None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


class MobileCheckServiceV2:
    def __init__(self, timeout: int = 20, use_proxy: bool = False, throttle: str = "none"):
        self.timeout = timeout
        self.use_proxy = use_proxy
        self.throttle = throttle if throttle in ("none", "3g", "4g") else "none"

    def _select_devices(self, mode: str = "full", selected: Optional[List[str]] = None) -> List[MobileDevice]:
        by_name = {d.name: d for d in DEVICE_PROFILES}
        if selected:
            out = [by_name[name] for name in selected if name in by_name]
            if out:
                return out
        names = QUICK_DEVICE_NAMES if mode == "quick" else DEFAULT_DEVICE_NAMES
        return [by_name[n] for n in names if n in by_name]

    def _http_prefetch(self, url: str) -> Dict[str, Any]:
        try:
            proxy_kwargs = {}
            if self.use_proxy:
                from app.proxy import get_requests_proxies
                _proxies = get_requests_proxies()
                if _proxies:
                    proxy_kwargs["proxies"] = _proxies
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"}, **proxy_kwargs)
            soup = BeautifulSoup(decode_response_text(resp), "html.parser")
            viewport = _extract_meta_viewport(soup)
            return {
                "ok": True,
                "status_code": resp.status_code,
                "final_url": resp.url,
                "viewport_content": viewport,
                "has_viewport": bool(viewport),
            }
        except Exception as e:
            return {
                "ok": False,
                "status_code": None,
                "error": str(e),
                "final_url": url,
                "viewport_content": None,
                "has_viewport": False,
            }

    def _evaluate_page(self, page) -> Dict[str, Any]:
        js = """
        (() => {
          const viewportWidth = window.innerWidth || 0;
          const docWidth = Math.max(document.body?.scrollWidth || 0, document.documentElement?.scrollWidth || 0);
          const horizontalOverflow = docWidth > viewportWidth + 2;

          const touchElements = Array.from(document.querySelectorAll('a,button,input,select,textarea,[role="button"]'));
          const touchRects = touchElements.map(el => {
            const r = el.getBoundingClientRect();
            return {w:r.width,h:r.height,visible:r.width>0&&r.height>0};
          }).filter(x => x.visible);
          const smallTouchTargets = touchRects.filter(r => r.w < 44 || r.h < 44).length;

          const textNodes = Array.from(document.querySelectorAll('p,span,li,a,button,label,input,textarea,h1,h2,h3,h4,h5,h6'));
          const smallFonts = textNodes.filter(el => {
            const style = window.getComputedStyle(el);
            const size = parseFloat(style.fontSize || '0');
            return size > 0 && size < 16;
          }).length;

          const images = Array.from(document.images || []);
          const largeImages = images.filter(img => {
            const r = img.getBoundingClientRect();
            return r.width > viewportWidth + 2;
          }).length;

          const menus = document.querySelectorAll('.hamburger,.menu-toggle,.navbar-toggle,[aria-label=\"menu\"]');
          const hasHamburger = menus.length > 0;

          return {
            viewport_width: viewportWidth,
            document_width: docWidth,
            horizontal_overflow: horizontalOverflow,
            touch_targets_total: touchRects.length,
            small_touch_targets: smallTouchTargets,
            small_fonts: smallFonts,
            large_images: largeImages,
            has_hamburger_menu: hasHamburger,
          };
        })();
        """
        return page.evaluate(js)

    def _collect_issues(self, eval_data: Dict[str, Any], viewport_content: Optional[str], console_errors: int) -> List[Dict[str, Any]]:
        issues: List[Dict[str, Any]] = []
        if not viewport_content:
            issues.append({"severity": "critical", "code": "viewport_missing", "title": "Отсутствует meta viewport", "details": "Добавьте meta viewport для корректного отображения на мобильных устройствах."})
        elif "width=device-width" not in viewport_content:
            issues.append({"severity": "critical", "code": "viewport_invalid", "title": "Некорректный viewport", "details": f"Текущее значение: {viewport_content}"})
        if eval_data.get("horizontal_overflow"):
            issues.append({"severity": "critical", "code": "horizontal_overflow", "title": "Обнаружен горизонтальный скролл", "details": f"Ширина документа {eval_data.get('document_width')}px превышает viewport {eval_data.get('viewport_width')}px"})
        if _safe_int(eval_data.get("small_touch_targets")) > 0:
            issues.append({"severity": "warning", "code": "small_touch_targets", "title": "Слишком маленькие интерактивные элементы", "details": f"{eval_data.get('small_touch_targets')} элементов меньше 44x44px."})
        if _safe_int(eval_data.get("small_fonts")) > 0:
            issues.append({"severity": "warning", "code": "small_fonts", "title": "Слишком мелкий текст", "details": f"{eval_data.get('small_fonts')} элементов с размером шрифта меньше 16px."})
        if _safe_int(eval_data.get("large_images")) > 0:
            issues.append({"severity": "warning", "code": "large_images", "title": "Изображения шире экрана", "details": f"{eval_data.get('large_images')} изображений выходят за границы viewport."})
        if console_errors > 0:
            issues.append({"severity": "warning", "code": "console_errors", "title": "Ошибки JavaScript в консоли", "details": f"Зафиксировано {console_errors} ошибок в консоли браузера."})
        if not eval_data.get("has_hamburger_menu"):
            issues.append({"severity": "info", "code": "nav_menu", "title": "Не найдено мобильное меню", "details": "Навигация может быть неудобной на узких экранах."})
        return issues

    def _apply_throttling(self, context, page) -> None:
        """Apply network throttling via CDP for realistic mobile testing."""
        throttle_profile = self.throttle
        if throttle_profile == "none":
            return
        try:
            cdp = context.new_cdp_session(page)
            if throttle_profile == "3g":
                cdp.send("Network.emulateNetworkConditions", {
                    "offline": False,
                    "downloadThroughput": int(1.6 * 1024 * 1024 / 8),  # 1.6 Mbps
                    "uploadThroughput": int(750 * 1024 / 8),  # 750 Kbps
                    "latency": 300,  # 300ms RTT
                })
            elif throttle_profile == "4g":
                cdp.send("Network.emulateNetworkConditions", {
                    "offline": False,
                    "downloadThroughput": int(9 * 1024 * 1024 / 8),  # 9 Mbps
                    "uploadThroughput": int(1.5 * 1024 * 1024 / 8),  # 1.5 Mbps
                    "latency": 70,  # 70ms RTT
                })
        except Exception:
            # CDP throttling is best-effort; do not fail the audit if unsupported.
            pass

    def _evaluate_wcag(self, page) -> Dict[str, Any]:
        """Run basic WCAG AA accessibility checks via JS evaluation."""
        wcag_js = """
        () => {
            const issues = [];
            // 1. Images without alt text
            const imgs = document.querySelectorAll('img:not([alt])');
            if (imgs.length > 0) issues.push({code: 'img_no_alt', count: imgs.length, severity: 'warning', title: 'Images without alt text'});

            // 2. Form inputs without labels
            const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"])');
            let unlabeled = 0;
            inputs.forEach(inp => {
                const id = inp.id;
                const hasLabel = id && document.querySelector('label[for="' + id + '"]');
                const hasAriaLabel = inp.getAttribute('aria-label') || inp.getAttribute('aria-labelledby');
                const hasPlaceholder = inp.getAttribute('placeholder');
                if (!hasLabel && !hasAriaLabel && !hasPlaceholder) unlabeled++;
            });
            if (unlabeled > 0) issues.push({code: 'input_no_label', count: unlabeled, severity: 'warning', title: 'Form inputs without labels'});

            // 3. Missing lang attribute
            if (!document.documentElement.getAttribute('lang')) issues.push({code: 'no_lang', count: 1, severity: 'info', title: 'Missing lang attribute on html'});

            // 4. Empty links/buttons
            const emptyLinks = [...document.querySelectorAll('a')].filter(a => !(a.textContent || '').trim() && !a.querySelector('img') && !a.getAttribute('aria-label')).length;
            if (emptyLinks > 0) issues.push({code: 'empty_links', count: emptyLinks, severity: 'warning', title: 'Empty links without text or aria-label'});

            // 5. Skip nav detection
            const hasSkipNav = !!document.querySelector('a[href="#main"], a[href="#content"], [class*="skip"]');
            if (!hasSkipNav) issues.push({code: 'no_skip_nav', count: 1, severity: 'info', title: 'No skip navigation link found'});

            return {issues, checked: true};
        }
        """
        try:
            return page.evaluate(wcag_js)
        except Exception:
            return {"issues": [], "checked": False}

    def _evaluate_contrast(self, page) -> Dict[str, Any]:
        """Run simplified WCAG AA color contrast checks via JS evaluation."""
        contrast_js = r"""
        () => {
            function luminance(r, g, b) {
                const [rs, gs, bs] = [r, g, b].map(c => {
                    c = c / 255;
                    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
                });
                return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
            }
            function contrastRatio(l1, l2) {
                const lighter = Math.max(l1, l2);
                const darker = Math.min(l1, l2);
                return (lighter + 0.05) / (darker + 0.05);
            }
            function parseColor(color) {
                const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                return m ? [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])] : null;
            }

            const failures = [];
            const textEls = document.querySelectorAll('p, span, a, li, h1, h2, h3, h4, h5, h6, td, th, label, button');
            let checked = 0, failed = 0;
            const maxCheck = 200;

            for (const el of textEls) {
                if (checked >= maxCheck) break;
                const style = getComputedStyle(el);
                const fg = parseColor(style.color);
                const bg = parseColor(style.backgroundColor);
                if (!fg || !bg) continue;
                if (bg[0] === 0 && bg[1] === 0 && bg[2] === 0 && style.backgroundColor.includes('0)')) continue;
                checked++;
                const ratio = contrastRatio(luminance(...fg), luminance(...bg));
                const fontSize = parseFloat(style.fontSize);
                const isLarge = fontSize >= 18 || (fontSize >= 14 && style.fontWeight >= 700);
                const minRatio = isLarge ? 3.0 : 4.5;
                if (ratio < minRatio) {
                    failed++;
                    if (failures.length < 10) {
                        failures.push({tag: el.tagName.toLowerCase(), text: (el.textContent||'').trim().substring(0,50), ratio: Math.round(ratio*100)/100, required: minRatio});
                    }
                }
            }
            return {checked, failed, failures, pass_rate: checked > 0 ? Math.round((checked - failed) / checked * 100) : 100};
        }
        """
        try:
            return page.evaluate(contrast_js)
        except Exception:
            return {"checked": 0, "failed": 0, "failures": [], "pass_rate": 100}

    def run(
        self,
        url: str,
        task_id: str,
        mode: str = "full",
        selected_devices: Optional[List[str]] = None,
        throttle: str = "none",
        progress_callback=None,
    ) -> Dict[str, Any]:
        def _notify(progress: int, message: str) -> None:
            if callable(progress_callback):
                try:
                    progress_callback(progress, message)
                except Exception:
                    pass

        # Allow run-time throttle override
        if throttle in ("3g", "4g"):
            self.throttle = throttle

        devices = self._select_devices(mode=mode, selected=selected_devices)
        _notify(8, "Получение метаданных страницы")
        prefetch = self._http_prefetch(url)
        domain = urlparse(url).netloc or "site"
        stamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
        shot_dir = Path(settings.REPORTS_DIR) / "mobile" / task_id / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)

        results: List[Dict[str, Any]] = []
        global_issues: List[Dict[str, Any]] = []
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            _notify(100, "Среда Playwright недоступна")
            return {
                "task_type": "mobile_check",
                "url": url,
                "completed_at": datetime.utcnow().isoformat(),
                "results": {
                    "engine": "v2",
                    "error": f"Playwright unavailable: {e}",
                    "mobile_friendly": False,
                    "devices_tested": [d.name for d in devices],
                    "device_results": [],
                    "issues": [{"severity": "critical", "code": "playwright_unavailable", "title": "Playwright runtime unavailable", "details": str(e)}],
                    "issues_count": 1,
                    "artifacts": {"screenshot_dir": str(shot_dir), "screenshots": []},
                },
            }

        with sync_playwright() as p:
            _notify(12, "Запуск браузерного движка")
            launch_kwargs = {"headless": True}
            extra_headers = get_scan_token_headers()
            if self.use_proxy:
                from app.proxy import get_playwright_proxy
                _pw_proxy = get_playwright_proxy()
                if _pw_proxy:
                    launch_kwargs["proxy"] = _pw_proxy
            browser = p.chromium.launch(**launch_kwargs)
            total_devices = len(devices) or 1
            for device in devices:
                started = time.perf_counter()
                device_idx = len(results) + 1
                _notify(12 + int(((device_idx - 1) / total_devices) * 80), f"Проверка {device.name} ({device_idx}/{total_devices})")
                shot_name = f"mobile_{_slug(domain)}_{stamp}_{_slug(device.name)}.png"
                shot_path = shot_dir / shot_name
                context_kwargs = {
                    "viewport": {"width": device.width, "height": device.height},
                    "device_scale_factor": device.dpr,
                    "is_mobile": True,
                    "has_touch": True,
                    "user_agent": device.user_agent,
                }
                if extra_headers:
                    context_kwargs["extra_http_headers"] = extra_headers
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
                # Apply network throttling for realistic mobile testing
                self._apply_throttling(context, page)
                console_errors = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                status_code = None
                final_url = prefetch.get("final_url") or url
                error = None
                eval_data: Dict[str, Any] = {}
                wcag_data: Dict[str, Any] = {}
                contrast_data: Dict[str, Any] = {}
                try:
                    try:
                        response = page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                    except Exception:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                    status_code = response.status if response else prefetch.get("status_code")
                    final_url = page.url or final_url
                    page.screenshot(path=str(shot_path), full_page=True)
                    eval_data = self._evaluate_page(page)
                    wcag_data = self._evaluate_wcag(page)
                    contrast_data = self._evaluate_contrast(page)
                except Exception as e:
                    error = str(e)
                finally:
                    try:
                        context.close()
                    except Exception:
                        # Context can already be closed by Playwright/browser crash; keep audit running.
                        pass

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                issues = self._collect_issues(eval_data, prefetch.get("viewport_content"), len(console_errors))
                # WCAG accessibility issues
                for wcag_issue in (wcag_data.get("issues") or []):
                    issues.append({
                        "severity": wcag_issue.get("severity", "info"),
                        "code": wcag_issue.get("code", "wcag_issue"),
                        "title": wcag_issue.get("title", "Accessibility issue"),
                        "details": f"{wcag_issue.get('count', 0)} element(s) affected.",
                    })
                # Contrast issues
                if contrast_data.get("failed", 0) > 0:
                    issues.append({
                        "severity": "warning",
                        "code": "low_contrast",
                        "title": "Low contrast text found",
                        "details": f"{contrast_data['failed']} elements below WCAG AA threshold (checked {contrast_data.get('checked', 0)}, pass rate {contrast_data.get('pass_rate', 100)}%).",
                    })
                if error:
                    issues.insert(0, {"severity": "critical", "code": "runtime_error", "title": "Device check failed", "details": error})
                global_issues.extend([{**issue, "device": device.name} for issue in issues])

                mobile_friendly = not any(issue["severity"] in ("critical", "warning") for issue in issues)
                results.append(
                    {
                        "device_name": device.name,
                        "category": device.category,
                        "orientation": "portrait",
                        "viewport": {"width": device.width, "height": device.height},
                        "pixel_ratio": device.dpr,
                        "status_code": status_code,
                        "final_url": final_url,
                        "load_time_ms": elapsed_ms,
                        "console_errors_count": len(console_errors),
                        "mobile_friendly": mobile_friendly,
                        "issues_count": len(issues),
                        "issues": issues,
                        "accessibility": wcag_data,
                        "contrast": contrast_data,
                        "throttle_profile": self.throttle,
                        "screenshot_path": str(shot_path),
                        "screenshot_name": shot_name,
                        "screenshot_url": f"/api/mobile-artifacts/{task_id}/{shot_name}",
                    }
                )
                _notify(12 + int((device_idx / total_devices) * 80), f"Завершено: {device.name} ({device_idx}/{total_devices})")
            # Task 3.1: Landscape orientation pass for phones only
            phone_devices = [d for d in devices if d.category == "phone"]
            total_landscape = len(phone_devices) or 1
            for ls_idx, device in enumerate(phone_devices, start=1):
                started = time.perf_counter()
                _notify(92 + int((ls_idx / total_landscape) * 6), f"Ландшафт: {device.name} ({ls_idx}/{total_landscape})")
                landscape_viewport = {"width": device.height, "height": device.width}
                shot_name = f"mobile_{_slug(domain)}_{stamp}_{_slug(device.name)}_landscape.png"
                shot_path = shot_dir / shot_name
                landscape_context_kwargs = {
                    "viewport": landscape_viewport,
                    "device_scale_factor": device.dpr,
                    "is_mobile": True,
                    "has_touch": True,
                    "user_agent": device.user_agent,
                }
                if extra_headers:
                    landscape_context_kwargs["extra_http_headers"] = extra_headers
                context = browser.new_context(**landscape_context_kwargs)
                page = context.new_page()
                # Apply network throttling for landscape pass as well
                self._apply_throttling(context, page)
                ls_console_errors = []
                page.on("console", lambda msg: ls_console_errors.append(msg.text) if msg.type == "error" else None)
                ls_status_code = None
                ls_final_url = prefetch.get("final_url") or url
                ls_error = None
                ls_eval_data: Dict[str, Any] = {}
                try:
                    try:
                        response = page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                    except Exception:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                    ls_status_code = response.status if response else prefetch.get("status_code")
                    ls_final_url = page.url or ls_final_url
                    page.screenshot(path=str(shot_path), full_page=True)
                    # Only check horizontal overflow in landscape
                    ls_eval_data = page.evaluate("""
                    (() => {
                      const viewportWidth = window.innerWidth || 0;
                      const docWidth = Math.max(document.body?.scrollWidth || 0, document.documentElement?.scrollWidth || 0);
                      const horizontalOverflow = docWidth > viewportWidth + 2;
                      return {
                        viewport_width: viewportWidth,
                        document_width: docWidth,
                        horizontal_overflow: horizontalOverflow,
                      };
                    })();
                    """)
                except Exception as e:
                    ls_error = str(e)
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                ls_issues: List[Dict[str, Any]] = []
                if ls_error:
                    ls_issues.append({"severity": "critical", "code": "runtime_error", "title": "Landscape check failed", "details": ls_error})
                if ls_eval_data.get("horizontal_overflow"):
                    ls_issues.append({
                        "severity": "warning",
                        "code": "horizontal_overflow_landscape",
                        "title": "Горизонтальный скролл в ландшафтном режиме",
                        "details": f"Ширина документа {ls_eval_data.get('document_width')}px превышает viewport {ls_eval_data.get('viewport_width')}px в ландшафтной ориентации.",
                    })
                global_issues.extend([{**issue, "device": device.name, "orientation": "landscape"} for issue in ls_issues])
                ls_mobile_friendly = not any(issue["severity"] in ("critical", "warning") for issue in ls_issues)
                results.append(
                    {
                        "device_name": device.name,
                        "category": device.category,
                        "orientation": "landscape",
                        "viewport": landscape_viewport,
                        "pixel_ratio": device.dpr,
                        "status_code": ls_status_code,
                        "final_url": ls_final_url,
                        "load_time_ms": elapsed_ms,
                        "console_errors_count": len(ls_console_errors),
                        "mobile_friendly": ls_mobile_friendly,
                        "issues_count": len(ls_issues),
                        "issues": ls_issues,
                        "screenshot_path": str(shot_path),
                        "screenshot_name": shot_name,
                        "screenshot_url": f"/api/mobile-artifacts/{task_id}/{shot_name}",
                    }
                )

            try:
                browser.close()
            except Exception:
                # Browser may already be terminated; do not fail whole task on teardown.
                pass

        total = len(results)
        mobile_friendly_devices = sum(1 for r in results if r.get("mobile_friendly"))
        avg_load = round(sum(r.get("load_time_ms", 0) for r in results) / total, 2) if total else 0
        summary = {
            "total_devices": total,
            "mobile_friendly_devices": mobile_friendly_devices,
            "non_friendly_devices": total - mobile_friendly_devices,
            "avg_load_time_ms": avg_load,
            "critical_issues": sum(1 for i in global_issues if i.get("severity") == "critical"),
            "warning_issues": sum(1 for i in global_issues if i.get("severity") == "warning"),
            "info_issues": sum(1 for i in global_issues if i.get("severity") == "info"),
        }
        recommendations = []
        if summary["critical_issues"] > 0:
            recommendations.append("В первую очередь устраните критические проблемы мобильной версии: viewport и горизонтальный скролл.")
        if summary["warning_issues"] > 0:
            recommendations.append("Устраните предупреждения: размеры интерактивных элементов, шрифтов и изображений.")
        if summary["critical_issues"] == 0 and summary["warning_issues"] == 0:
            recommendations.append("Критических проблем мобильной версии не обнаружено.")

        _notify(98, "Формирование итогового отчета")
        return {
            "task_type": "mobile_check",
            "url": url,
            "completed_at": datetime.utcnow().isoformat(),
            "results": {
                "engine": "v2",
                "mode": mode,
                "throttle_profile": self.throttle,
                "status_code": prefetch.get("status_code"),
                "final_url": prefetch.get("final_url"),
                "viewport_found": prefetch.get("has_viewport"),
                "viewport_content": prefetch.get("viewport_content"),
                "mobile_friendly": summary["critical_issues"] == 0 and summary["warning_issues"] == 0,
                "score": max(0, 100 - (summary["critical_issues"] * 20 + summary["warning_issues"] * 8)),
                "devices_tested": [r["device_name"] for r in results],
                "device_results": results,
                "issues": global_issues,
                "issues_count": len(global_issues),
                "summary": summary,
                "recommendations": recommendations,
                "artifacts": {
                    "screenshot_dir": str(shot_dir),
                    "screenshots": [r["screenshot_path"] for r in results if r.get("screenshot_path")],
                },
            },
        }
