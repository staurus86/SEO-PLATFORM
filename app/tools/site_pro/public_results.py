"""Public payload builder for Site Audit Pro."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from .schema import NormalizedSiteAuditPayload


def build_public_results(normalized: NormalizedSiteAuditPayload) -> Dict[str, Any]:
    """Convert normalized payload into stable public/export payload."""
    pages = [row.model_dump() for row in normalized.rows]
    issues = [
        {**issue.model_dump(), "url": row.url}
        for row in normalized.rows
        for issue in row.issues
    ]
    pagerank = sorted(
        [{"url": row.url, "score": row.pagerank or 0.0} for row in normalized.rows],
        key=lambda x: x["score"],
        reverse=True,
    )
    tf_idf = [{"url": row.url, "top_terms": row.top_terms} for row in normalized.rows]
    duplicate_title_groups: Dict[str, List[str]] = defaultdict(list)
    duplicate_desc_groups: Dict[str, List[str]] = defaultdict(list)
    topic_clusters: Dict[str, List[str]] = defaultdict(list)
    for row in normalized.rows:
        title = (row.title or "").strip().lower()
        desc = (row.meta_description or "").strip().lower()
        if row.duplicate_title_count > 1 and title:
            duplicate_title_groups[title].append(row.url)
        if row.duplicate_description_count > 1 and desc:
            duplicate_desc_groups[desc].append(row.url)
        topic_clusters[(row.topic_label or "misc")].append(row.url)

    total_pages = max(1, len(normalized.rows))
    orphan_pages = sum(1 for row in normalized.rows if row.orphan_page)
    topic_hubs = sum(1 for row in normalized.rows if row.topic_hub)
    pages_without_alt = sum(1 for row in normalized.rows if (row.images_without_alt or 0) > 0)
    non_https_pages = sum(1 for row in normalized.rows if row.is_https is False)
    avg_response_time = round(
        sum((row.response_time_ms or 0) for row in normalized.rows) / total_pages,
        1,
    )
    avg_readability = round(
        sum((row.readability_score or 0.0) for row in normalized.rows) / total_pages,
        1,
    )
    avg_link_quality = round(
        sum((row.link_quality_score or 0.0) for row in normalized.rows) / total_pages,
        1,
    )
    avg_perf_light = round(
        sum((row.perf_light_score or 0.0) for row in normalized.rows) / total_pages,
        1,
    )

    pipeline = {
        "pagerank": pagerank,
        "tf_idf": tf_idf,
        "duplicates": {
            "title_groups": [{"value": k, "urls": v} for k, v in duplicate_title_groups.items()],
            "description_groups": [{"value": k, "urls": v} for k, v in duplicate_desc_groups.items()],
        },
        "site_health": {
            "average_health_score": normalized.summary.score,
            "critical_issues": normalized.summary.critical_issues,
            "warning_issues": normalized.summary.warning_issues,
        },
        "semantic_linking_map": normalized.artifacts.get("semantic_suggestions", []),
        "anchor_text_quality": {
            "average_weak_anchor_ratio": round(
                sum((row.weak_anchor_ratio or 0.0) for row in normalized.rows) / max(1, len(normalized.rows)),
                3,
            ),
            "pages_with_weak_anchors": sum(1 for row in normalized.rows if (row.weak_anchor_ratio or 0.0) > 0.2),
        },
        "topic_clusters": [{"topic": k, "urls": v, "count": len(v)} for k, v in topic_clusters.items()],
        "link_quality_scores": [{"url": row.url, "score": row.link_quality_score} for row in normalized.rows],
        "metrics": {
            "avg_response_time_ms": avg_response_time,
            "avg_readability_score": avg_readability,
            "avg_link_quality_score": avg_link_quality,
            "avg_perf_light_score": avg_perf_light,
            "orphan_pages": orphan_pages,
            "topic_hubs": topic_hubs,
            "pages_without_alt": pages_without_alt,
            "non_https_pages": non_https_pages,
            "crawl_budget_high_risk": sum(1 for row in normalized.rows if (row.crawl_budget_risk or "") == "high"),
            "crawl_budget_medium_risk": sum(1 for row in normalized.rows if (row.crawl_budget_risk or "") == "medium"),
        },
    }
    return {
        "engine": "site_pro_adapter_v0",
        "mode": normalized.mode,
        "summary": normalized.summary.model_dump(),
        "pages": pages,
        "issues": issues,
        "issues_count": normalized.summary.issues_total,
        "pipeline": pipeline,
        "artifacts": normalized.artifacts,
    }
