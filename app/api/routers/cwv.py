"""
Core Web Vitals router.
"""
import math
import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import field_validator

from app.validators import URLModel, normalize_http_input as _normalize_http_input
from app.api.routers._task_store import create_task_pending, update_task_state

router = APIRouter(tags=["SEO Tools"])


def check_core_web_vitals(url: str, strategy: str = "desktop", use_proxy: bool = False, combined: bool = False) -> Dict[str, Any]:
    from app.tools.core_web_vitals import run_core_web_vitals

    return run_core_web_vitals(url=url, strategy=strategy, use_proxy=use_proxy, combined=combined)


class CoreWebVitalsRequest(URLModel):
    url: Optional[str] = ""
    strategy: Optional[str] = "desktop"
    scan_mode: Optional[str] = "single"
    batch_urls: Optional[List[str]] = None
    competitor_mode: bool = False
    combined: bool = False
    use_proxy: bool = False

    @field_validator("combined", mode="before")
    @classmethod
    def _normalize_combined(cls, value):
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        return token in {"1", "true", "yes", "on"}

    @field_validator("strategy", mode="before")
    @classmethod
    def _normalize_strategy(cls, value):
        token = str(value or "desktop").strip().lower()
        return token if token in {"mobile", "desktop"} else "desktop"

    @field_validator("scan_mode", mode="before")
    @classmethod
    def _normalize_scan_mode(cls, value):
        token = str(value or "single").strip().lower()
        return token if token in {"single", "batch"} else "single"

    @field_validator("batch_urls", mode="before")
    @classmethod
    def _normalize_batch_urls(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [x.strip() for x in re.split(r"[\r\n,;]+", value) if x.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("competitor_mode", mode="before")
    @classmethod
    def _normalize_competitor_mode(cls, value):
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        return token in {"1", "true", "yes", "on"}

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value):
        if value in (None, ""):
            return ""
        normalized = _normalize_http_input(str(value or ""))
        if not normalized:
            raise ValueError("Введите корректный URL сайта (домен или http/https URL).")
        return normalized

    @field_validator("batch_urls", mode="after")
    @classmethod
    def _normalize_batch_urls_http(cls, value):
        normalized_urls: List[str] = []
        for item in value or []:
            normalized = _normalize_http_input(str(item or ""))
            if not normalized:
                raise ValueError(f"Некорректный URL в batch-списке: {item}")
            normalized_urls.append(normalized)
        return normalized_urls


def _build_core_web_vitals_batch_result(
    *,
    strategy: str,
    source: str,
    sites: List[Dict[str, Any]],
) -> Dict[str, Any]:
    def _as_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
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

    score_values: List[float] = []
    metric_lcp_values: List[float] = []
    metric_inp_values: List[float] = []
    metric_cls_values: List[float] = []
    category_acc: Dict[str, List[float]] = {
        "performance": [],
        "accessibility": [],
        "best_practices": [],
        "seo": [],
    }
    status_counts = {"good": 0, "needs_improvement": 0, "poor": 0, "unknown": 0}
    top_recommendations: Dict[str, int] = {}
    common_opportunities: Dict[str, Dict[str, Any]] = {}
    plan_counts: Dict[str, int] = {}
    risk_counts = {"low": 0, "medium": 0, "high": 0}
    failed_urls: List[Dict[str, str]] = []
    successful_urls = 0

    for site in sites:
        if str(site.get("status") or "").lower() != "success":
            failed_urls.append(
                {
                    "url": str(site.get("url") or ""),
                    "error": str(site.get("error") or "Scan failed"),
                }
            )
            continue

        successful_urls += 1
        summary = site.get("summary") or {}
        status = str(summary.get("core_web_vitals_status") or "unknown").strip().lower()
        if status not in status_counts:
            status = "unknown"
        status_counts[status] += 1

        score_raw = summary.get("performance_score")
        try:
            if score_raw is not None:
                score_values.append(float(score_raw))
        except Exception:
            pass

        metrics = site.get("metrics") or {}
        lcp = _as_float(((metrics.get("lcp") or {}).get("field_value_ms")))
        if lcp is None:
            lcp = _as_float(((metrics.get("lcp") or {}).get("lab_value_ms")))
        inp = _as_float(((metrics.get("inp") or {}).get("field_value_ms")))
        if inp is None:
            inp = _as_float(((metrics.get("inp") or {}).get("lab_value_ms")))
        cls = _as_float(((metrics.get("cls") or {}).get("field_value")))
        if cls is None:
            cls = _as_float(((metrics.get("cls") or {}).get("lab_value")))
        if lcp is not None:
            metric_lcp_values.append(lcp)
        if inp is not None:
            metric_inp_values.append(inp)
        if cls is not None:
            metric_cls_values.append(cls)

        categories = site.get("categories") or {}
        for key in category_acc.keys():
            cat_val = _as_float(categories.get(key))
            if cat_val is not None:
                category_acc[key].append(cat_val)

        analysis = site.get("analysis") or {}
        risk_level = str(analysis.get("risk_level") or "").lower()
        if risk_level in risk_counts:
            risk_counts[risk_level] += 1

        for rec in (site.get("recommendations") or []):
            text = str(rec or "").strip()
            if not text:
                continue
            top_recommendations[text] = int(top_recommendations.get(text, 0) or 0) + 1

        for plan_item in (site.get("action_plan") or []):
            if not isinstance(plan_item, dict):
                continue
            title = str(plan_item.get("action") or "").strip()
            if not title:
                continue
            plan_counts[title] = int(plan_counts.get(title, 0) or 0) + 1

        for opp in (site.get("opportunities") or []):
            if not isinstance(opp, dict):
                continue
            opp_id = str(opp.get("id") or "")
            opp_title = str(opp.get("title") or opp_id or "").strip()
            if not opp_title:
                continue
            key = opp_id or opp_title
            bucket = common_opportunities.setdefault(
                key,
                {
                    "id": opp_id,
                    "title": opp_title,
                    "count": 0,
                    "critical_count": 0,
                    "high_count": 0,
                    "total_savings_ms": 0.0,
                    "total_savings_bytes": 0.0,
                    "group": str(opp.get("group") or ""),
                },
            )
            bucket["count"] = int(bucket.get("count") or 0) + 1
            priority = str(opp.get("priority") or "").lower()
            if priority == "critical":
                bucket["critical_count"] = int(bucket.get("critical_count") or 0) + 1
            if priority == "high":
                bucket["high_count"] = int(bucket.get("high_count") or 0) + 1
            ms = _as_float(opp.get("savings_ms"))
            b = _as_float(opp.get("savings_bytes"))
            if ms is not None:
                bucket["total_savings_ms"] = float(bucket.get("total_savings_ms") or 0.0) + ms
            if b is not None:
                bucket["total_savings_bytes"] = float(bucket.get("total_savings_bytes") or 0.0) + b

    if status_counts["poor"] > 0:
        batch_status = "poor"
    elif status_counts["needs_improvement"] > 0:
        batch_status = "needs_improvement"
    elif successful_urls > 0 and status_counts["good"] == successful_urls:
        batch_status = "good"
    else:
        batch_status = "unknown"

    recommendations = []
    for text, count in sorted(top_recommendations.items(), key=lambda item: (-item[1], item[0].lower()))[:8]:
        if count > 1:
            recommendations.append(f"{text} (повторяется на {count} URL)")
        else:
            recommendations.append(text)

    avg_score = round(sum(score_values) / len(score_values), 1) if score_values else None
    median_score = round(_median(score_values), 1) if score_values else None
    min_score = round(min(score_values), 1) if score_values else None
    max_score = round(max(score_values), 1) if score_values else None
    total_urls = len(sites)
    failed_count = len(failed_urls)
    common_opportunities_rows: List[Dict[str, Any]] = []
    for value in common_opportunities.values():
        ms_total = float(value.get("total_savings_ms") or 0.0)
        bytes_total = float(value.get("total_savings_bytes") or 0.0)
        common_opportunities_rows.append(
            {
                "id": value.get("id") or "",
                "title": value.get("title") or "",
                "group": value.get("group") or "",
                "count": int(value.get("count") or 0),
                "critical_count": int(value.get("critical_count") or 0),
                "high_count": int(value.get("high_count") or 0),
                "total_savings_ms": round(ms_total, 1),
                "total_savings_kib": round(bytes_total / 1024.0, 1),
            }
        )
    common_opportunities_rows.sort(
        key=lambda item: (
            -int(item.get("count") or 0),
            -int(item.get("critical_count") or 0),
            -float(item.get("total_savings_ms") or 0.0),
            -float(item.get("total_savings_kib") or 0.0),
        )
    )
    common_opportunities_rows = common_opportunities_rows[:12]

    priority_urls = []
    for site in sites:
        if str(site.get("status") or "").lower() != "success":
            priority_urls.append(
                {
                    "url": str(site.get("url") or ""),
                    "status": "error",
                    "score": None,
                    "reason": str(site.get("error") or "scan error"),
                }
            )
            continue
        site_summary = site.get("summary") or {}
        site_score = _as_float(site_summary.get("performance_score"))
        cwv = str(site_summary.get("core_web_vitals_status") or "unknown").lower()
        top_issue = ""
        opps = site.get("opportunities") or []
        if isinstance(opps, list) and opps:
            top_issue = str((opps[0] or {}).get("title") or "")
        reason = top_issue or f"CWV: {cwv}"
        priority_urls.append(
            {
                "url": str(site.get("url") or ""),
                "status": cwv,
                "score": site_score,
                "reason": reason,
            }
        )
    priority_urls.sort(
        key=lambda item: (
            0 if str(item.get("status") or "") == "error" else 1,
            0 if str(item.get("status") or "") == "poor" else 1 if str(item.get("status") or "") == "needs_improvement" else 2,
            float(item.get("score") if item.get("score") is not None else 101.0),
        )
    )
    priority_urls = priority_urls[:8]

    batch_action_plan = []
    for action, count in sorted(plan_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:8]:
        batch_action_plan.append(
            {
                "action": action,
                "affected_urls": count,
                "priority": "P1" if count >= max(2, math.ceil(total_urls * 0.5)) else "P2",
            }
        )

    return {
        "mode": "batch",
        "strategy": strategy,
        "source": source,
        "summary": {
            "total_urls": total_urls,
            "successful_urls": successful_urls,
            "failed_urls": failed_count,
            "average_performance_score": avg_score,
            "median_performance_score": median_score,
            "min_performance_score": min_score,
            "max_performance_score": max_score,
            "core_web_vitals_status": batch_status,
            "status_counts": status_counts,
            "metrics_average": {
                "lcp_ms": round(sum(metric_lcp_values) / len(metric_lcp_values), 1) if metric_lcp_values else None,
                "inp_ms": round(sum(metric_inp_values) / len(metric_inp_values), 1) if metric_inp_values else None,
                "cls": round(sum(metric_cls_values) / len(metric_cls_values), 3) if metric_cls_values else None,
            },
            "categories_average": {
                "performance": round(sum(category_acc["performance"]) / len(category_acc["performance"]), 1)
                if category_acc["performance"]
                else None,
                "accessibility": round(sum(category_acc["accessibility"]) / len(category_acc["accessibility"]), 1)
                if category_acc["accessibility"]
                else None,
                "best_practices": round(sum(category_acc["best_practices"]) / len(category_acc["best_practices"]), 1)
                if category_acc["best_practices"]
                else None,
                "seo": round(sum(category_acc["seo"]) / len(category_acc["seo"]), 1) if category_acc["seo"] else None,
            },
            "risk_counts": risk_counts,
        },
        "sites": sites,
        "failed_urls": failed_urls,
        "recommendations": recommendations,
        "common_opportunities": common_opportunities_rows,
        "priority_urls": priority_urls,
        "action_plan": batch_action_plan,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_core_web_vitals_competitor_result(
    *,
    strategy: str,
    source: str,
    sites: List[Dict[str, Any]],
) -> Dict[str, Any]:
    base = _build_core_web_vitals_batch_result(strategy=strategy, source=source, sites=sites)

    def _as_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
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

    def _site_is_success(site: Dict[str, Any]) -> bool:
        return str(site.get("status") or "").strip().lower() == "success"

    def _extract_metric(site: Dict[str, Any], metric_key: str, field_key: str, lab_key: str) -> Optional[float]:
        metrics = site.get("metrics") or {}
        payload = metrics.get(metric_key) or {}
        value = _as_float(payload.get(field_key))
        if value is None:
            value = _as_float(payload.get(lab_key))
        return value

    def _extract_site_snapshot(site: Dict[str, Any]) -> Dict[str, Any]:
        summary = site.get("summary") or {}
        opportunities = site.get("opportunities") or []
        recommendations = site.get("recommendations") or []
        top_focus = ""
        if isinstance(opportunities, list) and opportunities and isinstance(opportunities[0], dict):
            top_focus = str((opportunities[0] or {}).get("title") or "")
        if not top_focus and isinstance(recommendations, list) and recommendations:
            top_focus = str(recommendations[0] or "")
        return {
            "url": str(site.get("url") or ""),
            "status": str(site.get("status") or "error").lower(),
            "score": _as_float(summary.get("performance_score")),
            "cwv_status": str(summary.get("core_web_vitals_status") or "unknown").lower(),
            "lcp_ms": _extract_metric(site, "lcp", "field_value_ms", "lab_value_ms"),
            "inp_ms": _extract_metric(site, "inp", "field_value_ms", "lab_value_ms"),
            "cls": _extract_metric(site, "cls", "field_value", "lab_value"),
            "risk_level": str((site.get("analysis") or {}).get("risk_level") or "unknown").lower(),
            "top_focus": top_focus,
            "error": str(site.get("error") or ""),
        }

    def _rank_key(item: Dict[str, Any]) -> Tuple[int, float, float, float, float]:
        status_rank = {
            "good": 0,
            "needs_improvement": 1,
            "poor": 2,
            "unknown": 3,
        }.get(str(item.get("cwv_status") or "unknown"), 3)
        score = float(item.get("score") if item.get("score") is not None else -1.0)
        lcp = float(item.get("lcp_ms") if item.get("lcp_ms") is not None else 1e12)
        inp = float(item.get("inp_ms") if item.get("inp_ms") is not None else 1e12)
        cls = float(item.get("cls") if item.get("cls") is not None else 1e12)
        return (status_rank, -score, lcp, inp, cls)

    snapshots = [_extract_site_snapshot(site) for site in sites]
    primary = snapshots[0] if snapshots else {
        "url": "",
        "status": "error",
        "score": None,
        "cwv_status": "unknown",
        "lcp_ms": None,
        "inp_ms": None,
        "cls": None,
        "risk_level": "unknown",
        "top_focus": "",
        "error": "missing primary site",
    }
    competitors = snapshots[1:] if len(snapshots) > 1 else []

    successful = [item for item in snapshots if item.get("status") == "success"]
    successful_competitors = [item for item in competitors if item.get("status") == "success"]
    failed_competitors = [item for item in competitors if item.get("status") != "success"]

    ranked_success = sorted(successful, key=_rank_key)
    leader = ranked_success[0] if ranked_success else None
    primary_rank: Optional[int] = None
    for idx, item in enumerate(ranked_success, start=1):
        if str(item.get("url") or "") == str(primary.get("url") or ""):
            primary_rank = idx
            break

    peer_pool = successful_competitors if successful_competitors else [item for item in successful if item.get("url") != primary.get("url")]
    peer_scores = [item.get("score") for item in peer_pool if item.get("score") is not None]
    peer_lcp = [item.get("lcp_ms") for item in peer_pool if item.get("lcp_ms") is not None]
    peer_inp = [item.get("inp_ms") for item in peer_pool if item.get("inp_ms") is not None]
    peer_cls = [item.get("cls") for item in peer_pool if item.get("cls") is not None]

    benchmark = {
        "primary_url": primary.get("url"),
        "primary_rank": primary_rank,
        "total_ranked": len(ranked_success),
        "market_leader_url": (leader or {}).get("url"),
        "market_leader_score": round(float((leader or {}).get("score")), 1) if leader and leader.get("score") is not None else None,
        "competitor_median_score": round(_median(peer_scores), 1) if peer_scores else None,
        "competitor_median_lcp_ms": round(_median(peer_lcp), 1) if peer_lcp else None,
        "competitor_median_inp_ms": round(_median(peer_inp), 1) if peer_inp else None,
        "competitor_median_cls": round(_median(peer_cls), 3) if peer_cls else None,
    }

    primary_score = _as_float(primary.get("score"))
    primary_lcp = _as_float(primary.get("lcp_ms"))
    primary_inp = _as_float(primary.get("inp_ms"))
    primary_cls = _as_float(primary.get("cls"))

    comparison_rows: List[Dict[str, Any]] = []
    for item in competitors:
        score = _as_float(item.get("score"))
        lcp = _as_float(item.get("lcp_ms"))
        inp = _as_float(item.get("inp_ms"))
        cls = _as_float(item.get("cls"))
        comparison_rows.append(
            {
                "url": item.get("url"),
                "status": item.get("status"),
                "cwv_status": item.get("cwv_status"),
                "score": round(score, 1) if score is not None else None,
                "score_delta_vs_primary": round(score - primary_score, 1) if score is not None and primary_score is not None else None,
                "lcp_ms": round(lcp, 1) if lcp is not None else None,
                "lcp_delta_ms_vs_primary": round(lcp - primary_lcp, 1) if lcp is not None and primary_lcp is not None else None,
                "inp_ms": round(inp, 1) if inp is not None else None,
                "inp_delta_ms_vs_primary": round(inp - primary_inp, 1) if inp is not None and primary_inp is not None else None,
                "cls": round(cls, 3) if cls is not None else None,
                "cls_delta_vs_primary": round(cls - primary_cls, 3) if cls is not None and primary_cls is not None else None,
                "top_focus": item.get("top_focus") or "",
                "risk_level": item.get("risk_level") or "unknown",
                "error": item.get("error") or "",
            }
        )

    gaps_for_primary: List[str] = []
    strengths_of_primary: List[str] = []

    median_score = _as_float(benchmark.get("competitor_median_score"))
    median_lcp = _as_float(benchmark.get("competitor_median_lcp_ms"))
    median_inp = _as_float(benchmark.get("competitor_median_inp_ms"))
    median_cls = _as_float(benchmark.get("competitor_median_cls"))

    if primary.get("status") != "success":
        gaps_for_primary.append("Primary URL завершился с ошибкой сканирования; сравнение с рынком неполное.")
    else:
        if median_score is not None and primary_score is not None:
            delta = round(primary_score - median_score, 1)
            if delta < 0:
                gaps_for_primary.append(f"Performance score ниже медианы конкурентов на {abs(delta)} п.")
            elif delta > 0:
                strengths_of_primary.append(f"Performance score выше медианы конкурентов на {delta} п.")
        if median_lcp is not None and primary_lcp is not None:
            delta = round(primary_lcp - median_lcp, 1)
            if delta > 0:
                gaps_for_primary.append(f"LCP медленнее медианы конкурентов на {delta} ms.")
            elif delta < 0:
                strengths_of_primary.append(f"LCP быстрее медианы конкурентов на {abs(delta)} ms.")
        if median_inp is not None and primary_inp is not None:
            delta = round(primary_inp - median_inp, 1)
            if delta > 0:
                gaps_for_primary.append(f"INP хуже медианы конкурентов на {delta} ms.")
            elif delta < 0:
                strengths_of_primary.append(f"INP лучше медианы конкурентов на {abs(delta)} ms.")
        if median_cls is not None and primary_cls is not None:
            delta = round(primary_cls - median_cls, 3)
            if delta > 0:
                gaps_for_primary.append(f"CLS выше (хуже) медианы конкурентов на {delta}.")
            elif delta < 0:
                strengths_of_primary.append(f"CLS ниже (лучше) медианы конкурентов на {abs(delta)}.")
        if str(primary.get("cwv_status") or "unknown") != "good" and any(
            str(item.get("cwv_status") or "unknown") == "good" for item in successful_competitors
        ):
            gaps_for_primary.append("Primary URL не в статусе GOOD по CWV, при этом у конкурентов есть GOOD-результаты.")

    competitor_common: Dict[str, Dict[str, Any]] = {}
    for original_site in sites[1:]:
        if not _site_is_success(original_site):
            continue
        for opp in (original_site.get("opportunities") or []):
            if not isinstance(opp, dict):
                continue
            title = str(opp.get("title") or opp.get("id") or "").strip()
            if not title:
                continue
            key = str(opp.get("id") or title)
            bucket = competitor_common.setdefault(
                key,
                {
                    "id": str(opp.get("id") or ""),
                    "title": title,
                    "group": str(opp.get("group") or ""),
                    "count": 0,
                },
            )
            bucket["count"] = int(bucket.get("count") or 0) + 1
    competitor_common_rows = list(competitor_common.values())
    competitor_common_rows.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("title") or "")))
    competitor_common_rows = competitor_common_rows[:8]

    primary_plan: List[Dict[str, Any]] = []
    if sites and _site_is_success(sites[0]):
        plan_raw = sites[0].get("action_plan") or []
        if isinstance(plan_raw, list):
            primary_plan = [item for item in plan_raw if isinstance(item, dict)][:12]

    recommendations: List[str] = []
    recommendations.extend(gaps_for_primary[:5])
    recommendations.extend(str(item) for item in strengths_of_primary[:3])
    for item in competitor_common_rows[:3]:
        recommendations.append(
            f"У конкурентов часто встречается: {item.get('title')} ({item.get('count')} URL)."
        )
    if not recommendations:
        recommendations.extend(base.get("recommendations") or [])
    recommendations = recommendations[:12]

    summary = dict(base.get("summary") or {})
    summary.update(
        {
            "analysis_profile": "competitor",
            "primary_url": primary.get("url"),
            "primary_status": primary.get("status"),
            "primary_score": round(primary_score, 1) if primary_score is not None else None,
            "primary_cwv_status": primary.get("cwv_status"),
            "primary_rank": f"{primary_rank}/{len(ranked_success)}" if primary_rank is not None and ranked_success else None,
            "market_leader_url": benchmark.get("market_leader_url"),
            "market_leader_score": benchmark.get("market_leader_score"),
            "competitors_total": max(0, len(sites) - 1),
            "competitors_success": len(successful_competitors),
            "competitors_failed": len(failed_competitors),
        }
    )

    primary_payload: Dict[str, Any] = {}
    if sites:
        primary_raw = sites[0]
        primary_payload = {
            "url": str(primary_raw.get("url") or ""),
            "status": str(primary_raw.get("status") or "error"),
            "summary": primary_raw.get("summary") or {},
            "metrics": primary_raw.get("metrics") or {},
            "categories": primary_raw.get("categories") or {},
            "diagnostics": primary_raw.get("diagnostics") or {},
            "analysis": primary_raw.get("analysis") or {},
            "opportunities": primary_raw.get("opportunities") or [],
            "recommendations": primary_raw.get("recommendations") or [],
            "action_plan": primary_raw.get("action_plan") or [],
            "error": str(primary_raw.get("error") or ""),
        }

    return {
        "mode": "competitor",
        "strategy": strategy,
        "source": source,
        "summary": summary,
        "primary": primary_payload,
        "competitors": sites[1:] if len(sites) > 1 else [],
        "comparison_rows": comparison_rows,
        "benchmark": benchmark,
        "gaps_for_primary": gaps_for_primary,
        "strengths_of_primary": strengths_of_primary,
        "common_opportunities": competitor_common_rows,
        "sites": sites,
        "failed_urls": base.get("failed_urls") or [],
        "recommendations": recommendations,
        "action_plan": primary_plan if primary_plan else (base.get("action_plan") or []),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/tasks/core-web-vitals")
async def create_core_web_vitals(data: CoreWebVitalsRequest, background_tasks: BackgroundTasks, request: Request):
    """Run Core Web Vitals scan via PageSpeed Insights API (single, batch, competitor compare)."""
    strategy = str(data.strategy or "desktop").strip().lower()
    if strategy not in {"mobile", "desktop"}:
        strategy = "desktop"
    scan_mode = str(data.scan_mode or "single").strip().lower()
    if scan_mode not in {"single", "batch"}:
        scan_mode = "single"
    competitor_mode = bool(getattr(data, "competitor_mode", False))
    if competitor_mode:
        scan_mode = "batch"
    from app.core.scan_token import capture_scan_token_from_request, scan_token_context

    scan_token = capture_scan_token_from_request(request)

    max_batch_urls = 999
    raw_batch_urls = [str(item or "").strip() for item in (data.batch_urls or []) if str(item or "").strip()]
    if scan_mode == "batch" and not raw_batch_urls and str(data.url or "").strip():
        raw_batch_urls = [str(data.url).strip()]

    if scan_mode == "batch":
        if not raw_batch_urls:
            raise HTTPException(status_code=422, detail="Добавьте хотя бы один URL для batch Core Web Vitals сканирования.")
        if len(raw_batch_urls) > max_batch_urls:
            raise HTTPException(status_code=422, detail=f"Лимит batch Core Web Vitals: максимум {max_batch_urls} URL.")

        normalized_urls: List[str] = []
        seen = set()
        invalid_urls: List[str] = []
        for raw_value in raw_batch_urls:
            normalized = _normalize_http_input(raw_value)
            if not normalized:
                invalid_urls.append(raw_value)
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            normalized_urls.append(normalized)

        if invalid_urls:
            preview = ", ".join(invalid_urls[:3])
            raise HTTPException(
                status_code=422,
                detail=f"Некорректные URL в batch-списке: {preview}",
            )
        if not normalized_urls:
            raise HTTPException(status_code=422, detail="Не удалось подготовить список URL для batch сканирования.")
        if len(normalized_urls) > max_batch_urls:
            raise HTTPException(status_code=422, detail=f"Лимит batch Core Web Vitals: максимум {max_batch_urls} URL.")
        if competitor_mode and len(normalized_urls) < 2:
            raise HTTPException(
                status_code=422,
                detail="Для режима анализа конкурентов укажите минимум 2 URL: первый — ваш сайт, далее конкуренты.",
            )

        task_id = f"cwv-{datetime.now().timestamp()}"
        create_task_pending(task_id, "core_web_vitals", normalized_urls[0], status_message="Задача поставлена в очередь")
        print(
            f"[API] Core Web Vitals batch queued: urls={len(normalized_urls)}, "
            f"strategy={strategy}, competitor_mode={competitor_mode}, task_id={task_id}"
        )

        def _run_core_web_vitals_batch_task() -> None:
            with scan_token_context(scan_token):
                total = len(normalized_urls)
                sites: List[Dict[str, Any]] = []
                source = "pagespeed_insights_api"
                try:
                    update_task_state(
                        task_id,
                        status="RUNNING",
                        progress=5,
                        status_message=(
                            "Подготовка конкурентного Core Web Vitals сравнения"
                            if competitor_mode
                            else "Подготовка batch Core Web Vitals сканирования"
                        ),
                        progress_meta={
                            "processed_pages": 0,
                            "total_pages": total,
                            "queue_size": total,
                            "current_url": normalized_urls[0] if normalized_urls else "",
                            "competitor_mode": competitor_mode,
                        },
                    )

                    for index, target_url in enumerate(normalized_urls, start=1):
                        before_progress = 5 + int(((index - 1) / max(1, total)) * 85)
                        update_task_state(
                            task_id,
                            status="RUNNING",
                            progress=min(95, max(5, before_progress)),
                            status_message=f"Core Web Vitals: {index}/{total}",
                            progress_meta={
                                "processed_pages": index - 1,
                                "total_pages": total,
                                "queue_size": max(0, total - index + 1),
                                "current_url": target_url,
                            },
                        )

                        try:
                            scan_result = check_core_web_vitals(target_url, strategy=strategy, use_proxy=bool(data.use_proxy))
                            payload = scan_result.get("results", {}) if isinstance(scan_result, dict) else {}
                            summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
                            metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
                            categories = payload.get("categories", {}) if isinstance(payload, dict) else {}
                            diagnostics = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
                            analysis = payload.get("analysis", {}) if isinstance(payload, dict) else {}
                            opportunities = payload.get("opportunities", []) if isinstance(payload, dict) else []
                            recommendations = payload.get("recommendations", []) if isinstance(payload, dict) else []
                            action_plan = payload.get("action_plan", []) if isinstance(payload, dict) else []
                            source = str(payload.get("source") or source)
                            sites.append(
                                {
                                    "url": str(scan_result.get("url") or target_url),
                                    "status": "success",
                                    "summary": summary,
                                    "metrics": metrics,
                                    "categories": categories,
                                    "diagnostics": diagnostics,
                                    "analysis": analysis,
                                    "opportunities": opportunities[:8] if isinstance(opportunities, list) else [],
                                    "recommendations": recommendations if isinstance(recommendations, list) else [],
                                    "action_plan": action_plan[:8] if isinstance(action_plan, list) else [],
                                    "checked_at": payload.get("checked_at"),
                                }
                            )
                        except Exception as exc:
                            sites.append(
                                {
                                    "url": target_url,
                                    "status": "error",
                                    "error": str(exc),
                                }
                            )

                        after_progress = 5 + int((index / max(1, total)) * 85)
                        update_task_state(
                            task_id,
                            status="RUNNING",
                            progress=min(95, max(5, after_progress)),
                            status_message=f"Core Web Vitals: {index}/{total} завершено",
                            progress_meta={
                                "processed_pages": index,
                                "total_pages": total,
                                "queue_size": max(0, total - index),
                                "current_url": target_url,
                            },
                        )

                    batch_payload = _build_core_web_vitals_batch_result(
                        strategy=strategy,
                        source=source,
                        sites=sites,
                    )
                    if competitor_mode:
                        batch_payload = _build_core_web_vitals_competitor_result(
                            strategy=strategy,
                            source=source,
                            sites=sites,
                        )
                    failed_count = int((batch_payload.get("summary") or {}).get("failed_urls") or 0)
                    success_count = int((batch_payload.get("summary") or {}).get("successful_urls") or 0)
                    status_message = (
                        f"Batch Core Web Vitals завершен: успех {success_count}, ошибки {failed_count}"
                        if failed_count > 0
                        else f"Batch Core Web Vitals завершен: проверено {success_count} URL"
                    )
                    if competitor_mode:
                        status_message = (
                            f"Конкурентный анализ CWV завершен: успех {success_count}, ошибки {failed_count}"
                            if failed_count > 0
                            else f"Конкурентный анализ CWV завершен: сравнение по {success_count} URL"
                        )
                    result_payload = {
                        "task_type": "core_web_vitals",
                        "url": normalized_urls[0],
                        "results": batch_payload,
                    }
                    update_task_state(
                        task_id,
                        status="SUCCESS",
                        progress=100,
                        status_message=status_message,
                        progress_meta={
                            "processed_pages": total,
                            "total_pages": total,
                            "queue_size": 0,
                            "current_url": normalized_urls[-1] if normalized_urls else "",
                            "competitor_mode": competitor_mode,
                        },
                        result=result_payload,
                        error=None,
                    )
                except Exception as exc:
                    update_task_state(
                        task_id,
                        status="FAILURE",
                        progress=100,
                        status_message="Ошибка batch Core Web Vitals сканирования",
                        error=str(exc),
                    )

        background_tasks.add_task(_run_core_web_vitals_batch_task)
        return {
            "task_id": task_id,
            "status": "PENDING",
            "message": "Core Web Vitals competitor scan queued" if competitor_mode else "Core Web Vitals batch scan queued",
        }

    url = _normalize_http_input(data.url or "")
    if not url:
        raise HTTPException(status_code=422, detail="Введите корректный URL сайта (домен или http/https URL).")

    task_id = f"cwv-{datetime.now().timestamp()}"
    create_task_pending(task_id, "core_web_vitals", url, status_message="Задача поставлена в очередь")
    print(f"[API] Core Web Vitals single queued for: {url}, strategy={strategy}, task_id={task_id}")

    def _run_core_web_vitals_single_task() -> None:
        with scan_token_context(scan_token):
            try:
                update_task_state(
                    task_id,
                    status="RUNNING",
                    progress=10,
                    status_message="Запуск Core Web Vitals сканирования",
                    progress_meta={
                        "processed_pages": 0,
                        "total_pages": 1,
                        "queue_size": 1,
                        "current_url": url,
                    },
                )
                result = check_core_web_vitals(url, strategy=strategy, use_proxy=bool(data.use_proxy), combined=bool(data.combined))
                update_task_state(
                    task_id,
                    status="SUCCESS",
                    progress=100,
                    status_message="Core Web Vitals scan completed",
                    progress_meta={
                        "processed_pages": 1,
                        "total_pages": 1,
                        "queue_size": 0,
                        "current_url": url,
                    },
                    result=result,
                    error=None,
                )
            except ValueError as exc:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="Ошибка Core Web Vitals сканирования",
                    error=str(exc),
                )
            except Exception as exc:
                update_task_state(
                    task_id,
                    status="FAILURE",
                    progress=100,
                    status_message="Ошибка Core Web Vitals сканирования",
                    error=f"Core Web Vitals scan failed: {exc}",
                )

    background_tasks.add_task(_run_core_web_vitals_single_task)
    return {
        "task_id": task_id,
        "status": "PENDING",
        "message": "Core Web Vitals scan queued",
    }
