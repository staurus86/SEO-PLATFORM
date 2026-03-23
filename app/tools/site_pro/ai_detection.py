"""AI content detection utilities for Site Audit Pro."""
from __future__ import annotations

import re
from typing import List, Tuple
from urllib.parse import urlparse

from .constants import AI_LLM_STYLE_MARKERS, AI_PHRASE_MARKERS, AI_TECH_MARKERS

AI_STYLE_REGEX_MARKERS = (
    r"\b(?:важно|необходимо|следует|стоит)\s+(?:отметить|подчеркнуть|понимать)\b",
    r"\b(?:таким образом|следовательно|подводя итог|в заключение|резюмируя|итак)\b",
    r"\b(?:в современном мире|на сегодняшний день|в настоящее время)\b",
    r"\b(?:как видно|как можно видеть|как нетрудно заметить)\b",
    r"\b(?:it is important to note that|it is worth noting that|in summary|to sum up)\b",
    r"\b(?:as an ai language model|as an ai assistant|i hope this helps|feel free to ask)\b",
)


def _ai_marker_sample(text: str, markers: List[str]) -> str:
    raw = text or ""
    if not raw or not markers:
        return ""
    marker = str(markers[0]).strip()
    if not marker:
        return ""
    snippets: List[str] = []
    if re.fullmatch(r"[a-zA-Z0-9\u0400-\u04FF_-]+", marker):
        marker_pattern = rf"\b{re.escape(marker)}\b"
    else:
        marker_pattern = re.escape(marker)
    for m in re.finditer(marker_pattern, raw, flags=re.I):
        start = max(0, m.start() - 70)
        end = min(len(raw), m.end() + 70)
        snippet = re.sub(r"\s+", " ", raw[start:end].strip())
        if snippet:
            snippets.append(snippet)
        if len(snippets) >= 2:
            break
    return " ... ".join(snippets)


def _detect_ai_markers(text: str) -> Tuple[int, List[str]]:
    text_lower = (text or "").lower()
    if not text_lower:
        return 0, []
    found_markers: List[str] = []
    seen: set[str] = set()

    def add_marker(marker: str) -> None:
        token = str(marker or "").strip().lower()
        if not token or token in seen:
            return
        seen.add(token)
        found_markers.append(token)

    for phrase in AI_PHRASE_MARKERS:
        if phrase and phrase in text_lower:
            add_marker(phrase)

    for phrase in AI_LLM_STYLE_MARKERS:
        if phrase and phrase in text_lower:
            add_marker(phrase)

    for pattern in AI_STYLE_REGEX_MARKERS:
        match = re.search(pattern, text_lower, flags=re.I)
        if match:
            add_marker(match.group(0))

    for marker in AI_TECH_MARKERS:
        if re.search(rf"\b{re.escape(marker)}\b", text_lower):
            add_marker(marker)

    return len(found_markers), found_markers[:10]


def _classify_page_type(url: str, structured_types: List[str], title: str, body_text: str) -> str:
    parsed = urlparse(url or "")
    path = (parsed.path or "").lower().strip("/")
    stypes = {str(x).lower() for x in (structured_types or [])}
    text = f"{(title or '').lower()} {(body_text or '').lower()}"
    if path in ("",):
        return "home"
    if "product" in stypes or any(x in path for x in ("product", "shop", "catalog", "\u0442\u043e\u0432\u0430\u0440")):
        return "product"
    if "article" in stypes or any(x in path for x in ("blog", "news", "article", "post", "\u0441\u0442\u0430\u0442\u044c\u044f", "\u043d\u043e\u0432\u043e\u0441\u0442")):
        return "article"
    if any(x in path for x in ("category", "catalog", "collection", "\u043a\u0430\u0442\u0435\u0433\u043e\u0440")):
        return "category"
    if any(x in text for x in ("privacy policy", "terms", "cookie", "\u043f\u043e\u043b\u0438\u0442\u0438\u043a\u0430", "\u0443\u0441\u043b\u043e\u0432\u0438\u044f", "\u0441\u043e\u0433\u043b\u0430\u0441\u0438\u0435")):
        return "legal"
    if any(x in path for x in ("contact", "about", "company", "\u043a\u043e\u043d\u0442\u0430\u043a\u0442", "\u043e-\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438")):
        return "service"
    return "other"
