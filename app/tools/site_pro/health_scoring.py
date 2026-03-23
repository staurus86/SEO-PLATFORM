"""Health score calculation for Site Audit Pro rows."""
from __future__ import annotations

from collections import Counter
from typing import List

from .schema import NormalizedSiteAuditRow


def calculate_site_health_scores(rows: List[NormalizedSiteAuditRow], incoming_counts: Counter) -> None:
    """Assign per-page health score without mutating export schema."""
    if not rows:
        return

    for row in rows:
        score = 0.0
        if row.indexable:
            score += 20.0

        score += 5.0 if row.is_https else 0.0
        score += 5.0 if row.mobile_friendly_hint else 0.0
        score += 4.0 if row.compression_enabled else 0.0
        score += 4.0 if row.cache_control else 0.0
        if row.canonical_status == "self":
            score += 4.0
        elif row.canonical_status == "other":
            score += 2.0
        elif row.canonical_status == "external":
            score += 1.0
        score += min(6.0, float(row.html_quality_score or 0.0) / 100.0 * 6.0)

        response_time_ms = row.response_time_ms
        if response_time_ms is not None:
            if response_time_ms <= 800:
                score += 4.0
            elif response_time_ms <= 1500:
                score += 2.0
            elif response_time_ms <= 3000:
                score += 1.0

        score += 2.0 if int(row.structured_data or 0) > 0 else 0.0

        words = int(row.word_count or 0)
        score += 10.0 if words >= 300 else (words / 300.0) * 10.0
        score += min(8.0, float(row.unique_percent or 0.0) / 100.0 * 8.0)
        score += min(5.0, float(row.readability_score or 0.0) / 100.0 * 5.0)

        tox = float(row.toxicity_score or 0.0)
        if tox <= 20:
            score += 4.0
        elif tox <= 40:
            score += 2.0
        elif tox <= 60:
            score += 1.0

        freshness = row.content_freshness_days
        if freshness is not None:
            if freshness <= 180:
                score += 3.0
            elif freshness <= 365:
                score += 2.0
            elif freshness <= 730:
                score += 1.0

        title_len = int(row.title_len or 0)
        if 40 <= title_len <= 65:
            score += 5.0
        elif 25 <= title_len <= 75:
            score += 3.0

        desc_len = int(row.description_len or 0)
        if 80 <= desc_len <= 160:
            score += 3.0
        elif 50 <= desc_len <= 200:
            score += 1.0

        if int(row.h1_count or 0) == 1:
            score += 2.0

        no_alt = int(row.images_no_alt or 0)
        score += max(0.0, 2.0 - no_alt * 0.4)

        incoming = int(incoming_counts.get(row.url, 0))
        score += min(6.0, incoming * 1.5)
        if not row.orphan_page:
            score += 2.0
        if int(row.outgoing_internal_links or 0) > 0:
            score += 2.0

        if int(row.duplicate_title_count or 0) > 1:
            score -= 2.0
        if int(row.duplicate_description_count or 0) > 1:
            score -= 1.0

        row.health_score = round(max(0.0, min(100.0, score)), 1)
