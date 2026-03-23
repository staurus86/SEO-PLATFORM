"""Post-crawl broken link and image analysis helpers for Site Audit Pro."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from .schema import NormalizedSiteAuditRow, SiteAuditProIssue


def build_broken_links_data(
    *,
    link_check_results: List[Dict[str, Any]],
    all_discovered_links: Dict[str, Set[str]],
) -> Dict[str, Any]:
    broken_items: List[Dict[str, Any]] = []
    redirected_items: List[Dict[str, Any]] = []
    for item in link_check_results:
        if item.get("is_broken"):
            found_on = sorted(all_discovered_links.get(item["url"], set()))[:10]
            broken_items.append({**item, "found_on": found_on})
        elif item.get("redirect_url"):
            redirected_items.append(item)

    return {
        "total_checked": len(link_check_results),
        "broken_count": len(broken_items),
        "broken": broken_items[:200],
        "redirected": redirected_items[:200],
    }


def build_image_analysis_data(
    *,
    image_check_results: List[Dict[str, Any]],
    total_images_found: int,
) -> Dict[str, Any]:
    format_counts: Dict[str, int] = {"jpeg": 0, "png": 0, "webp": 0, "avif": 0, "svg": 0, "gif": 0, "other": 0}
    large_images: List[Dict[str, Any]] = []
    total_size = 0
    for img_result in image_check_results:
        fmt = img_result.get("format", "other")
        if fmt in format_counts:
            format_counts[fmt] += 1
        else:
            format_counts["other"] += 1
        sz = int(img_result.get("size_bytes") or 0)
        total_size += sz
        if sz > 200 * 1024:
            large_images.append(img_result)

    checked_count = len(image_check_results)
    modern_count = format_counts["webp"] + format_counts["avif"] + format_counts["svg"]
    modern_format_pct = round((modern_count / max(1, checked_count)) * 100.0, 1)
    legacy_count = format_counts["jpeg"] + format_counts["png"] + format_counts["gif"]

    return {
        "total_images": total_images_found,
        "checked": checked_count,
        "formats": format_counts,
        "modern_format_pct": modern_format_pct,
        "large_images": sorted(large_images, key=lambda x: x.get("size_bytes", 0), reverse=True)[:50],
        "missing_modern_format": legacy_count,
        "total_size_bytes": total_size,
        "avg_size_bytes": round(total_size / max(1, checked_count)),
    }


def apply_image_analysis_issues(rows: List[NormalizedSiteAuditRow], image_analysis_data: Dict[str, Any]) -> None:
    issue_target = rows[0] if rows else None
    checked_count = int(image_analysis_data.get("checked", 0) or 0)
    if not issue_target or checked_count <= 0:
        return

    modern_format_pct = float(image_analysis_data.get("modern_format_pct", 0.0) or 0.0)
    formats = image_analysis_data.get("formats", {}) or {}
    modern_count = int(formats.get("webp", 0) or 0) + int(formats.get("avif", 0) or 0) + int(formats.get("svg", 0) or 0)
    large_images = list(image_analysis_data.get("large_images", []) or [])

    if modern_format_pct < 50.0:
        issue_target.issues.append(
            SiteAuditProIssue(
                severity="warning",
                code="low_modern_image_formats_site",
                title="Most images use legacy formats (JPEG/PNG). Consider WebP/AVIF.",
                details=f"Modern format: {modern_format_pct}% ({modern_count}/{checked_count})",
            )
        )

    very_large = [img for img in large_images if int(img.get("size_bytes") or 0) > 1024 * 1024]
    large_over_500k = [img for img in large_images if int(img.get("size_bytes") or 0) > 500 * 1024]
    if very_large:
        issue_target.issues.append(
            SiteAuditProIssue(
                severity="critical",
                code="very_large_images",
                title="Very large images found — significantly impacts page speed",
                details=f"{len(very_large)} images over 1MB",
            )
        )
    elif large_over_500k:
        issue_target.issues.append(
            SiteAuditProIssue(
                severity="warning",
                code="large_images",
                title=f"Large images found ({len(large_over_500k)}) — optimize for faster loading",
                details=f"{len(large_over_500k)} images over 500KB",
            )
        )


def apply_broken_link_issues(
    rows: List[NormalizedSiteAuditRow],
    *,
    broken_links_data: Dict[str, Any],
    link_graph: Dict[str, Set[str]],
    all_discovered_links: Dict[str, Set[str]],
) -> None:
    broken_items = list(broken_links_data.get("broken", []) or [])
    if not broken_items:
        return

    broken_urls_set = {item["url"] for item in broken_items if item.get("url")}
    for row in rows:
        page_internal = link_graph.get(row.url, set())
        page_all = {link for link, sources in all_discovered_links.items() if row.url in sources}
        broken_on_page = broken_urls_set & (page_internal | page_all)
        if broken_on_page:
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="broken_links_on_page",
                    title=f"Page has {len(broken_on_page)} broken link(s)",
                    details=", ".join(sorted(broken_on_page)[:5]),
                )
            )
