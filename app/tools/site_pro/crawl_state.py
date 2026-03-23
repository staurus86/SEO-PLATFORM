"""Helpers for updating Site Audit Pro crawl state after fetching a page."""
from __future__ import annotations

from collections import Counter
from typing import Any, Deque, Dict, Set

from .crawl_stage import CrawlPageSuccess
from .schema import NormalizedSiteAuditRow


def record_page_state(
    *,
    row: NormalizedSiteAuditRow,
    page_result: CrawlPageSuccess,
    current_depth: int,
    page_limit: int,
    effective_batch_mode: bool,
    visited: Set[str],
    queue: Deque[str],
    depth_by_url: Dict[str, int],
    incoming_counts: Counter,
    link_graph: Dict[str, Set[str]],
    titles_by_url: Dict[str, str],
    descriptions_by_url: Dict[str, str],
    title_counter: Counter,
    desc_counter: Counter,
    page_texts: Dict[str, str],
    anchor_quality_raw: Dict[str, Any],
    all_discovered_links: Dict[str, Set[str]],
    all_image_urls: Dict[str, Set[str]],
    all_image_urls_global: Set[str],
    normalize_url: Any,
) -> None:
    depth_by_url[normalize_url(row.url)] = min(depth_by_url.get(normalize_url(row.url), current_depth), current_depth)
    depth_by_url[normalize_url(page_result.final_url)] = min(depth_by_url.get(normalize_url(page_result.final_url), current_depth), current_depth)

    if row.title:
        normalized_title = row.title.strip().lower()
        titles_by_url[row.url] = normalized_title
        title_counter[normalized_title] += 1
    if row.meta_description:
        normalized_desc = row.meta_description.strip().lower()
        descriptions_by_url[row.url] = normalized_desc
        desc_counter[normalized_desc] += 1

    page_texts[row.url] = page_result.page_text
    anchor_quality_raw[row.url] = (page_result.weak_anchor_count, page_result.anchor_total)
    link_graph[row.url] = set(page_result.links)

    for link in page_result.links:
        incoming_counts[link] += 1
        link_norm = normalize_url(link)
        if link_norm not in depth_by_url:
            depth_by_url[link_norm] = current_depth + 1
        if (not effective_batch_mode) and link not in visited and len(visited) + len(queue) < page_limit * 2:
            queue.append(link)

    for discovered in page_result.discovered_links:
        all_discovered_links[discovered].add(page_result.row.url)
    for image in page_result.image_urls:
        all_image_urls[page_result.row.url].add(image)
        all_image_urls_global.add(image)
