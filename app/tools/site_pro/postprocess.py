"""Post-processing helpers for Site Audit Pro rows and summary."""
from __future__ import annotations

from typing import Dict, List

from .schema import NormalizedSiteAuditRow, SiteAuditProSummary


def finalize_rows(rows: List[NormalizedSiteAuditRow]) -> None:
    """Populate recommendation and flattened issue codes for each row."""
    for row in rows:
        outgoing_total = (row.outgoing_internal_links or 0) + (row.outgoing_external_links or 0)
        if row.orphan_page:
            row.recommendation = "Add internal links from relevant hub/category pages."
        elif (row.images_without_alt or 0) > 0:
            row.recommendation = "Add descriptive alt text for images."
        elif row.weak_anchor_ratio and row.weak_anchor_ratio > 0.3:
            row.recommendation = "Replace weak anchors with intent-rich descriptive anchors."
        elif row.health_score is not None and row.health_score < 80:
            row.recommendation = "Resolve technical and on-page issues to raise health score."
        elif outgoing_total == 0:
            row.recommendation = "Add contextual internal links to improve crawl paths."
        else:
            row.recommendation = "Maintain page quality and monitor regressions."
        row.all_issues = [issue.code for issue in row.issues]


def build_summary(rows: List[NormalizedSiteAuditRow], *, mode: str) -> SiteAuditProSummary:
    """Aggregate summary metrics without changing outward schema."""
    severity_counts: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    for row in rows:
        for issue in row.issues:
            sev = (issue.severity or "info").lower()
            if sev not in severity_counts:
                continue
            severity_counts[sev] += 1

    issues_total = sum(severity_counts.values())
    avg_score = round(sum((r.health_score or 0.0) for r in rows) / len(rows), 1) if rows else 0.0

    return SiteAuditProSummary(
        total_pages=len(rows),
        internal_pages=len(rows),
        issues_total=issues_total,
        critical_issues=severity_counts["critical"],
        warning_issues=severity_counts["warning"],
        info_issues=severity_counts["info"],
        score=avg_score,
        mode=mode,
    )
