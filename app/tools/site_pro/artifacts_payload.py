"""Artifacts payload builder for Site Audit Pro."""
from __future__ import annotations

from typing import Any, Dict, List

from .schema import NormalizedSiteAuditRow


def build_artifacts_payload(
    *,
    max_pages: int,
    rows: List[NormalizedSiteAuditRow],
    effective_batch_mode: bool,
    prepared_batch_urls: List[str],
    crawl_errors: List[str],
    topic_clusters: Dict[str, List[str]],
    semantic_suggestions: List[Dict[str, str]],
    broken_links_data: Dict[str, Any],
    image_analysis_data: Dict[str, Any],
    homepage_row: NormalizedSiteAuditRow | None,
) -> Dict[str, Any]:
    """Build stable artifacts payload without changing contract."""
    crawl_budget_summary = {
        "high_risk_urls": sum(1 for row in rows if (row.crawl_budget_risk or "") == "high"),
        "medium_risk_urls": sum(1 for row in rows if (row.crawl_budget_risk or "") == "medium"),
        "parameterized_urls": sum(1 for row in rows if int(row.url_params_count or 0) > 0),
        "deep_path_urls": sum(1 for row in rows if int(row.path_depth or 0) >= 4),
    }

    homepage_security: Dict[str, Any] = {}
    if homepage_row:
        homepage_security = {
            "url": homepage_row.url,
            "security_headers_score": homepage_row.security_headers_score,
            "csp_present": homepage_row.csp_present,
            "hsts_present": homepage_row.hsts_present,
            "x_frame_options_present": homepage_row.x_frame_options_present,
            "referrer_policy_present": homepage_row.referrer_policy_present,
            "permissions_policy_present": homepage_row.permissions_policy_present,
            "mixed_content_count": homepage_row.mixed_content_count,
        }

    artifacts: Dict[str, Any] = {
        "migration_stage": "adapter_lightweight_crawl",
        "max_pages_requested": max_pages,
        "max_pages_scanned": len(rows),
        "batch_mode": effective_batch_mode,
        "batch_urls_requested": len(prepared_batch_urls),
        "crawl_errors": crawl_errors[:50],
        "crawl_budget_summary": crawl_budget_summary,
        "homepage_security": homepage_security,
        "topic_clusters_count": len(topic_clusters),
        "semantic_suggestions": semantic_suggestions,
        "broken_links": broken_links_data,
        "image_analysis": image_analysis_data,
        "notes": [
            "Lightweight crawl adapter is active",
            "Full seopro calculation parity is pending",
        ],
    }
    if effective_batch_mode:
        artifacts["notes"].append("Batch URL mode active: only provided URLs were scanned")

    return artifacts
