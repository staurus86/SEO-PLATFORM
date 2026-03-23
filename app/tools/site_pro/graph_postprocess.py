"""Post-crawl graph enrichment for Site Audit Pro."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Set, Tuple

from .graph_algorithms import _apply_linking_scores, _build_semantic_linking_map, _compute_pagerank, _compute_tfidf_scores
from .schema import NormalizedSiteAuditRow


def enrich_graph_metrics(
    *,
    rows: List[NormalizedSiteAuditRow],
    link_graph: Dict[str, Set[str]],
    incoming_counts: Counter,
    page_texts: Dict[str, str],
) -> Tuple[Dict[str, Set[str]], Dict[str, List[str]], List[Dict[str, str]]]:
    """Populate graph-derived row fields and return artifacts for downstream payloads."""
    all_urls = [row.url for row in rows]
    allowed = set(all_urls)
    normalized_graph: Dict[str, Set[str]] = {}
    for url in all_urls:
        normalized_graph[url] = {target for target in link_graph.get(url, set()) if target in allowed}

    pagerank_scores = _compute_pagerank(normalized_graph)
    tfidf_scores = _compute_tfidf_scores(page_texts, top_n=10)

    for row in rows:
        row.incoming_internal_links = int(incoming_counts.get(row.url, 0))
        row.pagerank = pagerank_scores.get(row.url, 0.0)
        row.tf_idf_keywords = tfidf_scores.get(row.url, {})
        row.top_terms = list(row.tf_idf_keywords.keys())[:10]
        row.topic_label = row.top_terms[0] if row.top_terms else (row.top_keywords[0] if row.top_keywords else "misc")

    semantic_by_source, topic_clusters = _build_semantic_linking_map(rows)
    for row in rows:
        row.semantic_links = semantic_by_source.get(row.url, [])

    _apply_linking_scores(rows=rows, incoming_counts=incoming_counts)

    semantic_suggestions: List[Dict[str, str]] = []
    for topic, urls in topic_clusters.items():
        if len(urls) < 2:
            continue
        base = urls[0]
        linked = normalized_graph.get(base, set())
        for candidate in urls[1:]:
            if candidate not in linked:
                semantic_suggestions.append(
                    {
                        "source_url": base,
                        "target_url": candidate,
                        "topic": topic,
                        "reason": "Shared topic without internal link",
                    }
                )
            if len(semantic_suggestions) >= 500:
                break
        if len(semantic_suggestions) >= 500:
            break

    return normalized_graph, topic_clusters, semantic_suggestions
