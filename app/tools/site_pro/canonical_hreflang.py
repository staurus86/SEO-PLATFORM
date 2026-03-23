"""Canonical and hreflang helpers for Site Audit Pro."""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Set, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .schema import NormalizedSiteAuditRow, SiteAuditProIssue


NormalizeUrl = Callable[[str], str]


def extract_hreflang_data(soup: BeautifulSoup, page_url: str, normalize_url: NormalizeUrl) -> Tuple[List[str], Dict[str, str], bool]:
    langs: List[str] = []
    targets: Dict[str, str] = {}
    has_x_default = False
    for tag in soup.find_all("link", href=True):
        rel = [str(x).lower() for x in (tag.get("rel") or [])]
        if "alternate" not in rel:
            continue
        lang = str(tag.get("hreflang") or "").strip()
        if not lang:
            continue
        href = str(tag.get("href") or "").strip()
        if not href:
            continue
        lang_lower = lang.lower()
        normalized_target = normalize_url(urljoin(page_url, href))
        langs.append(lang_lower)
        targets[lang_lower] = normalized_target
        if lang_lower == "x-default":
            has_x_default = True
    return langs, targets, has_x_default


def apply_canonical_and_hreflang_checks(
    rows: List[NormalizedSiteAuditRow],
    *,
    extended_hreflang_checks: bool,
    normalize_url: NormalizeUrl,
) -> None:
    row_by_url: Dict[str, NormalizedSiteAuditRow] = {}
    for row in rows:
        row_by_url[normalize_url(row.url)] = row
        if row.final_url:
            row_by_url[normalize_url(row.final_url)] = row

    for row in rows:
        canonical_raw = (row.canonical or "").strip()
        if canonical_raw:
            canonical_target = normalize_url(urljoin(row.url, canonical_raw))
            target = row_by_url.get(canonical_target)
            if target:
                row.canonical_target_status = target.status_code
                row.canonical_target_indexable = target.indexable
                target_status = int(target.status_code or 0)
                target_robots = (target.meta_robots or "").lower()
                if target_status >= 400:
                    row.canonical_conflict = "canonical_target_4xx_5xx"
                    row.issues.append(
                        SiteAuditProIssue(
                            severity="critical",
                            code="canonical_target_error_status",
                            title="Canonical points to an error page",
                            details=f"Canonical target status: {target_status}",
                        )
                    )
                elif 300 <= target_status < 400:
                    row.canonical_conflict = "canonical_target_redirect"
                    row.issues.append(
                        SiteAuditProIssue(
                            severity="warning",
                            code="canonical_target_redirect",
                            title="Canonical points to a redirect URL",
                            details=f"Canonical target status: {target_status}",
                        )
                    )
                elif "noindex" in target_robots:
                    row.canonical_conflict = "canonical_target_noindex"
                    row.issues.append(
                        SiteAuditProIssue(
                            severity="warning",
                            code="canonical_target_noindex",
                            title="Canonical points to a noindex page",
                        )
                    )

        robots = (row.meta_robots or "").lower()
        if "noindex" in robots and (row.canonical_status or "").lower() in ("self", "other"):
            row.canonical_conflict = row.canonical_conflict or "noindex_with_canonical"
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="noindex_canonical_conflict",
                    title="Page has both canonical and noindex",
                )
            )

    if not extended_hreflang_checks:
        return

    lang_re = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$|^x-default$", re.I)
    for row in rows:
        langs = list(row.hreflang_langs or [])
        targets = dict(row.hreflang_targets or {})
        if not langs:
            continue

        seen_langs: Set[str] = set()
        for lang in langs:
            if lang in seen_langs:
                row.hreflang_issues.append(f"duplicate_lang:{lang}")
                continue
            seen_langs.add(lang)
            if not lang_re.match(lang):
                row.hreflang_issues.append(f"invalid_lang_code:{lang}")

        if len(langs) > 1 and not row.hreflang_has_x_default:
            row.hreflang_issues.append("missing_x_default")

        row_norm = normalize_url(row.final_url or row.url)
        for lang, target_url in targets.items():
            target_row = row_by_url.get(normalize_url(target_url))
            if not target_row:
                row.hreflang_issues.append(f"target_not_scanned:{lang}")
                continue
            back_targets = {
                normalize_url(x)
                for x in (target_row.hreflang_targets or {}).values()
                if x
            }
            if row_norm not in back_targets:
                row.hreflang_issues.append(f"missing_reciprocal:{lang}")

        for item in row.hreflang_issues[:15]:
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="hreflang_extended_check",
                    title="Extended hreflang check warning",
                    details=item,
                )
            )
