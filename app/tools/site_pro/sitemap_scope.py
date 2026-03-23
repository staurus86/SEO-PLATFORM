"""Bounded sitemap discovery/sampling for Site Audit Pro."""
from __future__ import annotations

from collections import deque
import io
import re
from typing import Any, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests

from app.api.routers.robots_fetch import _decode_sitemap_payload


def _local_name(tag: str) -> str:
    value = str(tag or "")
    if "}" in value:
        return value.rsplit("}", 1)[-1]
    return value


def _normalize_site_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme:
        value = f"https://{value}"
        parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    clean = value.strip()
    if clean.endswith("/") and len(clean) > len(parsed.scheme) + 3:
        clean = clean.rstrip("/")
    return clean


def _looks_like_sitemap_payload(payload: bytes | str) -> bool:
    if isinstance(payload, bytes):
        head = payload[:20000].lstrip(b"\xef\xbb\xbf \n\r\t").lower()
        return head.startswith(b"<?xml") or b"<urlset" in head or b"<sitemapindex" in head
    head = str(payload or "").lstrip("\ufeff \n\r\t").lower()
    return head.startswith("<?xml") or "<urlset" in head or "<sitemapindex" in head


def _discover_sitemap_candidates(*, site_url: str, session: requests.Session, timeout: int) -> Tuple[List[str], Optional[str]]:
    normalized_site = _normalize_site_url(site_url)
    if not normalized_site:
        return [], None

    parsed_site = urlparse(normalized_site)
    root = f"{parsed_site.scheme}://{parsed_site.netloc}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEO-Tools/1.0)"}

    try:
        robots_response = session.get(urljoin(root, "/robots.txt"), timeout=timeout, headers=headers, allow_redirects=True)
        if int(getattr(robots_response, "status_code", 0) or 0) == 200:
            robots_candidates: List[str] = []
            for line in str(getattr(robots_response, "text", "") or "").splitlines():
                if not re.match(r"^\s*sitemap\s*:", line, flags=re.IGNORECASE):
                    continue
                raw_loc = line.split(":", 1)[1].strip() if ":" in line else ""
                normalized_loc = _normalize_site_url(urljoin(root + "/", raw_loc))
                if not normalized_loc:
                    continue
                try:
                    sitemap_response = session.get(normalized_loc, timeout=timeout, headers=headers, allow_redirects=True)
                    decoded_content, _ = _decode_sitemap_payload(
                        getattr(sitemap_response, "content", b""),
                        getattr(sitemap_response, "url", normalized_loc),
                        getattr(sitemap_response, "headers", {}),
                    )
                    if int(getattr(sitemap_response, "status_code", 0) or 0) == 200 and _looks_like_sitemap_payload(decoded_content):
                        robots_candidates.append(normalized_loc)
                except Exception:
                    continue
            if robots_candidates:
                return list(dict.fromkeys(robots_candidates))[:3], "robots.txt"
    except Exception:
        pass

    for path in (
        "/sitemap.xml",
        "/sitemap.xml.gz",
        "/sitemap_index.xml",
        "/sitemap_index.xml.gz",
        "/sitemap-index.xml",
        "/sitemap-index.xml.gz",
        "/sitemaps.xml",
        "/sitemaps.xml.gz",
        "/wp-sitemap.xml",
        "/wp-sitemap.xml.gz",
    ):
        candidate = urljoin(root, path)
        try:
            sitemap_response = session.get(candidate, timeout=timeout, headers=headers, allow_redirects=True)
            decoded_content, _ = _decode_sitemap_payload(
                getattr(sitemap_response, "content", b""),
                getattr(sitemap_response, "url", candidate),
                getattr(sitemap_response, "headers", {}),
            )
            if int(getattr(sitemap_response, "status_code", 0) or 0) == 200 and _looks_like_sitemap_payload(decoded_content):
                return [candidate], "common_path"
        except Exception:
            continue

    return [], None


def _iter_sitemap_locations(xml_text: str | bytes) -> Tuple[Optional[str], List[str]]:
    root_tag: Optional[str] = None
    locations: List[str] = []
    try:
        if isinstance(xml_text, bytes):
            xml_text = xml_text.decode("utf-8", errors="replace")
        context = ET.iterparse(io.StringIO(xml_text), events=("start", "end"))
        for event, elem in context:
            tag_name = _local_name(elem.tag).lower()
            if event == "start" and root_tag is None:
                root_tag = tag_name
            if event == "end" and tag_name == "loc":
                value = (elem.text or "").strip()
                if value:
                    locations.append(value)
                elem.clear()
    except ET.ParseError:
        return None, []
    return root_tag, locations


def sample_site_urls_from_sitemaps(
    *,
    site_url: str,
    session: requests.Session,
    page_limit: int,
    timeout: int = 12,
) -> Dict[str, Any]:
    """
    Discover a bounded sample of sitemap URLs for Site Audit Pro.

    Guarantees:
    - never raises for discovery/fetch/parse failures
    - hard-caps number of sitemap files and URLs processed
    - falls back to normal crawl when sitemap is unavailable or oversized
    """
    normalized_site = _normalize_site_url(site_url)
    if not normalized_site:
        return {
            "sample_urls": [],
            "sample_set": set(),
            "notes": ["Sitemap discovery skipped: invalid site URL."],
            "source": None,
            "root_sitemaps": [],
            "sitemaps_scanned": 0,
            "urls_discovered": 0,
            "truncated": False,
        }

    parsed_site = urlparse(normalized_site)
    root = f"{parsed_site.scheme}://{parsed_site.netloc}"
    base_host = parsed_site.netloc
    max_root_sitemaps = 3
    max_sitemap_files = max(4, min(32, page_limit * 2))
    max_sample_urls = max(10, min(200, page_limit * 4))

    notes: List[str] = []
    truncated = False
    scanned = 0

    try:
        root_sitemaps, source = _discover_sitemap_candidates(site_url=normalized_site, session=session, timeout=timeout)
    except Exception as exc:
        return {
            "sample_urls": [],
            "sample_set": set(),
            "notes": [f"Sitemap discovery failed, continuing without sitemap seed: {exc}"],
            "source": None,
            "root_sitemaps": [],
            "sitemaps_scanned": 0,
            "urls_discovered": 0,
            "truncated": False,
        }

    if not root_sitemaps:
        return {
            "sample_urls": [],
            "sample_set": set(),
            "notes": ["Sitemap not found, continuing with link-based crawl only."],
            "source": source,
            "root_sitemaps": [],
            "sitemaps_scanned": 0,
            "urls_discovered": 0,
            "truncated": False,
        }

    queue: Deque[str] = deque(root_sitemaps[:max_root_sitemaps])
    visited_sitemaps: Set[str] = set()
    sampled_urls: List[str] = []
    sampled_set: Set[str] = set()
    seen_child_sitemaps: Set[str] = set(queue)

    while queue and scanned < max_sitemap_files and len(sampled_urls) < max_sample_urls:
        sitemap_url = queue.popleft()
        if not sitemap_url or sitemap_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sitemap_url)
        scanned += 1

        try:
            response = session.get(sitemap_url, timeout=timeout, allow_redirects=True)
            decoded_content, _ = _decode_sitemap_payload(
                getattr(response, "content", b""),
                getattr(response, "url", sitemap_url),
                getattr(response, "headers", {}),
            )
        except Exception as exc:
            notes.append(f"Sitemap fetch skipped for {sitemap_url}: {exc}")
            continue

        if int(getattr(response, "status_code", 0) or 0) != 200:
            notes.append(f"Sitemap fetch skipped for {sitemap_url}: HTTP {getattr(response, 'status_code', 'n/a')}")
            continue

        root_tag, locations = _iter_sitemap_locations(decoded_content)
        if not root_tag:
            notes.append(f"Sitemap parse skipped for {sitemap_url}: invalid XML.")
            continue

        if root_tag == "sitemapindex":
            for loc in locations:
                normalized_loc = _normalize_site_url(urljoin(root + "/", loc))
                if not normalized_loc or normalized_loc in seen_child_sitemaps:
                    continue
                if urlparse(normalized_loc).netloc != base_host:
                    continue
                seen_child_sitemaps.add(normalized_loc)
                if len(visited_sitemaps) + len(queue) >= max_sitemap_files:
                    truncated = True
                    continue
                queue.append(normalized_loc)
            continue

        if root_tag != "urlset":
            notes.append(f"Sitemap parse skipped for {sitemap_url}: unsupported root tag {root_tag}.")
            continue

        for loc in locations:
            normalized_loc = _normalize_site_url(urljoin(root + "/", loc))
            if not normalized_loc:
                continue
            parsed_loc = urlparse(normalized_loc)
            if parsed_loc.netloc != base_host:
                continue
            if normalized_loc in sampled_set:
                continue
            sampled_set.add(normalized_loc)
            sampled_urls.append(normalized_loc)
            if len(sampled_urls) >= max_sample_urls:
                truncated = True
                break

    if queue:
        truncated = True
    if truncated:
        notes.append(
            f"Sitemap sampling was capped at {max_sitemap_files} files and {max_sample_urls} URLs to keep the crawl stable."
        )

    return {
        "sample_urls": sampled_urls,
        "sample_set": sampled_set,
        "notes": notes,
        "source": source,
        "root_sitemaps": root_sitemaps[:max_root_sitemaps],
        "sitemaps_scanned": scanned,
        "urls_discovered": len(sampled_urls),
        "truncated": truncated,
    }
