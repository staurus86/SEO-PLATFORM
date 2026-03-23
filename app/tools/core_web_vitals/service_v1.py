"""Core Web Vitals scanner via Google PageSpeed Insights API."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from app.config import settings


PSI_ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"


def _normalize_http_input(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _normalize_strategy(value: str) -> str:
    token = str(value or "").strip().lower()
    return token if token in {"mobile", "desktop"} else "desktop"


def _extract_psi_error_text(response: requests.Response) -> str:
    try:
        payload = response.json()
        return str((payload.get("error") or {}).get("message") or "").strip()
    except Exception:
        return str(getattr(response, "text", "") or "")[:300].strip()


def _is_retryable_psi_error(status_code: int, error_text: str) -> bool:
    text = str(error_text or "").upper()
    if status_code in (500, 502, 503, 504):
        return True
    if status_code == 400 and ("FAILED_DOCUMENT_REQUEST" in text or "ERR_TIMED_OUT" in text):
        return True
    return False


def _humanize_psi_error(status_code: int, error_text: str) -> str:
    text = str(error_text or "").strip()
    text_upper = text.upper()
    if "FAILED_DOCUMENT_REQUEST" in text_upper or "ERR_TIMED_OUT" in text_upper:
        return (
            "PageSpeed Insights не смог загрузить страницу: целевой сайт не ответил вовремя для Lighthouse. "
            "Проверьте, что URL открывается извне без долгой задержки, сервер не блокирует Google/PageSpeed и страница не зависает на редиректах."
        )
    if status_code == 429:
        return "PageSpeed Insights временно отклонил запрос из-за лимита API. Повторите позже."
    if text:
        return f"PSI API error ({status_code}): {text}"
    return f"PSI API error ({status_code}): unknown error"


def _metric_status(metric: str, value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    if metric == "lcp":
        if value <= 2500:
            return "good"
        if value <= 4000:
            return "needs_improvement"
        return "poor"
    if metric == "inp":
        if value <= 200:
            return "good"
        if value <= 500:
            return "needs_improvement"
        return "poor"
    if metric == "cls":
        if value <= 0.1:
            return "good"
        if value <= 0.25:
            return "needs_improvement"
        return "poor"
    if metric == "fcp":
        if value <= 1800:
            return "good"
        if value <= 3000:
            return "needs_improvement"
        return "poor"
    if metric == "ttfb":
        if value <= 800:
            return "good"
        if value <= 1800:
            return "needs_improvement"
        return "poor"
    if metric == "speed_index":
        if value <= 3400:
            return "good"
        if value <= 5800:
            return "needs_improvement"
        return "poor"
    if metric == "tbt":
        if value <= 200:
            return "good"
        if value <= 600:
            return "needs_improvement"
        return "poor"
    if metric == "tti":
        if value <= 3800:
            return "good"
        if value <= 7300:
            return "needs_improvement"
        return "poor"
    return "unknown"


def _field_metric(metrics: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        payload = metrics.get(key)
        if not isinstance(payload, dict):
            continue
        percentile = payload.get("percentile")
        try:
            if percentile is not None:
                return float(percentile)
        except Exception:
            continue
    return None


def _field_metric_with_category(
    metrics: Dict[str, Any], keys: List[str], *, divisor: float = 1.0
) -> Tuple[Optional[float], Optional[str]]:
    for key in keys:
        payload = metrics.get(key)
        if not isinstance(payload, dict):
            continue
        percentile = payload.get("percentile")
        try:
            if percentile is not None:
                raw = float(percentile)
                value = raw / float(divisor or 1.0)
                category = str(payload.get("category") or "").strip().lower() or None
                return value, category
        except Exception:
            continue
    return None, None


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _as_int(value: Any) -> Optional[int]:
    num = _as_float(value)
    if num is None:
        return None
    try:
        return int(round(num))
    except Exception:
        return None


def _round_or_none(value: Optional[float], digits: int = 1) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except Exception:
        return None


def _median(values: List[float]) -> Optional[float]:
    arr = [float(x) for x in values if x is not None]
    if not arr:
        return None
    arr.sort()
    n = len(arr)
    mid = n // 2
    if n % 2 == 1:
        return arr[mid]
    return (arr[mid - 1] + arr[mid]) / 2.0


def _opportunity_group(audit_id: str) -> str:
    key = str(audit_id or "").lower()
    if any(x in key for x in ("image", "avif", "webp", "next-gen")):
        return "images"
    if any(x in key for x in ("unused-javascript", "legacy-javascript", "bootup-time")):
        return "javascript"
    if any(x in key for x in ("unused-css", "render-blocking", "font-display")):
        return "css_fonts"
    if any(x in key for x in ("server-response-time", "network", "redirect")):
        return "network_server"
    if any(x in key for x in ("mainthread", "long-tasks", "forced-reflow", "dom-size")):
        return "main_thread"
    if "lcp" in key:
        return "lcp"
    return "other"


def _extract_savings(audit_id: str, audit: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    details = audit.get("details") if isinstance(audit, dict) else {}
    details = details if isinstance(details, dict) else {}
    savings_ms = _as_float(details.get("overallSavingsMs"))
    savings_bytes = _as_float(details.get("overallSavingsBytes"))
    items = details.get("items")
    if isinstance(items, list):
        if savings_ms is None:
            ms_candidates = [_as_float(item.get("wastedMs")) for item in items if isinstance(item, dict)]
            ms_values = [v for v in ms_candidates if v is not None and v > 0]
            if ms_values:
                savings_ms = sum(ms_values)
        if savings_bytes is None:
            byte_candidates = [_as_float(item.get("wastedBytes")) for item in items if isinstance(item, dict)]
            byte_values = [v for v in byte_candidates if v is not None and v > 0]
            if byte_values:
                savings_bytes = sum(byte_values)

    if savings_ms is None:
        numeric = _as_float(audit.get("numericValue"))
        display = str(audit.get("displayValue") or "").lower()
        if numeric is not None and numeric > 0 and numeric < 300000 and ("ms" in display or "s" in display):
            savings_ms = numeric

    if savings_bytes is None:
        display = str(audit.get("displayValue") or "").lower()
        matched = re.search(r"([\d,.]+)\s*(ki?b|mi?b|bytes?)", display)
        if matched:
            raw_num = _as_float(str(matched.group(1)).replace(",", "."))
            unit = matched.group(2)
            if raw_num is not None:
                if unit.startswith("m"):
                    savings_bytes = raw_num * 1024 * 1024
                elif unit.startswith("k"):
                    savings_bytes = raw_num * 1024
                else:
                    savings_bytes = raw_num

    if savings_ms is not None:
        savings_ms = max(0.0, float(savings_ms))
    if savings_bytes is not None:
        savings_bytes = max(0.0, float(savings_bytes))
    return savings_ms, savings_bytes


def _build_action_plan(
    *,
    metrics: Dict[str, Any],
    opportunities: List[Dict[str, Any]],
    diagnostics: Dict[str, Any],
    category_scores: Dict[str, Optional[int]],
) -> List[Dict[str, str]]:
    plan: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add(priority: str, area: str, owner: str, action: str, why: str, impact: str, key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        plan.append(
            {
                "priority": priority,
                "area": area,
                "owner": owner,
                "action": action,
                "why": why,
                "expected_impact": impact,
            }
        )

    lcp_status = str(((metrics.get("lcp") or {}).get("status") or "unknown")).lower()
    inp_status = str(((metrics.get("inp") or {}).get("status") or "unknown")).lower()
    cls_status = str(((metrics.get("cls") or {}).get("status") or "unknown")).lower()
    ttfb_status = str(((metrics.get("ttfb") or {}).get("status") or "unknown")).lower()
    tbt_status = str(((metrics.get("tbt") or {}).get("status") or "unknown")).lower()

    if lcp_status in {"poor", "needs_improvement"}:
        add(
            "P1" if lcp_status == "poor" else "P2",
            "LCP",
            "Frontend + Backend",
            "Сократить критический путь рендеринга и ускорить загрузку LCP-ресурса.",
            "LCP выше целевого порога.",
            "Снижение LCP, рост perceived speed и CR.",
            "metric_lcp",
        )
    if inp_status in {"poor", "needs_improvement"} or tbt_status in {"poor", "needs_improvement"}:
        add(
            "P1" if inp_status == "poor" else "P2",
            "INP / Main Thread",
            "Frontend",
            "Снизить JS-нагрузку, разбить long tasks, отложить не-критичный JS.",
            "Высокая задержка отклика/нагрузка main thread.",
            "Улучшение INP, отзывчивости и UX.",
            "metric_inp_tbt",
        )
    if cls_status in {"poor", "needs_improvement"}:
        add(
            "P2",
            "CLS",
            "Frontend",
            "Зафиксировать размеры медиа/баннеров и исключить layout shifts при загрузке.",
            "CLS не в зоне GOOD.",
            "Стабильный layout, меньше срывов взаимодействия.",
            "metric_cls",
        )
    if ttfb_status in {"poor", "needs_improvement"}:
        add(
            "P1" if ttfb_status == "poor" else "P2",
            "TTFB",
            "Backend + DevOps",
            "Оптимизировать backend latency, кэш и CDN edge.",
            "TTFB выше рекомендованного уровня.",
            "Ускорение старта рендера и улучшение LCP/FCP.",
            "metric_ttfb",
        )

    scripts_count = _as_int(diagnostics.get("num_scripts")) or 0
    requests_count = _as_int(diagnostics.get("num_requests")) or 0
    if scripts_count >= 25:
        add(
            "P2",
            "JS Budget",
            "Frontend",
            "Ввести budget на JS-бандлы и удалить неиспользуемые зависимости.",
            f"Высокое число скриптов: {scripts_count}.",
            "Снижение main-thread time и сетевой нагрузки.",
            "diag_scripts",
        )
    if requests_count >= 120:
        add(
            "P3",
            "Network",
            "Frontend + DevOps",
            "Сократить количество запросов: объединение ресурсов, preload, lazy loading.",
            f"Высокое число запросов: {requests_count}.",
            "Улучшение FCP/LCP на медленных сетях.",
            "diag_requests",
        )

    seo_score = category_scores.get("seo")
    if seo_score is not None and seo_score < 85:
        add(
            "P3",
            "SEO Tech",
            "SEO + Frontend",
            "Закрыть технические SEO-аудиты Lighthouse (meta, индексируемость, crawl hints).",
            f"SEO score: {seo_score}.",
            "Повышение технической полноты страницы для индексирования.",
            "cat_seo",
        )

    top_opps = opportunities[:6]
    for opp in top_opps:
        priority = str(opp.get("priority") or "medium").lower()
        pid = str(opp.get("id") or "")
        title = str(opp.get("title") or pid or "Opportunity")
        display = str(opp.get("display_value") or "").strip()
        owner = "Frontend" if str(opp.get("group") or "").lower() in {"javascript", "css_fonts", "images", "main_thread"} else "Backend + Frontend"
        pr = "P1" if priority == "critical" else "P2" if priority == "high" else "P3"
        add(
            pr,
            str(opp.get("group") or "Performance"),
            owner,
            title,
            "Результат PSI указывает на потенциал оптимизации.",
            display or "Улучшение performance score и CWV.",
            f"opp_{pid}",
        )

    order = {"P1": 0, "P2": 1, "P3": 2}
    plan.sort(key=lambda item: (order.get(item.get("priority", "P3"), 3), item.get("area", "")))
    return plan[:12]


def _run_combined_report(
    *,
    url: str,
    timeout: int = 60,
    api_key: Optional[str] = None,
    use_proxy: bool = False,
) -> Dict[str, Any]:
    """Run PSI for both mobile and desktop and return a merged combined report."""
    mobile_result = run_core_web_vitals(
        url=url, strategy="mobile", timeout=timeout, api_key=api_key, use_proxy=use_proxy, combined=False,
    )
    desktop_result = run_core_web_vitals(
        url=url, strategy="desktop", timeout=timeout, api_key=api_key, use_proxy=use_proxy, combined=False,
    )

    mobile_res = mobile_result.get("results") or {}
    desktop_res = desktop_result.get("results") or {}

    def _get_metric(res: Dict[str, Any], metric: str, key: str) -> Any:
        return ((res.get("metrics") or {}).get(metric) or {}).get(key)

    def _get_summary(res: Dict[str, Any], key: str) -> Any:
        return (res.get("summary") or {}).get(key)

    score_m = _get_summary(mobile_res, "performance_score")
    score_d = _get_summary(desktop_res, "performance_score")

    comparison = {
        "performance_mobile": score_m,
        "performance_desktop": score_d,
        "lcp_mobile_ms": _get_metric(mobile_res, "lcp", "lab_value_ms"),
        "lcp_desktop_ms": _get_metric(desktop_res, "lcp", "lab_value_ms"),
        "cls_mobile": _get_metric(mobile_res, "cls", "lab_value"),
        "cls_desktop": _get_metric(desktop_res, "cls", "lab_value"),
        "cwv_grade_mobile": _get_summary(mobile_res, "core_web_vitals_status"),
        "cwv_grade_desktop": _get_summary(desktop_res, "core_web_vitals_status"),
    }

    return {
        "task_type": "core_web_vitals",
        "url": mobile_result.get("url") or desktop_result.get("url") or url,
        "results": {
            "combined": True,
            "mobile": mobile_res,
            "desktop": desktop_res,
            "comparison": comparison,
        },
    }


def run_core_web_vitals(
    *,
    url: str,
    strategy: str = "desktop",
    timeout: int = 60,
    api_key: Optional[str] = None,
    use_proxy: bool = False,
    combined: bool = False,
) -> Dict[str, Any]:
    if combined:
        return _run_combined_report(url=url, timeout=timeout, api_key=api_key, use_proxy=use_proxy)

    normalized_url = _normalize_http_input(url)
    if not normalized_url:
        raise ValueError("Введите корректный URL сайта (http/https или домен).")

    selected_strategy = _normalize_strategy(strategy)
    token = str(api_key or getattr(settings, "PAGESPEED_API_KEY", "") or "").strip()

    params: List[Tuple[str, str]] = [
        ("url", normalized_url),
        ("strategy", selected_strategy),
        ("category", "performance"),
        ("category", "accessibility"),
        ("category", "best-practices"),
        ("category", "seo"),
    ]
    if token:
        params.append(("key", token))

    configured_timeout = int(timeout or getattr(settings, "PAGESPEED_TIMEOUT_SEC", 60) or 60)
    configured_timeout = max(20, configured_timeout)
    max_retries = int(getattr(settings, "PAGESPEED_MAX_RETRIES", 3) or 3)
    max_retries = max(1, min(5, max_retries))

    response = None
    last_exception: Optional[Exception] = None
    retries_used = 0
    proxy_kwargs = {}
    if use_proxy:
        from app.proxy import get_requests_proxies
        _proxies = get_requests_proxies()
        if _proxies:
            proxy_kwargs["proxies"] = _proxies

    for attempt in range(max_retries):
        read_timeout = configured_timeout + (attempt * 15)
        try:
            response = requests.get(
                PSI_ENDPOINT,
                params=params,
                timeout=(10, read_timeout),
                **proxy_kwargs,
            )
            error_text = _extract_psi_error_text(response) if response.status_code != 200 else ""
            if _is_retryable_psi_error(response.status_code, error_text) and attempt < (max_retries - 1):
                retries_used += 1
                time.sleep(min(2.0, 0.35 * (2 ** attempt)))
                continue
            break
        except requests.Timeout as exc:
            last_exception = exc
            if attempt >= (max_retries - 1):
                break
            retries_used += 1
            time.sleep(min(2.0, 0.35 * (2 ** attempt)))
            continue
        except requests.RequestException as exc:
            raise RuntimeError(f"PSI request failed: {exc}") from exc

    if response is None:
        message = str(last_exception) if last_exception else "unknown timeout"
        raise RuntimeError(
            f"PSI request failed after {max_retries} attempts: {message}. "
            "Попробуйте повторить запрос или увеличить PAGESPEED_TIMEOUT_SEC."
        )

    if response.status_code != 200:
        error_text = _extract_psi_error_text(response)
        raise RuntimeError(_humanize_psi_error(response.status_code, error_text))

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"PSI response decode failed: {exc}") from exc

    lighthouse = data.get("lighthouseResult") or {}
    categories = lighthouse.get("categories") or {}
    audits = lighthouse.get("audits") or {}
    performance_score_raw = ((categories.get("performance") or {}).get("score"))
    performance_score = int(round(float(performance_score_raw) * 100)) if performance_score_raw is not None else None
    categories_scores: Dict[str, Optional[int]] = {}
    for category_key in ("performance", "accessibility", "best-practices", "seo"):
        raw_value = _as_float((categories.get(category_key) or {}).get("score"))
        normalized_key = category_key.replace("-", "_")
        categories_scores[normalized_key] = int(round(raw_value * 100)) if raw_value is not None else None

    lcp_lab = ((audits.get("largest-contentful-paint") or {}).get("numericValue"))
    inp_lab = (
        (audits.get("interaction-to-next-paint") or {}).get("numericValue")
        or (audits.get("experimental-interaction-to-next-paint") or {}).get("numericValue")
    )
    cls_lab = ((audits.get("cumulative-layout-shift") or {}).get("numericValue"))
    fcp_lab = ((audits.get("first-contentful-paint") or {}).get("numericValue"))
    ttfb_lab = ((audits.get("server-response-time") or {}).get("numericValue"))
    speed_index_lab = ((audits.get("speed-index") or {}).get("numericValue"))
    tbt_lab = ((audits.get("total-blocking-time") or {}).get("numericValue"))
    tti_lab = ((audits.get("interactive") or {}).get("numericValue"))

    loading_experience = data.get("loadingExperience") or {}
    field_metrics = loading_experience.get("metrics") or {}
    origin_loading_experience = data.get("originLoadingExperience") or {}
    origin_metrics = origin_loading_experience.get("metrics") or {}
    lcp_field, lcp_field_category = _field_metric_with_category(field_metrics, ["LARGEST_CONTENTFUL_PAINT_MS"])
    inp_field, inp_field_category = _field_metric_with_category(
        field_metrics, ["INTERACTION_TO_NEXT_PAINT", "INTERACTION_TO_NEXT_PAINT_MS"]
    )
    cls_field, cls_field_category = _field_metric_with_category(
        field_metrics, ["CUMULATIVE_LAYOUT_SHIFT_SCORE"], divisor=100.0
    )
    lcp_origin, lcp_origin_category = _field_metric_with_category(origin_metrics, ["LARGEST_CONTENTFUL_PAINT_MS"])
    inp_origin, inp_origin_category = _field_metric_with_category(
        origin_metrics, ["INTERACTION_TO_NEXT_PAINT", "INTERACTION_TO_NEXT_PAINT_MS"]
    )
    cls_origin, cls_origin_category = _field_metric_with_category(
        origin_metrics, ["CUMULATIVE_LAYOUT_SHIFT_SCORE"], divisor=100.0
    )

    metrics = {
        "lcp": {
            "lab_value_ms": float(lcp_lab) if lcp_lab is not None else None,
            "field_value_ms": lcp_field,
            "origin_value_ms": lcp_origin,
            "field_category": lcp_field_category,
            "origin_category": lcp_origin_category,
            "status": _metric_status("lcp", lcp_field if lcp_field is not None else (float(lcp_lab) if lcp_lab is not None else None)),
            "thresholds": {"good_max": 2500, "ni_max": 4000},
        },
        "inp": {
            "lab_value_ms": float(inp_lab) if inp_lab is not None else None,
            "field_value_ms": inp_field,
            "origin_value_ms": inp_origin,
            "field_category": inp_field_category,
            "origin_category": inp_origin_category,
            "status": _metric_status("inp", inp_field if inp_field is not None else (float(inp_lab) if inp_lab is not None else None)),
            "thresholds": {"good_max": 200, "ni_max": 500},
        },
        "cls": {
            "lab_value": float(cls_lab) if cls_lab is not None else None,
            "field_value": cls_field,
            "origin_value": cls_origin,
            "field_category": cls_field_category,
            "origin_category": cls_origin_category,
            "status": _metric_status(
                "cls",
                (
                    cls_field if cls_field is not None else (float(cls_lab) if cls_lab is not None else None)
                ),
            ),
            "thresholds": {"good_max": 0.1, "ni_max": 0.25},
        },
        "fcp": {
            "lab_value_ms": float(fcp_lab) if fcp_lab is not None else None,
            "status": _metric_status("fcp", float(fcp_lab) if fcp_lab is not None else None),
            "thresholds": {"good_max": 1800, "ni_max": 3000},
        },
        "ttfb": {
            "lab_value_ms": float(ttfb_lab) if ttfb_lab is not None else None,
            "status": _metric_status("ttfb", float(ttfb_lab) if ttfb_lab is not None else None),
            "thresholds": {"good_max": 800, "ni_max": 1800},
        },
        "speed_index": {
            "lab_value_ms": float(speed_index_lab) if speed_index_lab is not None else None,
            "status": _metric_status("speed_index", float(speed_index_lab) if speed_index_lab is not None else None),
            "thresholds": {"good_max": 3400, "ni_max": 5800},
        },
        "tbt": {
            "lab_value_ms": float(tbt_lab) if tbt_lab is not None else None,
            "status": _metric_status("tbt", float(tbt_lab) if tbt_lab is not None else None),
            "thresholds": {"good_max": 200, "ni_max": 600},
        },
        "tti": {
            "lab_value_ms": float(tti_lab) if tti_lab is not None else None,
            "status": _metric_status("tti", float(tti_lab) if tti_lab is not None else None),
            "thresholds": {"good_max": 3800, "ni_max": 7300},
        },
    }

    cwv_statuses = [metrics["lcp"]["status"], metrics["inp"]["status"], metrics["cls"]["status"]]
    if "poor" in cwv_statuses:
        cwv_grade = "poor"
    elif "needs_improvement" in cwv_statuses:
        cwv_grade = "needs_improvement"
    elif all(item == "good" for item in cwv_statuses):
        cwv_grade = "good"
    else:
        cwv_grade = "unknown"

    opportunities: List[Dict[str, Any]] = []
    for audit_id, audit in audits.items():
        if not isinstance(audit, dict):
            continue
        score = audit.get("score")
        display_mode = str(audit.get("scoreDisplayMode") or "")
        if score is None:
            continue
        try:
            score_value = float(score)
        except Exception:
            continue
        if display_mode not in {"metricSavings", "binary", "numeric", "informative"}:
            continue
        if score_value >= 0.95:
            continue
        savings_ms, savings_bytes = _extract_savings(audit_id, audit)
        impact_score = ((1.0 - score_value) * 120.0)
        if savings_ms is not None:
            impact_score += min(35.0, savings_ms / 120.0)
        if savings_bytes is not None:
            impact_score += min(25.0, savings_bytes / (1024.0 * 60.0))
        priority = "medium"
        if impact_score >= 35 or score_value <= 0.35:
            priority = "critical"
        elif impact_score >= 20 or score_value <= 0.6:
            priority = "high"
        opportunities.append(
            {
                "id": audit_id,
                "title": str(audit.get("title") or audit_id),
                "score": round(score_value, 3),
                "display_value": str(audit.get("displayValue") or ""),
                "description": str(audit.get("description") or ""),
                "group": _opportunity_group(audit_id),
                "priority": priority,
                "impact_score": round(impact_score, 2),
                "savings_ms": _round_or_none(savings_ms, 1),
                "savings_bytes": _as_int(savings_bytes),
                "savings_kib": _round_or_none((savings_bytes / 1024.0) if savings_bytes is not None else None, 1),
            }
        )
    priority_rank = {"critical": 0, "high": 1, "medium": 2}
    opportunities.sort(
        key=lambda item: (
            priority_rank.get(str(item.get("priority") or "medium"), 3),
            -float(item.get("impact_score") or 0.0),
            float(item.get("score") or 1.0),
        )
    )
    opportunities = opportunities[:15]

    resource_rows: List[Dict[str, Any]] = []
    total_transfer = 0.0
    total_requests = 0
    resource_items = (((audits.get("resource-summary") or {}).get("details") or {}).get("items") or [])
    if isinstance(resource_items, list):
        for row in resource_items:
            if not isinstance(row, dict):
                continue
            transfer = _as_float(row.get("transferSize")) or 0.0
            requests_count = _as_int(row.get("requestCount")) or 0
            resource_type = str(row.get("resourceType") or "other")
            total_transfer += max(0.0, transfer)
            total_requests += max(0, requests_count)
            resource_rows.append(
                {
                    "resource_type": resource_type,
                    "request_count": requests_count,
                    "transfer_bytes": int(round(transfer)),
                    "transfer_kib": _round_or_none(transfer / 1024.0, 1),
                }
            )
    resource_rows.sort(key=lambda item: float(item.get("transfer_bytes") or 0), reverse=True)

    diagnostics_payload = (((audits.get("diagnostics") or {}).get("details") or {}).get("items") or [])
    diagnostics_item = diagnostics_payload[0] if isinstance(diagnostics_payload, list) and diagnostics_payload else {}
    diagnostics = {
        "num_requests": _as_int(diagnostics_item.get("numRequests")),
        "num_scripts": _as_int(diagnostics_item.get("numScripts")),
        "num_stylesheets": _as_int(diagnostics_item.get("numStylesheets")),
        "num_tasks": _as_int(diagnostics_item.get("numTasks")),
        "num_tasks_over_50ms": _as_int(diagnostics_item.get("numTasksOver50ms")),
        "num_tasks_over_100ms": _as_int(diagnostics_item.get("numTasksOver100ms")),
        "max_rtt_ms": _as_int(diagnostics_item.get("maxRtt")),
        "total_byte_weight_kib": _round_or_none(
            ((_as_float(diagnostics_item.get("totalByteWeight")) or 0.0) / 1024.0), 1
        ),
    }

    third_party_rows: List[Dict[str, Any]] = []
    third_party_items = (((audits.get("third-party-summary") or {}).get("details") or {}).get("items") or [])
    if isinstance(third_party_items, list):
        for item in third_party_items:
            if not isinstance(item, dict):
                continue
            transfer = _as_float(item.get("transferSize")) or 0.0
            main_thread = _as_float(item.get("mainThreadTime")) or 0.0
            blocking = _as_float(item.get("blockingTime")) or 0.0
            if transfer <= 0 and main_thread <= 0 and blocking <= 0:
                continue
            third_party_rows.append(
                {
                    "entity": str(item.get("entity") or "third-party"),
                    "transfer_kib": _round_or_none(transfer / 1024.0, 1),
                    "main_thread_ms": _round_or_none(main_thread, 1),
                    "blocking_ms": _round_or_none(blocking, 1),
                }
            )
    third_party_rows.sort(
        key=lambda item: (float(item.get("main_thread_ms") or 0.0), float(item.get("transfer_kib") or 0.0)),
        reverse=True,
    )
    third_party_rows = third_party_rows[:10]

    action_plan = _build_action_plan(
        metrics=metrics,
        opportunities=opportunities,
        diagnostics=diagnostics,
        category_scores=categories_scores,
    )

    recommendations: List[str] = []
    for row in action_plan[:10]:
        text = f"{row.get('priority')}: {row.get('action')}"
        impact = str(row.get("expected_impact") or "").strip()
        if impact:
            text += f" ({impact})"
        recommendations.append(text)
    if not recommendations:
        recommendations = [
            "Поддерживайте текущий уровень производительности: регулярный аудит CWV после релизов.",
            "Проверьте кэширование статики и оптимизацию изображений для стабильного LCP.",
        ]

    cwv_penalty = 0.0
    if cwv_grade == "poor":
        cwv_penalty = 24.0
    elif cwv_grade == "needs_improvement":
        cwv_penalty = 10.0
    secondary_scores = [x for x in categories_scores.values() if x is not None and isinstance(x, int)]
    secondary_avg = (sum(secondary_scores) / len(secondary_scores)) if secondary_scores else float(performance_score or 0)
    perf_value = float(performance_score or 0)
    health_index = int(max(0, min(100, round((perf_value * 0.7) + (secondary_avg * 0.3) - cwv_penalty))))
    risk_level = "low"
    if cwv_grade == "poor" or health_index < 55:
        risk_level = "high"
    elif cwv_grade == "needs_improvement" or health_index < 75:
        risk_level = "medium"
    grade = "A" if health_index >= 90 else "B" if health_index >= 75 else "C" if health_index >= 60 else "D" if health_index >= 45 else "E"

    dominant_issues: List[str] = []
    for opp in opportunities[:6]:
        label = str(opp.get("title") or opp.get("id") or "").strip()
        if not label or label in dominant_issues:
            continue
        dominant_issues.append(label)

    fetched_at = datetime.now(timezone.utc).isoformat()
    return {
        "task_type": "core_web_vitals",
        "url": normalized_url,
        "results": {
            "strategy": selected_strategy,
            "source": "pagespeed_insights_api",
            "mode": "single",
            "summary": {
                "performance_score": performance_score,
                "core_web_vitals_status": cwv_grade,
                "passed_metrics": sum(1 for key in ("lcp", "inp", "cls") if metrics[key].get("status") == "good"),
                "total_core_metrics": 3,
                "health_index": health_index,
                "risk_level": risk_level,
                "grade": grade,
            },
            "categories": categories_scores,
            "metrics": metrics,
            "field_data": {
                "is_available": bool(field_metrics),
                "overall_category": str(loading_experience.get("overall_category") or "").lower() or None,
                "origin_fallback": bool(origin_metrics),
                "origin_overall_category": str(origin_loading_experience.get("overall_category") or "").lower() or None,
                "origin_metrics": {
                    "lcp_ms": _round_or_none(lcp_origin, 1),
                    "lcp_category": lcp_origin_category,
                    "inp_ms": _round_or_none(inp_origin, 1),
                    "inp_category": inp_origin_category,
                    "cls": _round_or_none(cls_origin, 3),
                    "cls_category": cls_origin_category,
                },
            },
            "resource_summary": {
                "total_requests": total_requests,
                "total_transfer_kib": _round_or_none(total_transfer / 1024.0, 1),
                "by_type": resource_rows,
            },
            "diagnostics": diagnostics,
            "third_party": {
                "top_entities": third_party_rows,
            },
            "opportunities": opportunities,
            "action_plan": action_plan,
            "recommendations": recommendations,
            "analysis": {
                "risk_level": risk_level,
                "grade": grade,
                "health_index": health_index,
                "dominant_issues": dominant_issues,
                "score_stats": {
                    "performance_score": performance_score,
                    "categories_avg": _round_or_none(_as_float(secondary_avg), 1),
                    "opportunities_count": len(opportunities),
                    "critical_opportunities": sum(1 for x in opportunities if str(x.get("priority")) == "critical"),
                    "high_opportunities": sum(1 for x in opportunities if str(x.get("priority")) == "high"),
                },
            },
            "page_context": {
                "final_url": str(lighthouse.get("finalDisplayedUrl") or data.get("id") or normalized_url),
                "requested_url": normalized_url,
                "fetch_time": str(lighthouse.get("fetchTime") or ""),
                "runtime_error": str(lighthouse.get("runtimeError", {}).get("message") or ""),
                "lighthouse_version": str(lighthouse.get("lighthouseVersion") or ""),
                "user_agent": str(lighthouse.get("userAgent") or ""),
                "timing_total_ms": _round_or_none(_as_float((lighthouse.get("timing") or {}).get("total")), 1),
                "stack_packs": [
                    {
                        "id": str(item.get("id") or ""),
                        "title": str(item.get("title") or ""),
                    }
                    for item in (lighthouse.get("stackPacks") or [])
                    if isinstance(item, dict)
                ],
            },
            "api": {
                "has_key": bool(token),
                "endpoint": PSI_ENDPOINT,
                "retries_used": retries_used,
                "timeout_sec": configured_timeout,
            },
            "checked_at": fetched_at,
        },
    }
