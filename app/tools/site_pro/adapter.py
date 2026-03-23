"""Adapter bridge for future seopro.py migration."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup

from .schema import (
    NormalizedSiteAuditPayload,
    SiteAuditProIssue,
    NormalizedSiteAuditRow,
)

from .constants import FILLER_WORDS, WEAK_ANCHORS
from .text_analysis import (
    _avg_sentence_length,
    _avg_word_length,
    _calc_filler_ratio,
    _calc_toxicity,
    _complex_words_percent,
    _extract_top_keywords,
    _keyword_density_profile,
    _keyword_stuffing_score,
    _readability_score,
    _tokenize,
)
from .content_checks import (
    _boilerplate_percent,
    _content_density,
    _content_freshness_days,
    _cta_text_quality,
    _detect_author_info,
    _detect_breadcrumbs,
    _detect_cloaking,
    _detect_contact_info,
    _detect_legal_docs,
    _detect_reviews,
    _detect_structured_data,
    _detect_trust_badges,
    _extract_hidden_content_signals,
    _h_hierarchy_summary,
    _heading_distribution,
    _semantic_tags_count,
    _unique_percent,
    _validate_structured_common,
)
from .ai_detection import (
    _ai_marker_sample,
    _classify_page_type,
    _detect_ai_markers,
)
from .canonical_hreflang import apply_canonical_and_hreflang_checks, extract_hreflang_data
from .artifacts_payload import build_artifacts_payload
from .crawl_stage import build_crawl_page_failure, build_crawl_page_success
from .duplicates_postprocess import apply_duplicate_and_depth_signals, apply_near_duplicate_signals
from .graph_postprocess import enrich_graph_metrics
from .health_scoring import calculate_site_health_scores
from .media_link_analysis import (
    apply_broken_link_issues,
    apply_image_analysis_issues,
    build_broken_links_data,
    build_image_analysis_data,
)
from .crawl_state import record_page_state
from .signals_stage import apply_homepage_security_signals, apply_orphan_page_signals, find_homepage_row
from .postprocess import build_summary, finalize_rows
from .public_results import build_public_results
from .sitemap_scope import sample_site_urls_from_sitemaps


class SiteAuditProAdapter:
    """
    Transitional adapter.
    Current behavior returns a deterministic normalized skeleton so API/UI wiring
    can be shipped before full seopro function-level porting.
    """

    # Backward-compatible thin wrappers kept intentionally: tests and utility code
    # still call legacy private methods directly on the adapter.
    def _detect_ai_markers(self, text: str) -> Tuple[int, List[str]]:
        return _detect_ai_markers(text)

    def _ai_marker_sample(self, text: str, markers: List[str]) -> List[str]:
        return _ai_marker_sample(text, markers)

    def _content_freshness_days(self, last_modified: str) -> Optional[int]:
        return _content_freshness_days(last_modified)

    def _is_internal_url(self, candidate: str, base_host: str) -> bool:
        parsed = urlparse(candidate)
        if not parsed.scheme.startswith("http"):
            return False
        return parsed.netloc == base_host

    def _normalize_url(self, raw_url: str) -> str:
        clean, _ = urldefrag((raw_url or "").strip())
        if clean.endswith("/") and len(clean) > len(urlparse(clean).scheme) + 3:
            return clean.rstrip("/")
        return clean

    def _extract_internal_links(self, page_url: str, soup: BeautifulSoup, base_host: str) -> List[str]:
        links: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = (tag.get("href") or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            candidate = self._normalize_url(urljoin(page_url, href))
            if self._is_internal_url(candidate, base_host):
                links.append(candidate)
        return links

    def _classify_canonical(self, canonical: str, page_url: str, base_host: str) -> str:
        if not canonical:
            return "missing"
        parsed = urlparse(canonical)
        if parsed.scheme and not parsed.scheme.startswith("http"):
            return "invalid"
        if parsed.scheme.startswith("http") and parsed.netloc and parsed.netloc != base_host:
            return "external"
        normalized_canonical = self._normalize_url(urljoin(page_url, canonical))
        normalized_page = self._normalize_url(page_url)
        if normalized_canonical == normalized_page:
            return "self"
        if normalized_canonical:
            return "other"
        return "invalid"

    def _extract_hreflang_data(self, soup: BeautifulSoup, page_url: str) -> Tuple[List[str], Dict[str, str], bool]:
        return extract_hreflang_data(soup=soup, page_url=page_url, normalize_url=self._normalize_url)

    def _apply_canonical_and_hreflang_checks(
        self,
        rows: List[NormalizedSiteAuditRow],
        *,
        start_url: str,
        extended_hreflang_checks: bool,
    ) -> None:
        _ = start_url
        apply_canonical_and_hreflang_checks(
            rows=rows,
            extended_hreflang_checks=extended_hreflang_checks,
            normalize_url=self._normalize_url,
        )

    def _extract_all_links(
        self, page_url: str, soup: BeautifulSoup, base_host: str,
    ) -> Tuple[List[str], List[str]]:
        """Return (internal_links, external_links) as resolved absolute URLs."""
        internal: List[str] = []
        external: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = (tag.get("href") or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            candidate = self._normalize_url(urljoin(page_url, href))
            parsed = urlparse(candidate)
            if not parsed.scheme.startswith("http"):
                continue
            if parsed.netloc == base_host:
                internal.append(candidate)
            else:
                external.append(candidate)
        return internal, external

    def _extract_image_urls(
        self, page_url: str, soup: BeautifulSoup,
    ) -> List[str]:
        """Extract image URLs from <img src> and <picture><source srcset>."""
        urls: List[str] = []
        for img in soup.find_all("img", src=True):
            src = (img.get("src") or "").strip()
            if src:
                urls.append(self._normalize_url(urljoin(page_url, src)))
        for source in soup.find_all("source", srcset=True):
            if source.find_parent("picture"):
                srcset = (source.get("srcset") or "").strip()
                if srcset:
                    # srcset may contain multiple entries like "url 1x, url 2x"
                    for entry in srcset.split(","):
                        parts = entry.strip().split()
                        if parts:
                            urls.append(self._normalize_url(urljoin(page_url, parts[0])))
        return urls

    def _check_links_batch(
        self,
        links: list,
        session: requests.Session,
        batch_size: int = 50,
        max_workers: int = 10,
    ) -> list:
        """Check links in batches to avoid server overload."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time

        results = []
        for i in range(0, len(links), batch_size):
            batch = links[i : i + batch_size]
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for link in batch:
                    futures[executor.submit(self._check_single_link, link, session)] = link
                for future in as_completed(futures):
                    link = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as e:
                        results.append({"url": link, "status_code": None, "is_broken": True, "error": str(e)})
            time.sleep(0.5)  # pause between batches
        return results

    def _check_single_link(self, url: str, session: requests.Session) -> dict:
        """HEAD request to check if link is alive. Falls back to GET on 405."""
        try:
            resp = session.head(url, timeout=8, allow_redirects=True)
            if resp.status_code == 405:
                resp = session.get(url, timeout=8, allow_redirects=True, stream=True)
                resp.close()
            return {
                "url": url,
                "status_code": resp.status_code,
                "is_broken": resp.status_code >= 400,
                "redirect_url": str(resp.url) if str(resp.url) != url else None,
                "response_time_ms": int(resp.elapsed.total_seconds() * 1000),
            }
        except requests.Timeout:
            return {"url": url, "status_code": None, "is_broken": True, "error": "timeout"}
        except Exception as e:
            return {"url": url, "status_code": None, "is_broken": True, "error": str(e)}

    def _check_images_batch(
        self,
        image_urls: list,
        session: requests.Session,
        batch_size: int = 50,
        max_workers: int = 10,
    ) -> list:
        """HEAD-check images in batches to get size and content-type."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time

        results = []
        for i in range(0, len(image_urls), batch_size):
            batch = image_urls[i : i + batch_size]
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for img_url in batch:
                    futures[executor.submit(self._head_image, img_url, session)] = img_url
                for future in as_completed(futures):
                    img_url = futures[future]
                    try:
                        results.append(future.result())
                    except Exception:
                        results.append({"url": img_url, "size_bytes": 0, "format": "other"})
            time.sleep(0.3)
        return results

    def _head_image(self, url: str, session: requests.Session) -> dict:
        """HEAD request for an image URL to get Content-Length and Content-Type."""
        try:
            resp = session.head(url, timeout=8, allow_redirects=True)
            content_type = (resp.headers.get("Content-Type") or "").lower()
            content_length = int(resp.headers.get("Content-Length") or 0)
            fmt = "other"
            if "webp" in content_type:
                fmt = "webp"
            elif "avif" in content_type:
                fmt = "avif"
            elif "svg" in content_type:
                fmt = "svg"
            elif "png" in content_type:
                fmt = "png"
            elif "gif" in content_type:
                fmt = "gif"
            elif "jpeg" in content_type or "jpg" in content_type:
                fmt = "jpeg"
            elif not content_type:
                # Fallback: detect from URL extension
                lower_url = url.lower().split("?")[0]
                if lower_url.endswith(".webp"):
                    fmt = "webp"
                elif lower_url.endswith(".avif"):
                    fmt = "avif"
                elif lower_url.endswith(".svg"):
                    fmt = "svg"
                elif lower_url.endswith(".png"):
                    fmt = "png"
                elif lower_url.endswith(".gif"):
                    fmt = "gif"
                elif lower_url.endswith((".jpg", ".jpeg")):
                    fmt = "jpeg"
            return {"url": url, "size_bytes": content_length, "format": fmt}
        except Exception:
            return {"url": url, "size_bytes": 0, "format": "other"}

    def _extract_anchor_data(
        self, page_url: str, soup: BeautifulSoup, base_host: str
    ) -> Tuple[List[str], int, int, int, int, int]:
        internal_links: List[str] = []
        weak_count = 0
        total = 0
        external = 0
        external_nofollow = 0
        external_follow = 0
        for tag in soup.find_all("a", href=True):
            href = (tag.get("href") or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True).lower())
            candidate = self._normalize_url(urljoin(page_url, href))
            parsed = urlparse(candidate)
            if not parsed.scheme.startswith("http"):
                continue
            total += 1
            if text in WEAK_ANCHORS:
                weak_count += 1
            if parsed.netloc == base_host:
                internal_links.append(candidate)
            else:
                external += 1
                rel_values = [r.strip().lower() for r in (tag.get("rel") or []) if isinstance(r, str)]
                if "nofollow" in rel_values:
                    external_nofollow += 1
                else:
                    external_follow += 1
        return internal_links, weak_count, total, external, external_nofollow, external_follow

    def _build_row(
        self,
        source_url: str,
        final_url: str,
        status_code: int,
        status_line: str,
        html: str,
        base_host: str,
        headers: Dict[str, Any],
        response_time_ms: int,
        redirect_count: int,
        html_size_bytes: int,
        detailed_checks: bool,
    ) -> Tuple[NormalizedSiteAuditRow, List[str], str, int, int]:
        soup = BeautifulSoup(html or "", "html.parser")
        body_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        title_tags = soup.find_all("title")
        title = (soup.title.string if soup.title and soup.title.string else "").strip()
        title_tags_count = len(title_tags)
        desc_tags = soup.find_all("meta", attrs={"name": lambda v: str(v).lower().strip() == "description"})
        desc_tag = desc_tags[0] if desc_tags else None
        description = (desc_tag.get("content") if desc_tag else "") or ""
        meta_description_tags_count = len(desc_tags)
        robots_tag = soup.find("meta", attrs={"name": "robots"})
        robots_tags = soup.find_all("meta", attrs={"name": lambda v: str(v).lower().strip() == "robots"})
        robots = ((robots_tag.get("content") if robots_tag else "") or "").lower()
        viewport_tag = soup.find("meta", attrs={"name": "viewport"})
        viewport = ((viewport_tag.get("content") if viewport_tag else "") or "").lower()
        charset_declared = bool(soup.find("meta", attrs={"charset": True}))
        multiple_meta_robots = len(robots_tags) > 1
        canonical_tag = soup.find("link", attrs={"rel": lambda x: x and "canonical" in str(x).lower()})
        canonical = (canonical_tag.get("href") if canonical_tag else "") or ""
        canonical_status = self._classify_canonical(canonical=canonical, page_url=final_url, base_host=base_host)
        breadcrumbs = _detect_breadcrumbs(soup)
        schema_count = len(
            [
                tag
                for tag in soup.find_all("script")
                if ((tag.get("type") or "").lower().strip() == "application/ld+json")
            ]
        )
        structured_data_total, structured_data_detail, structured_types = _detect_structured_data(soup)
        structured_error_codes = _validate_structured_common(soup)
        hreflang_langs, hreflang_targets, hreflang_has_x_default = self._extract_hreflang_data(soup=soup, page_url=final_url)
        hreflang_count = len(hreflang_langs)
        dom_nodes_count = len(soup.find_all(True))
        h1_count = len(soup.find_all("h1"))
        h1_text = (soup.find("h1").get_text(" ", strip=True)[:120] if soup.find("h1") else "")
        images = soup.find_all("img")
        image_srcs = [self._normalize_url(urljoin(final_url, str(img.get("src") or "").strip())) for img in images if str(img.get("src") or "").strip()]
        image_src_counter = Counter(image_srcs)
        image_duplicate_src_count = sum(1 for _, c in image_src_counter.items() if c > 1)
        images_external_count = sum(1 for src in image_srcs if urlparse(src).netloc and urlparse(src).netloc != base_host)
        images_modern_format_count = sum(
            1
            for src in image_srcs
            if re.search(r"\.(webp|avif)(?:$|[?#])", src, flags=re.I)
        )
        images_without_alt = sum(1 for img in images if not (img.get("alt") or "").strip())
        generic_alt_count = sum(
            1
            for img in images
            if str(img.get("alt") or "").strip().lower() in {"image", "photo", "picture", "img", "\u0444\u043e\u0442\u043e", "\u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0430"}
        )
        decorative_non_empty_alt_count = sum(
            1
            for img in images
            if (
                str(img.get("role") or "").strip().lower() == "presentation"
                or str(img.get("aria-hidden") or "").strip().lower() == "true"
            )
            and bool(str(img.get("alt") or "").strip())
        )
        images_no_width_height = sum(
            1 for img in images if not (str(img.get("width") or "").strip() and str(img.get("height") or "").strip())
        )
        images_no_lazy_load = sum(
            1
            for img in images
            if ((img.get("loading") or "").strip().lower() != "lazy")
        )
        lists_count = len(soup.find_all(["ul", "ol"]))
        tables_count = len(soup.find_all("table"))
        faq_count = len(soup.find_all(attrs={"itemtype": re.compile("FAQPage", re.I)}))
        cta_count = len(
            [
                tag
                for tag in soup.find_all(["a", "button"])
                if any(word in (tag.get_text(" ", strip=True).lower()) for word in ("buy", "order", "contact", "sign", "register"))
            ]
        )
        hidden_content, hidden_nodes_count, hidden_text_chars, hidden_text_snippets = _extract_hidden_content_signals(soup)
        deprecated_tags = sorted({t.name for t in soup.find_all(["font", "center", "marquee", "blink"])})
        semantic_tags_count = _semantic_tags_count(soup)
        heading_distribution = _heading_distribution(soup)
        h_hierarchy, h_errors, h_details = _h_hierarchy_summary(soup=soup, heading_distribution=heading_distribution)
        words = _tokenize(body_text)
        ai_markers_count, ai_markers_list = _detect_ai_markers(body_text)
        ai_marker_sample = _ai_marker_sample(body_text, ai_markers_list)
        word_count_est = len(words)
        ai_markers_density_1k = round((ai_markers_count / max(1, word_count_est)) * 1000.0, 2) if word_count_est else 0.0
        filler_phrases = [w for w in FILLER_WORDS if re.search(rf"\b{re.escape(w)}\b", body_text.lower())][:20]
        unique_word_count = len(set(words))
        top_keywords = _extract_top_keywords(words, top_n=10)
        keyword_density_profile = _keyword_density_profile(words, top_n=10)
        keyword_stuffing_score = _keyword_stuffing_score(body_text)
        lexical_diversity = round(unique_word_count / max(1, len(words)), 3) if words else 0.0
        readability_score = _readability_score(body_text)
        avg_sentence_length = _avg_sentence_length(body_text)
        avg_word_length = _avg_word_length(body_text)
        complex_words_percent = _complex_words_percent(body_text)
        content_density = _content_density(soup=soup, text=body_text)
        boilerplate_percent = _boilerplate_percent(text=body_text)
        toxicity_score = _calc_toxicity(words)
        filler_ratio = _calc_filler_ratio(body_text)
        page_type = _classify_page_type(final_url, structured_types, title, body_text)
        # Guard against false positives on legal/policy pages with formal language patterns.
        ai_false_positive_guard = page_type in {"legal"} or bool(re.search(r"\b(api|sdk|json|http|ssl|tls|csp)\b", body_text.lower()))
        ai_risk_raw = (
            ai_markers_count * 4.0
            + ai_markers_density_1k * 2.0
            + float(toxicity_score) * 0.6
            + float(filler_ratio) * 35.0
        )
        if ai_false_positive_guard and ai_markers_count <= 6:
            ai_risk_raw *= 0.75
        if word_count_est < 120 and ai_markers_count <= 2:
            ai_risk_raw *= 0.8
        ai_risk_score = round(max(0.0, min(100.0, ai_risk_raw)), 1)
        if ai_risk_score >= 70:
            ai_risk_level = "high"
        elif ai_risk_score >= 40:
            ai_risk_level = "medium"
        else:
            ai_risk_level = "low"
        internal_links, weak_anchor_count, anchor_total, external_links, external_nofollow, external_follow = self._extract_anchor_data(
            final_url, soup, base_host
        )
        has_headers = bool(headers)
        content_encoding = (
            str(headers.get("Content-Encoding") or headers.get("content-encoding") or "").strip().lower()
            if has_headers
            else ""
        )
        compression_enabled = bool(content_encoding) if has_headers else None
        cache_control = (
            str(headers.get("Cache-Control") or headers.get("cache-control") or "").strip().lower()
            if has_headers
            else ""
        )
        etag = str(headers.get("ETag") or headers.get("etag") or "").strip() if has_headers else ""
        expires = str(headers.get("Expires") or headers.get("expires") or "").strip() if has_headers else ""
        cache_enabled = (("max-age" in cache_control) or bool(etag) or bool(expires)) if has_headers else None
        x_robots_tag = str(headers.get("X-Robots-Tag") or headers.get("x-robots-tag") or "").strip() if has_headers else ""
        last_modified = str(headers.get("Last-Modified") or headers.get("last-modified") or "").strip() if has_headers else ""
        content_freshness_days = self._content_freshness_days(last_modified)
        is_https = urlparse(final_url).scheme.lower() == "https"
        og_tags = len(soup.find_all("meta", attrs={"property": lambda v: str(v).lower().startswith("og:") if v else False}))
        js_count = len(soup.find_all("script"))
        external_script_tags = [tag for tag in soup.find_all("script", src=True)]
        js_assets_count = len(external_script_tags)
        css_assets_count = len(
            [
                tag
                for tag in soup.find_all("link", href=True)
                if "stylesheet" in [str(x).lower() for x in (tag.get("rel") or [])]
            ]
        )
        render_blocking_js_count = len(
            [
                tag
                for tag in soup.find_all("script", src=True)
                if (
                    not tag.get("async")
                    and not tag.get("defer")
                    and bool(tag.find_parent("head"))
                )
            ]
        )
        preload_hints_count = len(
            [
                tag
                for tag in soup.find_all("link", href=True)
                if "preload" in [str(x).lower() for x in (tag.get("rel") or [])]
            ]
        )
        js_dependence = js_count >= 8
        has_main_tag = bool(soup.find("main"))
        cloaking_detected = _detect_cloaking(
            body_text=body_text,
            hidden_content=hidden_content,
            hidden_nodes_count=hidden_nodes_count,
            hidden_text_chars=hidden_text_chars,
        )
        has_contact_info = _detect_contact_info(body_text)
        has_legal_docs = _detect_legal_docs(body_text)
        has_author_info = _detect_author_info(soup, body_text)
        has_reviews = _detect_reviews(soup, body_text)
        trust_badges = _detect_trust_badges(body_text)
        cta_text_quality = _cta_text_quality(soup)
        total_links = anchor_total
        follow_links_total = 0
        nofollow_links_total = 0
        for tag in soup.find_all("a", href=True):
            rel_values = [r.strip().lower() for r in (tag.get("rel") or []) if isinstance(r, str)]
            if "nofollow" in rel_values:
                nofollow_links_total += 1
            else:
                follow_links_total += 1

        parsed_url = urlparse(final_url or source_url)
        query_params = parse_qs(parsed_url.query, keep_blank_values=True)
        url_params_count = len(query_params)
        path_depth = len([seg for seg in (parsed_url.path or "").split("/") if seg])
        crawl_budget_risk = "low"
        if url_params_count >= 3 or path_depth >= 5:
            crawl_budget_risk = "high"
        elif url_params_count >= 1 or path_depth >= 3:
            crawl_budget_risk = "medium"

        perf_penalty = 0.0
        perf_penalty += max(0.0, (response_time_ms - 800) / 120.0)
        perf_penalty += max(0.0, ((html_size_bytes / 1024.0) - 180.0) / 12.0)
        perf_penalty += max(0.0, (dom_nodes_count - 1800) / 220.0)
        perf_penalty += render_blocking_js_count * 2.0
        perf_light_score = round(max(0.0, min(100.0, 100.0 - perf_penalty)), 1)

        csp_present = bool(str(headers.get("Content-Security-Policy") or headers.get("content-security-policy") or "").strip())
        hsts_present = bool(str(headers.get("Strict-Transport-Security") or headers.get("strict-transport-security") or "").strip())
        x_frame_options_present = bool(str(headers.get("X-Frame-Options") or headers.get("x-frame-options") or "").strip())
        referrer_policy_present = bool(str(headers.get("Referrer-Policy") or headers.get("referrer-policy") or "").strip())
        permissions_policy_present = bool(str(headers.get("Permissions-Policy") or headers.get("permissions-policy") or "").strip())
        mixed_content_count = len(re.findall(r"""(?:src|href)\s*=\s*["']http://""", html or "", flags=re.I)) if is_https else 0
        security_headers_score = round(
            (
                (20.0 if csp_present else 0.0)
                + (20.0 if hsts_present else 0.0)
                + (20.0 if x_frame_options_present else 0.0)
                + (20.0 if referrer_policy_present else 0.0)
                + (20.0 if permissions_policy_present else 0.0)
                - min(20.0, mixed_content_count * 2.0)
            ),
            1,
        )

        issues: List[SiteAuditProIssue] = []
        penalty = 0.0
        if status_code >= 400:
            issues.append(
                SiteAuditProIssue(
                    severity="critical",
                    code="http_status_error",
                    title="HTTP status indicates page error",
                    details=f"Status code: {status_code}",
                )
            )
            penalty += 60
        if not title:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="missing_title",
                    title="Title is missing",
                    details="Page has no <title>.",
                )
            )
            penalty += 20
        if detailed_checks and title_tags_count > 1:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="multiple_title_tags",
                    title="Multiple <title> tags found",
                    details=f"Count: {title_tags_count}",
                )
            )
            penalty += 6
        if not description.strip():
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="missing_meta_description",
                    title="Meta description is missing",
                    details="Page has no <meta name='description'>.",
                )
            )
            penalty += 8
        if detailed_checks and meta_description_tags_count > 1:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="multiple_meta_descriptions",
                    title="Multiple meta description tags found",
                    details=f"Count: {meta_description_tags_count}",
                )
            )
            penalty += 4
        if detailed_checks and not charset_declared:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="missing_charset_meta",
                    title="Charset meta declaration is missing",
                )
            )
            penalty += 2
        if detailed_checks and not viewport:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="missing_viewport_meta",
                    title="Viewport meta declaration is missing",
                )
            )
            penalty += 2
        if detailed_checks and multiple_meta_robots:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="multiple_meta_robots",
                    title="Multiple meta robots tags found",
                )
            )
            penalty += 3
        if "noindex" in robots:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="noindex_detected",
                    title="Page contains noindex directive",
                    details=f"meta robots: {robots}",
                )
            )
            penalty += 15
        if not canonical:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="missing_canonical",
                    title="Canonical link is missing",
                )
            )
            penalty += 5
        if len(words) < 120:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="thin_content",
                    title="Thin content detected",
                    details=f"Word count: {len(words)}",
                )
            )
            penalty += 10
        if detailed_checks and perf_light_score < 60:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="light_perf_low_score",
                    title="Low lightweight performance score",
                    details=f"Score: {perf_light_score}",
                )
            )
            penalty += 6
        if h1_count != 1:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="h1_hierarchy_issue",
                    title="H1 hierarchy issue",
                    details=f"H1 count: {h1_count}",
                )
            )
            penalty += 7
        if compression_enabled is False:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="compression_disabled",
                    title="Response compression is not detected",
                )
            )
            penalty += 4
        if cache_enabled is False:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="cache_disabled",
                    title="Cache hints are missing in response headers",
                )
            )
            penalty += 3
        if not is_https:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="non_https_url",
                    title="Page is not served over HTTPS",
                )
            )
            penalty += 12
        if detailed_checks and (images_count := len(images)):
            modern_ratio = (images_modern_format_count / max(1, images_count)) * 100.0
            if modern_ratio < 20.0:
                issues.append(
                    SiteAuditProIssue(
                        severity="info",
                        code="low_modern_image_formats",
                        title="Low usage of WebP/AVIF image formats",
                        details=f"Modern formats: {images_modern_format_count}/{images_count}",
                    )
                )
                penalty += 2
        if detailed_checks and image_duplicate_src_count > 0:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="duplicate_image_sources",
                    title="Duplicate image sources found on page",
                    details=f"Duplicate sources: {image_duplicate_src_count}",
                )
            )
            penalty += 2
        if detailed_checks and generic_alt_count > 0:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="generic_alt_texts",
                    title="Generic image alt texts found",
                    details=f"Generic alt count: {generic_alt_count}",
                )
            )
            penalty += 2
        if detailed_checks and decorative_non_empty_alt_count > 0:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="decorative_images_with_alt",
                    title="Decorative images should have empty alt",
                    details=f"Decorative with non-empty alt: {decorative_non_empty_alt_count}",
                )
            )
            penalty += 2
        if detailed_checks and crawl_budget_risk == "high":
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="crawl_budget_risk_high",
                    title="High crawl budget risk for URL pattern",
                    details=f"params={url_params_count}, depth={path_depth}",
                )
            )
            penalty += 4
        elif detailed_checks and crawl_budget_risk == "medium":
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="crawl_budget_risk_medium",
                    title="Medium crawl budget risk for URL pattern",
                    details=f"params={url_params_count}, depth={path_depth}",
                )
            )
            penalty += 1
        if detailed_checks and structured_error_codes:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="structured_data_common_errors",
                    title="Common structured data errors detected",
                    details=", ".join(structured_error_codes[:8]),
                )
            )
            penalty += min(12, len(structured_error_codes) * 2)
        if detailed_checks and ai_risk_score >= 70:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="ai_risk_high",
                    title="High AI-text risk signals detected",
                    details=f"risk={ai_risk_score}, density={ai_markers_density_1k}/1k",
                )
            )
            penalty += 4
        if detailed_checks and hidden_content:
            issues.append(
                SiteAuditProIssue(
                    severity="warning",
                    code="hidden_content_css",
                    title="Hidden content detected (CSS/ARIA/small font)",
                    details=f"nodes={hidden_nodes_count}, hidden_text_chars={hidden_text_chars}"
                    + ("\n" + "\n".join(hidden_text_snippets[:5]) if hidden_text_snippets else ""),
                )
            )
            penalty += min(10, max(2, hidden_nodes_count // 2))
        if detailed_checks and cloaking_detected:
            issues.append(
                SiteAuditProIssue(
                    severity="critical",
                    code="cloaking_detected",
                    title="Potential cloaking risk detected",
                    details=f"nodes={hidden_nodes_count}, hidden_text_chars={hidden_text_chars}"
                    + ("\n" + "\n".join(hidden_text_snippets[:5]) if hidden_text_snippets else ""),
                )
            )
            penalty += 20
        if detailed_checks and cta_count == 0 and page_type in {"home", "service", "product", "category"}:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="cta_missing",
                    title="No conversion CTA detected",
                )
            )
            penalty += 2
        if detailed_checks and len(words) >= 600 and lists_count == 0 and tables_count == 0:
            issues.append(
                SiteAuditProIssue(
                    severity="info",
                    code="no_lists_tables_on_long_content",
                    title="Long content has no lists/tables",
                    details=f"word_count={len(words)}",
                )
            )
            penalty += 1

        indexable = ("noindex" not in robots and status_code < 400)
        if status_code >= 400:
            indexability_reason = "http_error"
        elif "noindex" in robots:
            indexability_reason = "meta_noindex"
        elif "noindex" in x_robots_tag.lower():
            indexability_reason = "x_robots_noindex"
        elif canonical_status == "external":
            indexability_reason = "canonical_external"
        else:
            indexability_reason = "indexable"

        health_score = max(0.0, round(100.0 - penalty, 1))
        trust_score = round(
            min(
                100.0,
                5.0
                + (20.0 if has_contact_info else 0.0)
                + (20.0 if has_legal_docs else 0.0)
                + (25.0 if has_reviews else 0.0)
                + (30.0 if trust_badges else 0.0),
            ),
            1,
        )
        eeat_components = {
            "expertise": round(min(20.0, 6.0 + (12.0 if has_author_info else 0.0)), 1),
            "authoritativeness": round(min(30.0, 22.0 + (8.0 if has_reviews else 0.0) + (5.0 if trust_badges else 0.0)), 1),
            "trustworthiness": round(
                30.0 if (has_contact_info and has_legal_docs) else (15.0 if (has_contact_info or has_legal_docs) else 0.0),
                1,
            ),
            "experience": round(20.0 if (len(words) >= 300 and has_author_info) else 0.0, 1),
        }
        eeat_score = round(min(100.0, sum(float(v) for v in eeat_components.values())), 1)

        row = NormalizedSiteAuditRow(
            url=source_url,
            final_url=final_url,
            status_code=status_code,
            status_line=(status_line or "").strip() or None,
            response_time_ms=response_time_ms,
            response_headers_count=len(headers or {}),
            html_size_bytes=html_size_bytes,
            content_kb=round((html_size_bytes or 0) / 1024.0, 1),
            dom_nodes_count=dom_nodes_count,
            js_assets_count=js_assets_count,
            css_assets_count=css_assets_count,
            render_blocking_js_count=render_blocking_js_count,
            preload_hints_count=preload_hints_count,
            perf_light_score=perf_light_score,
            redirect_count=redirect_count,
            is_https=is_https,
            compression_enabled=compression_enabled,
            compression_algorithm=content_encoding or None,
            cache_enabled=cache_enabled,
            cache_control=cache_control or None,
            last_modified=last_modified or None,
            content_freshness_days=content_freshness_days,
            indexable=indexable,
            indexability_reason=indexability_reason,
            health_score=health_score,
            title=title,
            title_tags_count=title_tags_count,
            title_len=len(title),
            meta_description=description.strip(),
            meta_description_tags_count=meta_description_tags_count,
            description_len=len(description.strip()),
            charset_declared=charset_declared,
            viewport_declared=bool(viewport),
            multiple_meta_robots=multiple_meta_robots,
            canonical=canonical.strip(),
            canonical_status=canonical_status,
            meta_robots=robots,
            x_robots_tag=x_robots_tag or None,
            breadcrumbs=breadcrumbs,
            structured_data=structured_data_total,
            structured_data_detail=structured_data_detail,
            structured_types=structured_types,
            structured_errors_count=len(structured_error_codes),
            structured_error_codes=structured_error_codes,
            schema_count=schema_count,
            hreflang_count=hreflang_count,
            hreflang_langs=hreflang_langs[:25],
            hreflang_has_x_default=hreflang_has_x_default,
            hreflang_targets=hreflang_targets,
            mobile_friendly_hint=("width=device-width" in viewport) if viewport else None,
            word_count=len(words),
            unique_word_count=unique_word_count,
            keyword_stuffing_score=keyword_stuffing_score,
            top_keywords=top_keywords,
            keyword_density_profile=keyword_density_profile,
            lexical_diversity=lexical_diversity,
            unique_percent=_unique_percent(body_text),
            readability_score=readability_score,
            avg_sentence_length=avg_sentence_length,
            avg_word_length=avg_word_length,
            complex_words_percent=complex_words_percent,
            content_density=content_density,
            boilerplate_percent=boilerplate_percent,
            toxicity_score=toxicity_score,
            filler_ratio=filler_ratio,
            heading_distribution=heading_distribution,
            h_hierarchy=h_hierarchy,
            h_errors=h_errors,
            h_details=h_details,
            semantic_tags_count=semantic_tags_count,
            html_quality_score=round(
                max(0.0, min(100.0, 50.0 + min(30.0, semantic_tags_count * 2.5) + min(20.0, content_density * 0.2))),
                1,
            ),
            deprecated_tags=deprecated_tags,
            hidden_content=hidden_content,
            hidden_nodes_count=hidden_nodes_count,
            hidden_text_chars=hidden_text_chars,
            hidden_text_snippets=hidden_text_snippets,
            cta_count=cta_count,
            cta_text_quality=cta_text_quality,
            lists_count=lists_count,
            tables_count=tables_count,
            faq_count=faq_count,
            h1_count=h1_count,
            h1_text=h1_text,
            images_count=len(images),
            images_without_alt=images_without_alt,
            images_no_alt=images_without_alt,
            images_modern_format_count=images_modern_format_count,
            images_external_count=images_external_count,
            image_duplicate_src_count=image_duplicate_src_count,
            generic_alt_count=generic_alt_count,
            decorative_non_empty_alt_count=decorative_non_empty_alt_count,
            images_optimization={
                "total": len(images),
                "no_alt": images_without_alt,
                "no_width_height": images_no_width_height,
                "no_lazy_load": images_no_lazy_load,
            },
            outgoing_internal_links=len(internal_links),
            outgoing_external_links=external_links,
            external_nofollow_links=external_nofollow,
            external_follow_links=external_follow,
            follow_links_total=follow_links_total,
            nofollow_links_total=nofollow_links_total,
            total_links=total_links,
            weak_anchor_ratio=round((weak_anchor_count / anchor_total), 3) if anchor_total else 0.0,
            anchor_text_quality_score=round(max(0.0, 100.0 - (((weak_anchor_count / anchor_total) * 100.0) if anchor_total else 0.0)), 1),
            ai_markers_count=ai_markers_count,
            ai_markers_list=ai_markers_list,
            ai_marker_sample=ai_marker_sample or None,
            ai_markers_density_1k=ai_markers_density_1k,
            ai_risk_score=ai_risk_score,
            ai_risk_level=ai_risk_level,
            ai_false_positive_guard=ai_false_positive_guard,
            page_type=page_type,
            filler_phrases=filler_phrases,
            url_params_count=url_params_count,
            path_depth=path_depth,
            crawl_budget_risk=crawl_budget_risk,
            csp_present=csp_present,
            hsts_present=hsts_present,
            x_frame_options_present=x_frame_options_present,
            referrer_policy_present=referrer_policy_present,
            permissions_policy_present=permissions_policy_present,
            mixed_content_count=mixed_content_count,
            security_headers_score=security_headers_score,
            og_tags=og_tags,
            js_dependence=js_dependence,
            has_main_tag=has_main_tag,
            cloaking_detected=cloaking_detected,
            has_contact_info=has_contact_info,
            has_legal_docs=has_legal_docs,
            has_author_info=has_author_info,
            has_reviews=has_reviews,
            trust_badges=trust_badges,
            trust_score=trust_score,
            eeat_components=eeat_components,
            eeat_score=eeat_score,
            compression=compression_enabled,
            all_issues=[i.code for i in issues],
            issues=issues,
        )
        return row, internal_links, body_text, weak_anchor_count, anchor_total

    def _calculate_site_health_scores(self, rows: List[NormalizedSiteAuditRow], incoming_counts: Counter) -> None:
        calculate_site_health_scores(rows=rows, incoming_counts=incoming_counts)

    def run(
        self,
        url: str,
        mode: str = "quick",
        max_pages: int = 5,
        batch_urls: List[str] | None = None,
        batch_mode: bool = False,
        extended_hreflang_checks: bool = False,
        progress_callback: Optional[Callable[[int, str, Optional[Dict[str, Any]]], None]] = None,
        use_proxy: bool = False,
    ) -> NormalizedSiteAuditPayload:
        def notify(progress: int, message: str, meta: Optional[Dict[str, Any]] = None) -> None:
            if callable(progress_callback):
                progress_callback(progress, message, meta)

        selected_mode = "full" if mode == "full" else "quick"
        page_limit = max(1, min(int(max_pages or 5), 5000))
        timeout = 12

        start_url = self._normalize_url(url)
        base_host = urlparse(start_url).netloc
        if not base_host:
            raise ValueError("Invalid URL for Site Audit Pro")

        prepared_batch_urls: List[str] = []
        if batch_urls:
            seen_batch: Set[str] = set()
            for raw in batch_urls:
                normalized = self._normalize_url(raw)
                if not normalized or normalized in seen_batch:
                    continue
                seen_batch.add(normalized)
                prepared_batch_urls.append(normalized)

        effective_batch_mode = bool(batch_mode and prepared_batch_urls)
        sitemap_scope: Dict[str, Any] = {
            "sample_urls": [],
            "sample_set": set(),
            "notes": [],
            "source": None,
            "root_sitemaps": [],
            "sitemaps_scanned": 0,
            "urls_discovered": 0,
            "truncated": False,
        }
        visited: Set[str] = set()
        depth_by_url: Dict[str, int] = {}
        if effective_batch_mode:
            queue = deque(prepared_batch_urls)
            for u in prepared_batch_urls:
                depth_by_url[self._normalize_url(u)] = 0
        else:
            from .network import build_session
            session = build_session(use_proxy)
            sitemap_scope = sample_site_urls_from_sitemaps(
                site_url=start_url,
                session=session,
                page_limit=page_limit,
                timeout=12,
            )
            seed_urls = [start_url]
            for candidate in sitemap_scope.get("sample_urls") or []:
                normalized_candidate = self._normalize_url(candidate)
                if normalized_candidate and normalized_candidate not in seed_urls:
                    seed_urls.append(normalized_candidate)
                    depth_by_url[normalized_candidate] = 1
            queue = deque(seed_urls)
            depth_by_url[self._normalize_url(start_url)] = 0
        if effective_batch_mode:
            from .network import build_session
            session = build_session(use_proxy)
        rows: List[NormalizedSiteAuditRow] = []
        titles_by_url: Dict[str, str] = {}
        descriptions_by_url: Dict[str, str] = {}
        title_counter: Counter = Counter()
        desc_counter: Counter = Counter()
        crawl_errors: List[str] = []
        link_graph: Dict[str, Set[str]] = defaultdict(set)
        incoming_counts: Counter = Counter()
        page_texts: Dict[str, str] = {}
        anchor_quality_raw: Dict[str, Tuple[int, int]] = {}
        # Broken link checking: link_url -> set of pages where it was found
        all_discovered_links: Dict[str, Set[str]] = defaultdict(set)
        # Image analysis: collect unique image URLs per page
        all_image_urls: Dict[str, Set[str]] = defaultdict(set)
        all_image_urls_global: Set[str] = set()

        total_target = len(prepared_batch_urls) if effective_batch_mode else page_limit
        total_target = max(1, total_target)

        while queue and len(visited) < page_limit:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            current_norm = self._normalize_url(current)
            current_depth = int(depth_by_url.get(current_norm, 0))

            try:
                response = session.get(current, timeout=timeout, allow_redirects=True)
                page_result = build_crawl_page_success(
                    current_url=current,
                    response=response,
                    timeout=timeout,
                    base_host=base_host,
                    detailed_checks=(selected_mode == "full"),
                    normalize_url=self._normalize_url,
                    build_row=self._build_row,
                    extract_all_links=self._extract_all_links,
                    extract_image_urls=self._extract_image_urls,
                )
                row = page_result.row
                rows.append(row)
                from .crawl_state import record_page_state

                record_page_state(
                    row=row,
                    page_result=page_result,
                    current_depth=current_depth,
                    page_limit=page_limit,
                    effective_batch_mode=effective_batch_mode,
                    visited=visited,
                    queue=queue,
                    depth_by_url=depth_by_url,
                    incoming_counts=incoming_counts,
                    link_graph=link_graph,
                    titles_by_url=titles_by_url,
                    descriptions_by_url=descriptions_by_url,
                    title_counter=title_counter,
                    desc_counter=desc_counter,
                    page_texts=page_texts,
                    anchor_quality_raw=anchor_quality_raw,
                    all_discovered_links=all_discovered_links,
                    all_image_urls=all_image_urls,
                    all_image_urls_global=all_image_urls_global,
                    normalize_url=self._normalize_url,
                )
            except Exception as exc:
                crawl_errors.append(f"{current}: {exc}")
                rows.append(build_crawl_page_failure(current_url=current, error=exc))
                link_graph[current] = set()
                page_texts[current] = ""
                anchor_quality_raw[current] = (0, 0)

            processed_pages = len(visited)
            loop_progress = 25 + int((processed_pages / total_target) * 45)
            loop_progress = max(25, min(70, loop_progress))
            notify(
                loop_progress,
                f"Processed pages: {processed_pages}/{total_target}",
                {
                    "processed_pages": processed_pages,
                    "total_pages": total_target,
                    "queue_size": len(queue),
                    "batch_mode": effective_batch_mode,
                    "current_url": current,
                },
            )

        # ── Broken Link Checking (Task 1.3) ──────────────────────────────
        _MAX_LINK_CHECK = 2000
        links_to_check = sorted(all_discovered_links.keys())
        broken_links_note: Optional[str] = None
        if len(links_to_check) > _MAX_LINK_CHECK:
            broken_links_note = f"Only first {_MAX_LINK_CHECK} of {len(links_to_check)} unique links were checked."
            links_to_check = links_to_check[:_MAX_LINK_CHECK]

        notify(72, "Checking links for broken URLs…")
        link_check_results = self._check_links_batch(links_to_check, session) if links_to_check else []

        broken_links_data = build_broken_links_data(
            link_check_results=link_check_results,
            all_discovered_links=all_discovered_links,
        )
        if broken_links_note:
            broken_links_data["note"] = broken_links_note

        # ── Image Analysis (Task 1.4) ─────────────────────────────────
        _MAX_IMAGE_CHECK = 500
        total_images_found = len(all_image_urls_global)
        images_sample = sorted(all_image_urls_global)[:_MAX_IMAGE_CHECK]

        notify(78, "Analyzing images…")
        image_check_results = self._check_images_batch(images_sample, session) if images_sample else []

        image_analysis_data = build_image_analysis_data(
            image_check_results=image_check_results,
            total_images_found=total_images_found,
        )
        apply_image_analysis_issues(rows, image_analysis_data)
        apply_broken_link_issues(
            rows,
            broken_links_data=broken_links_data,
            link_graph=link_graph,
            all_discovered_links=all_discovered_links,
        )

        notify(82, "Analyzing duplicates and structure…")

        apply_duplicate_and_depth_signals(
            rows=rows,
            titles_by_url=titles_by_url,
            descriptions_by_url=descriptions_by_url,
            title_counter=title_counter,
            desc_counter=desc_counter,
            depth_by_url=depth_by_url,
            normalize_url=self._normalize_url,
            selected_mode=selected_mode,
            effective_batch_mode=effective_batch_mode,
        )

        homepage_row = find_homepage_row(rows, start_url=start_url, normalize_url=self._normalize_url)
        apply_homepage_security_signals(homepage_row, selected_mode=selected_mode)

        self._apply_canonical_and_hreflang_checks(
            rows=rows,
            start_url=start_url,
            extended_hreflang_checks=extended_hreflang_checks,
        )

        apply_near_duplicate_signals(rows=rows, page_texts=page_texts)

        normalized_graph, topic_clusters, semantic_suggestions = enrich_graph_metrics(
            rows=rows,
            link_graph=link_graph,
            incoming_counts=incoming_counts,
            page_texts=page_texts,
        )
        self._calculate_site_health_scores(rows=rows, incoming_counts=incoming_counts)

        apply_orphan_page_signals(rows)
        sitemap_sample_set = {self._normalize_url(item) for item in (sitemap_scope.get("sample_set") or set()) if item}
        for row in rows:
            normalized_candidates = {
                self._normalize_url(row.url),
                self._normalize_url(row.final_url or ""),
            }
            in_sitemap = any(candidate and candidate in sitemap_sample_set for candidate in normalized_candidates)
            row.in_sitemap = in_sitemap
            if in_sitemap:
                row.sitemap_source = str(sitemap_scope.get("source") or "sampled")
        finalize_rows(rows)

        summary = build_summary(rows, mode=selected_mode)

        artifacts = build_artifacts_payload(
            max_pages=max_pages,
            rows=rows,
            effective_batch_mode=effective_batch_mode,
            prepared_batch_urls=prepared_batch_urls,
            crawl_errors=crawl_errors,
            topic_clusters=topic_clusters,
            semantic_suggestions=semantic_suggestions,
            broken_links_data=broken_links_data,
            image_analysis_data=image_analysis_data,
            homepage_row=homepage_row,
            sitemap_scope={
                "source": sitemap_scope.get("source"),
                "root_sitemaps": sitemap_scope.get("root_sitemaps") or [],
                "sitemaps_scanned": int(sitemap_scope.get("sitemaps_scanned") or 0),
                "urls_discovered": int(sitemap_scope.get("urls_discovered") or 0),
                "sample_size": len(sitemap_scope.get("sample_urls") or []),
                "truncated": bool(sitemap_scope.get("truncated")),
                "notes": sitemap_scope.get("notes") or [],
            },
        )

        return NormalizedSiteAuditPayload(
            mode=selected_mode,
            summary=summary,
            rows=rows,
            artifacts=artifacts,
        )

    @staticmethod
    def to_public_results(normalized: NormalizedSiteAuditPayload) -> Dict[str, Any]:
        return build_public_results(normalized)

