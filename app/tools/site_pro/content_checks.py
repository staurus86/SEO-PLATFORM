"""Content analysis and checks for Site Audit Pro."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Set, Tuple

from bs4 import BeautifulSoup

from .constants import BOILERPLATE_PATTERNS, STOP_WORDS, TOKEN_LONG_RE
from .text_analysis import _tokenize_long


def _heading_distribution(soup: BeautifulSoup) -> Dict[str, int]:
    return {f"h{i}": len(soup.find_all(f"h{i}")) for i in range(1, 7)}


def _semantic_tags_count(soup: BeautifulSoup) -> int:
    return len(soup.find_all(["main", "article", "section", "aside", "nav", "header", "footer"]))


def _content_density(soup: BeautifulSoup, text: str) -> float:
    text_words = len((text or "").split())
    total_words = len(soup.get_text(" ", strip=True).split())
    if total_words <= 0:
        return 0.0
    return round((text_words / total_words) * 100.0, 2)


def _boilerplate_percent(text: str) -> float:
    raw = text or ""
    if not raw:
        return 0.0
    total = len(raw.split())
    if total <= 0:
        return 0.0
    matches = sum(len(re.findall(pattern, raw, flags=re.I)) for pattern in BOILERPLATE_PATTERNS)
    score = min(100.0, (matches / total) * 100.0) if total > 0 else 0.0
    return round(score, 1)


def _unique_percent(text: str) -> float:
    if not text:
        return 0.0
    words = [word.lower() for word in TOKEN_LONG_RE.findall(text) if len(word) > 2]
    if not words:
        return 0.0
    filtered = [word for word in words if word not in STOP_WORDS]
    if not filtered:
        return 0.0
    return round(min(100.0, max(0.0, (len(set(filtered)) / len(filtered)) * 100.0)), 1)


def _structured_type_label(raw_type: Any) -> str:
    value = str(raw_type or "").strip()
    if not value:
        return ""
    if "/" in value or "#" in value:
        value = re.split(r"[/#]", value)[-1]
    return value[:120]


def _detect_structured_data(soup: BeautifulSoup) -> Tuple[int, Dict[str, int], List[str]]:
    json_ld_tags = soup.find_all("script", attrs={"type": lambda v: str(v).lower().strip() == "application/ld+json"})
    microdata_items = soup.find_all(attrs={"itemtype": True})
    rdfa_items = soup.find_all(attrs={"typeof": True})
    detail = {
        "json_ld": len(json_ld_tags),
        "microdata": len(microdata_items),
        "rdfa": len(rdfa_items),
    }
    types: Set[str] = set()
    for obj in _extract_jsonld_objects(soup):
        raw_type = obj.get("@type")
        if isinstance(raw_type, list):
            for item in raw_type:
                label = _structured_type_label(item)
                if label:
                    types.add(label)
        else:
            label = _structured_type_label(raw_type)
            if label:
                types.add(label)
    for item in microdata_items:
        itemtype = str(item.get("itemtype") or "").strip()
        if itemtype:
            label = _structured_type_label(itemtype)
            if label:
                types.add(label)
    for item in rdfa_items:
        typeof = str(item.get("typeof") or "").strip()
        for part in re.split(r"\s+", typeof):
            label = _structured_type_label(part)
            if label:
                types.add(label)
    return detail["json_ld"] + detail["microdata"] + detail["rdfa"], detail, sorted(types)[:15]


def _extract_jsonld_objects(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    objects: List[Dict[str, Any]] = []
    tags = soup.find_all("script", attrs={"type": lambda v: str(v).lower().strip() == "application/ld+json"})
    for tag in tags:
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        stack: List[Any] = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, list):
                stack.extend(current)
                continue
            if not isinstance(current, dict):
                continue
            objects.append(current)
            graph = current.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return objects


def _jsonld_types(obj: Dict[str, Any]) -> Set[str]:
    raw = obj.get("@type")
    if isinstance(raw, list):
        return {str(x).strip().lower() for x in raw if str(x).strip()}
    if isinstance(raw, str):
        val = raw.strip().lower()
        return {val} if val else set()
    return set()


def _validate_structured_common(soup: BeautifulSoup) -> List[str]:
    codes: List[str] = []
    objects = _extract_jsonld_objects(soup)
    for obj in objects:
        types = _jsonld_types(obj)
        if not types:
            continue

        if "product" in types:
            if not str(obj.get("name") or "").strip():
                codes.append("product_missing_name")
            offers = obj.get("offers")
            offer_items = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
            if not offer_items:
                codes.append("product_missing_offers")
            else:
                has_price = any(str((item or {}).get("price") or "").strip() for item in offer_items if isinstance(item, dict))
                has_currency = any(
                    str((item or {}).get("priceCurrency") or "").strip()
                    for item in offer_items
                    if isinstance(item, dict)
                )
                if not has_price:
                    codes.append("product_missing_price")
                if not has_currency:
                    codes.append("product_missing_price_currency")

        if types.intersection({"article", "newsarticle", "blogposting"}):
            if not str(obj.get("headline") or "").strip():
                codes.append("article_missing_headline")
            if not str(obj.get("datePublished") or "").strip():
                codes.append("article_missing_date_published")
            if not obj.get("author"):
                codes.append("article_missing_author")

        if types.intersection({"organization", "localbusiness"}):
            if not str(obj.get("name") or "").strip():
                codes.append("organization_missing_name")
            if not str(obj.get("url") or "").strip():
                codes.append("organization_missing_url")

        if "breadcrumblist" in types:
            item_list = obj.get("itemListElement")
            if not item_list or (isinstance(item_list, list) and len(item_list) == 0):
                codes.append("breadcrumb_missing_item_list")

        if "faqpage" in types:
            main_entity = obj.get("mainEntity")
            if not main_entity or (isinstance(main_entity, list) and len(main_entity) == 0):
                codes.append("faq_missing_main_entity")

    return sorted(set(codes))


def _simhash64(text: str) -> int:
    tokens = [t for t in _tokenize_long(text, min_len=3) if t not in STOP_WORDS]
    if not tokens:
        return 0
    tf = Counter(tokens)
    vector = [0] * 64
    for token, weight in tf.items():
        h = int(hashlib.md5(token.encode("utf-8", errors="ignore")).hexdigest()[:16], 16)
        for i in range(64):
            if (h >> i) & 1:
                vector[i] += weight
            else:
                vector[i] -= weight
    value = 0
    for i, score in enumerate(vector):
        if score >= 0:
            value |= (1 << i)
    return value


def _hamming64(a: int, b: int) -> int:
    return int((a ^ b).bit_count())


def _detect_breadcrumbs(soup: BeautifulSoup) -> bool:
    if soup.find(attrs={"itemtype": re.compile("BreadcrumbList", re.I)}):
        return True
    for nav in soup.find_all("nav"):
        aria = (nav.get("aria-label") or "").lower()
        if "breadcrumb" in aria:
            return True
    return False


def _content_freshness_days(last_modified: str) -> int | None:
    value = (last_modified or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return None


def _detect_contact_info(text: str) -> bool:
    raw = (text or "").lower()
    if re.search(r"(\+?\d[\d\s\-\(\)]{7,}\d)|([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})", raw):
        return True
    contact_tokens = (
        "contact", "contacts", "phone", "call us", "support", "help center",
        "\u043a\u043e\u043d\u0442\u0430\u043a\u0442", "\u043a\u043e\u043d\u0442\u0430\u043a\u0442\u044b", "\u0442\u0435\u043b\u0435\u0444\u043e\u043d", "\u0441\u0432\u044f\u0437\u0430\u0442\u044c\u0441\u044f", "\u043e\u0431\u0440\u0430\u0442\u043d\u0430\u044f \u0441\u0432\u044f\u0437\u044c", "\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430",
        "\u0433\u043e\u0440\u044f\u0447\u0430\u044f \u043b\u0438\u043d\u0438\u044f", "\u0430\u0434\u0440\u0435\u0441", "email", "e-mail",
    )
    return any(token in raw for token in contact_tokens)


def _detect_legal_docs(text: str) -> bool:
    raw = (text or "").lower()
    legal_tokens = (
        "privacy", "privacy policy", "terms", "terms of use", "terms and conditions", "policy", "cookies", "gdpr",
        "ccpa", "refund policy", "shipping policy", "returns policy", "disclaimer", "public offer",
        "\u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0430 \u043a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u0438", "\u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0438 \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u044b\u0445 \u0434\u0430\u043d\u043d\u044b\u0445", "\u0443\u0441\u043b\u043e\u0432\u0438\u044f \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u044f",
        "\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u043e\u0435 \u0441\u043e\u0433\u043b\u0430\u0448\u0435\u043d\u0438\u0435", "\u043e\u0444\u0435\u0440\u0442\u0430", "\u043f\u0443\u0431\u043b\u0438\u0447\u043d\u0430\u044f \u043e\u0444\u0435\u0440\u0442\u0430", "\u0441\u043e\u0433\u043b\u0430\u0441\u0438\u0435 \u043d\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0443",
        "cookie", "\u043a\u0443\u043a\u0438", "\u0432\u043e\u0437\u0432\u0440\u0430\u0442", "\u0434\u043e\u0441\u0442\u0430\u0432\u043a\u0430", "\u043e\u0442\u043a\u0430\u0437 \u043e\u0442 \u043e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u043e\u0441\u0442\u0438",
    )
    return any(token in raw for token in legal_tokens)


def _detect_author_info(soup: BeautifulSoup, text: str) -> bool:
    raw = (text or "").lower()
    if soup.find(attrs={"rel": re.compile("author", re.I)}):
        return True
    if soup.find(attrs={"itemprop": re.compile("author", re.I)}):
        return True
    if soup.find(attrs={"class": re.compile(r"author|byline|editor|reviewed|\u044d\u043a\u0441\u043f\u0435\u0440\u0442|\u0430\u0432\u0442\u043e\u0440", re.I)}):
        return True
    author_tokens = (
        "author", "written by", "editor", "reviewed by", "fact checked",
        "\u0430\u0432\u0442\u043e\u0440", "\u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440", "\u043f\u0440\u043e\u0432\u0435\u0440\u0435\u043d\u043e", "\u044d\u043a\u0441\u043f\u0435\u0440\u0442", "\u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b \u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u0438\u043b",
    )
    return any(token in raw for token in author_tokens)


def _detect_reviews(soup: BeautifulSoup, text: str) -> bool:
    raw = (text or "").lower()
    if soup.find(attrs={"itemprop": re.compile("review|rating", re.I)}):
        return True
    if soup.find(attrs={"class": re.compile(r"review|rating|testimonial|otzyv|\u043e\u0442\u0437\u044b\u0432", re.I)}):
        return True
    review_tokens = (
        "review", "rating", "testimonial", "stars", "score", "customer stories",
        "\u043e\u0442\u0437\u044b\u0432", "\u043e\u0442\u0437\u044b\u0432\u044b", "\u0440\u0435\u0439\u0442\u0438\u043d\u0433", "\u043e\u0446\u0435\u043d\u043a\u0430", "\u043d\u0430\u043c \u0434\u043e\u0432\u0435\u0440\u044f\u044e\u0442", "\u043a\u0435\u0439\u0441\u044b \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432",
    )
    return any(token in raw for token in review_tokens)


def _detect_trust_badges(text: str) -> bool:
    raw = (text or "").lower()
    badge_tokens = (
        "secure", "verified", "ssl", "tls", "https", "guarantee", "trusted",
        "certified", "official partner", "money-back", "warranty", "iso", "pci dss",
        "\u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e", "\u0437\u0430\u0449\u0438\u0449\u0435\u043d\u043e", "\u043f\u0440\u043e\u0432\u0435\u0440\u0435\u043d\u043e", "\u0433\u0430\u0440\u0430\u043d\u0442\u0438\u044f", "\u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0439 \u043f\u0430\u0440\u0442\u043d\u0435\u0440",
        "\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u0446\u0438\u0440\u043e\u0432\u0430\u043d\u043e", "\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442", "\u043b\u0438\u0446\u0435\u043d\u0437\u0438\u044f",
    )
    return any(token in raw for token in badge_tokens)


def _cta_text_quality(soup: BeautifulSoup) -> float:
    buttons = soup.find_all(["a", "button"])
    if not buttons:
        return 0.0
    good = 0
    for tag in buttons:
        txt = (tag.get_text(" ", strip=True) or "").lower()
        if any(token in txt for token in ("buy", "start", "contact", "book", "sign", "register", "learn more")):
            good += 1
    return round((good / len(buttons)) * 100.0, 1)


def _h_hierarchy_summary(soup: BeautifulSoup, heading_distribution: Dict[str, int]) -> Tuple[str, List[str], Dict[str, Any]]:
    h1_count = int(heading_distribution.get("h1", 0))
    errors: List[str] = []
    heading_sequence = [int(tag.name[1]) for tag in soup.find_all(re.compile(r"^h[1-6]$", re.I))]
    heading_outline = [
        {
            "level": int(tag.name[1]),
            "text": (tag.get_text(" ", strip=True) or "")[:120],
        }
        for tag in soup.find_all(re.compile(r"^h[1-6]$", re.I))[:20]
    ]
    if h1_count == 0:
        errors.append("missing_h1")
    elif h1_count > 1:
        errors.append("multiple_h1")
    if heading_sequence and heading_sequence[0] != 1:
        errors.append("wrong_start")
    for prev, current in zip(heading_sequence, heading_sequence[1:]):
        if (current - prev) > 1:
            errors.append("heading_level_skip")
            break
    if not errors:
        status = "Good"
    elif "wrong_start" in errors:
        status = "Bad (wrong start)"
    elif "missing_h1" in errors:
        status = "Bad (missing h1)"
    elif "multiple_h1" in errors:
        status = "Bad (multiple h1)"
    elif "heading_level_skip" in errors:
        status = "Bad (level skip)"
    else:
        status = "Bad"
    details = {
        "total_headers": int(sum(heading_distribution.values())),
        "h1_count": h1_count,
        "heading_sequence_preview": heading_sequence[:20],
        "heading_outline": heading_outline,
    }
    return status, errors, details


_SKIP_HIDDEN_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "code", "pre"})
_ICON_CLASS_RE = re.compile(r"icon|fa-|bi-|material", re.I)

_HIDDEN_SELECTORS: List[Tuple[str, str]] = [
    ("[hidden]", "[hidden]"),
    ('[style*="display:none"]', "display:none"),
    ('[style*="display: none"]', "display:none"),
    ('[style*="visibility:hidden"]', "visibility:hidden"),
    ('[style*="visibility: hidden"]', "visibility:hidden"),
    ('[style*="opacity:0"]', "opacity:0"),
    ('[style*="opacity: 0"]', "opacity:0"),
    ('[style*="text-indent:-"]', "text-indent"),
    ('[style*="left:-9999"]', "off-screen"),
    ('[style*="left: -9999"]', "off-screen"),
    ('[style*="clip:rect(0"]', "clip"),
    ('[style*="height:0"]', "zero-size"),
    ('[style*="height: 0"]', "zero-size"),
    ('[style*="width:0"]', "zero-size"),
    ('[style*="width: 0"]', "zero-size"),
]

_MAX_SNIPPETS = 10


def _extract_hidden_content_signals(soup: BeautifulSoup) -> Tuple[bool, int, int, List[str]]:
    hidden_nodes: List[Tuple[Any, str]] = []  # (node, method)
    seen_ids: Set[int] = set()

    for sel, method in _HIDDEN_SELECTORS:
        for node in soup.select(sel):
            node_id = id(node)
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            hidden_nodes.append((node, method))

    for node in soup.find_all(attrs={"aria-hidden": "true"}):
        node_id = id(node)
        if node_id in seen_ids:
            continue
        text_len = len(re.sub(r"\s+", " ", node.get_text(" ", strip=True)))
        if text_len > 0:
            seen_ids.add(node_id)
            hidden_nodes.append((node, "aria-hidden"))

    for node in soup.find_all(style=True):
        style = str(node.get("style") or "").lower()
        m = re.search(r"font-size\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*px", style)
        if m:
            try:
                if float(m.group(1)) < 5.0:
                    node_id = id(node)
                    if node_id not in seen_ids:
                        seen_ids.add(node_id)
                        hidden_nodes.append((node, "small-font"))
            except Exception:
                pass

    # --- Filter false positives and collect snippets ---
    hidden_nodes_count = 0
    hidden_text_chars = 0
    snippets: List[str] = []

    for node, method in hidden_nodes:
        tag_name = getattr(node, "name", None) or ""
        if tag_name.lower() in _SKIP_HIDDEN_TAGS:
            continue

        raw_text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if len(raw_text) < 4:
            continue

        # Skip icon elements for aria-hidden
        if method == "aria-hidden" and tag_name.lower() in ("i", "span"):
            cls = " ".join(node.get("class", []))
            if _ICON_CLASS_RE.search(cls):
                continue

        hidden_nodes_count += 1
        hidden_text_chars += len(raw_text)

        if len(snippets) < _MAX_SNIPPETS:
            snippet_text = raw_text[:150]
            if len(raw_text) > 150:
                snippet_text += "..."
            snippets.append(f"[{method}] {snippet_text}")

    return (hidden_nodes_count > 0), hidden_nodes_count, hidden_text_chars, snippets


def _detect_cloaking(
    body_text: str,
    hidden_content: bool,
    hidden_nodes_count: int,
    hidden_text_chars: int,
) -> bool:
    if not hidden_content:
        return False
    total_chars = len((body_text or "").strip())
    if total_chars <= 0:
        return False
    hidden_ratio = float(hidden_text_chars) / float(max(1, total_chars))
    if hidden_nodes_count >= 20:
        return True
    if hidden_ratio >= 0.30 and hidden_text_chars >= 200:
        return True
    if hidden_nodes_count >= 8 and hidden_text_chars >= 200:
        return True
    return hidden_nodes_count >= 6 and total_chars < 200
