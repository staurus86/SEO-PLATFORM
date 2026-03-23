"""Post-crawl signal application for Site Audit Pro."""
from __future__ import annotations

from typing import Callable, List, Optional

from .schema import NormalizedSiteAuditRow, SiteAuditProIssue


NormalizeUrl = Callable[[str], str]


def find_homepage_row(rows: List[NormalizedSiteAuditRow], *, start_url: str, normalize_url: NormalizeUrl) -> Optional[NormalizedSiteAuditRow]:
    start_norm = normalize_url(start_url)
    for row in rows:
        if normalize_url(row.url) == start_norm or normalize_url(row.final_url or "") == start_norm:
            return row
    return None


def apply_homepage_security_signals(homepage_row: Optional[NormalizedSiteAuditRow], *, selected_mode: str) -> None:
    if not homepage_row or selected_mode != "full":
        return

    if not homepage_row.csp_present:
        homepage_row.issues.append(
            SiteAuditProIssue(severity="warning", code="security_missing_csp", title="Homepage missing CSP header")
        )
    if homepage_row.is_https and not homepage_row.hsts_present:
        homepage_row.issues.append(
            SiteAuditProIssue(severity="warning", code="security_missing_hsts", title="Homepage missing HSTS header")
        )
    if not homepage_row.x_frame_options_present:
        homepage_row.issues.append(
            SiteAuditProIssue(severity="info", code="security_missing_xfo", title="Homepage missing X-Frame-Options header")
        )
    if not homepage_row.referrer_policy_present:
        homepage_row.issues.append(
            SiteAuditProIssue(
                severity="info",
                code="security_missing_referrer_policy",
                title="Homepage missing Referrer-Policy header",
            )
        )
    if not homepage_row.permissions_policy_present:
        homepage_row.issues.append(
            SiteAuditProIssue(
                severity="info",
                code="security_missing_permissions_policy",
                title="Homepage missing Permissions-Policy header",
            )
        )
    if int(homepage_row.mixed_content_count or 0) > 0:
        homepage_row.issues.append(
            SiteAuditProIssue(
                severity="warning",
                code="security_mixed_content_homepage",
                title="Homepage contains mixed content links",
                details=f"Mixed content refs: {homepage_row.mixed_content_count}",
            )
        )


def apply_orphan_page_signals(rows: List[NormalizedSiteAuditRow]) -> None:
    for row in rows:
        if row.orphan_page:
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="orphan_or_isolated_page",
                    title="Orphan page — no incoming internal links",
                )
            )
