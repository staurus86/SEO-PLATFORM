"""Single-page crawl stage helpers for Site Audit Pro."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from app.tools.http_text import decode_response_text

from .schema import NormalizedSiteAuditRow, SiteAuditProIssue


@dataclass
class CrawlPageSuccess:
    row: NormalizedSiteAuditRow
    final_url: str
    page_text: str
    links: List[str]
    weak_anchor_count: int
    anchor_total: int
    discovered_links: List[str]
    image_urls: List[str]


def build_crawl_page_success(
    *,
    current_url: str,
    response: Any,
    timeout: int,
    base_host: str,
    detailed_checks: bool,
    normalize_url,
    build_row,
    extract_all_links,
    extract_image_urls,
) -> CrawlPageSuccess:
    """Normalize one successful crawl response into row + extracted side data."""
    _ = timeout
    raw_html = decode_response_text(response)
    final_url = normalize_url(response.url or current_url)
    reason = str(getattr(response, "reason", "") or "").strip()
    status_line = f"{response.status_code} {reason}".strip()
    response_time_ms = int(
        max(
            0.0,
            float(getattr(getattr(response, "elapsed", None), "total_seconds", lambda: 0.0)()) * 1000.0,
        )
    )
    html_size_bytes = len((raw_html or "").encode("utf-8", errors="ignore"))
    row, links, page_text, weak_anchor_count, anchor_total = build_row(
        source_url=current_url,
        final_url=final_url,
        status_code=response.status_code,
        status_line=status_line,
        html=raw_html or "",
        base_host=base_host,
        headers=dict(getattr(response, "headers", {}) or {}),
        response_time_ms=response_time_ms,
        redirect_count=len(getattr(response, "history", []) or []),
        html_size_bytes=html_size_bytes,
        detailed_checks=detailed_checks,
    )

    page_soup = BeautifulSoup(raw_html or "", "html.parser")
    internal_links, external_links = extract_all_links(final_url, page_soup, base_host)
    image_urls = extract_image_urls(final_url, page_soup)

    return CrawlPageSuccess(
        row=row,
        final_url=final_url,
        page_text=page_text,
        links=links,
        weak_anchor_count=weak_anchor_count,
        anchor_total=anchor_total,
        discovered_links=internal_links + external_links,
        image_urls=image_urls,
    )


def build_crawl_page_failure(*, current_url: str, error: Exception) -> NormalizedSiteAuditRow:
    """Build fallback row for failed fetch without changing outward schema."""
    return NormalizedSiteAuditRow(
        url=current_url,
        status_code=None,
        status_line=None,
        indexable=False,
        health_score=0.0,
        issues=[
            SiteAuditProIssue(
                severity="critical",
                code="request_failed",
                title="Failed to fetch page",
                details=str(error),
            )
        ],
    )
