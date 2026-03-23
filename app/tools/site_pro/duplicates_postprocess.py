"""Duplicate and structure post-processing for Site Audit Pro."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Set

from .content_checks import _hamming64, _simhash64
from .schema import NormalizedSiteAuditRow, SiteAuditProIssue


def apply_duplicate_and_depth_signals(
    *,
    rows: List[NormalizedSiteAuditRow],
    titles_by_url: Dict[str, str],
    descriptions_by_url: Dict[str, str],
    title_counter: Counter,
    desc_counter: Counter,
    depth_by_url: Dict[str, int],
    normalize_url,
    selected_mode: str,
    effective_batch_mode: bool,
) -> None:
    duplicate_titles = {title for title, count in title_counter.items() if title and count > 1}
    duplicate_desc = {desc for desc, count in desc_counter.items() if desc and count > 1}

    for row in rows:
        row_title = titles_by_url.get(row.url, "")
        row_desc = descriptions_by_url.get(row.url, "")
        title_count = title_counter.get(row_title, 0) if row_title else 0
        desc_count = desc_counter.get(row_desc, 0) if row_desc else 0
        row.duplicate_title_count = title_count if title_count > 1 else 0
        row.duplicate_description_count = desc_count if desc_count > 1 else 0

        if row_title in duplicate_titles:
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="duplicate_title",
                    title="Duplicate title detected",
                )
            )
        if row_desc in duplicate_desc:
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="duplicate_meta_description",
                    title="Duplicate meta description detected",
                )
            )

        row_norm = normalize_url(row.final_url or row.url)
        row.click_depth = depth_by_url.get(row_norm, depth_by_url.get(normalize_url(row.url)))
        if (selected_mode == "full") and (not effective_batch_mode) and row.click_depth is not None and row.click_depth > 3:
            row.issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="deep_click_depth",
                    title="Page is too deep in click depth",
                    details=f"Click depth: {row.click_depth}",
                )
            )


def apply_near_duplicate_signals(
    *,
    rows: List[NormalizedSiteAuditRow],
    page_texts: Dict[str, str],
) -> None:
    simhash_by_url: Dict[str, int] = {}
    row_by_url: Dict[str, NormalizedSiteAuditRow] = {}
    for row in rows:
        row_by_url[row.url] = row
        text = page_texts.get(row.url, "")
        if int(row.word_count or 0) < 80:
            continue
        simhash_by_url[row.url] = _simhash64(text)

    near_dup_map: Dict[str, Set[str]] = defaultdict(set)
    candidate_urls = list(simhash_by_url.keys())
    for i in range(len(candidate_urls)):
        url_1 = candidate_urls[i]
        hash_1 = simhash_by_url[url_1]
        for j in range(i + 1, len(candidate_urls)):
            url_2 = candidate_urls[j]
            hash_2 = simhash_by_url[url_2]
            if _hamming64(hash_1, hash_2) <= 6:
                near_dup_map[url_1].add(url_2)
                near_dup_map[url_2].add(url_1)

    for url_key, near_set in near_dup_map.items():
        row = row_by_url.get(url_key)
        if not row:
            continue
        row.near_duplicate_count = len(near_set)
        row.near_duplicate_urls = sorted(near_set)[:10]
        row.issues.append(
            SiteAuditProIssue(
                severity="warning",
                code="near_duplicate_content",
                title="Near-duplicate content detected",
                details=f"Similar pages: {len(near_set)}",
            )
        )
