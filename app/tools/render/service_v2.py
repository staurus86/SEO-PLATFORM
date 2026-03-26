"""Render audit v2 service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse
import json
import re
import textwrap
import time

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.core.scan_token import get_scan_token_headers
from app.tools.http_text import decode_response_text


UA_DESKTOP = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.96 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

NON_SEO_META_NAMES = [
    "viewport",
    "charset",
    "theme-color",
    "color-scheme",
    "referrer",
    "format-detection",
    "mobile-web-app-capable",
    "apple-mobile-web-app-capable",
    "apple-mobile-web-app-status-bar-style",
    "apple-mobile-web-app-title",
    "application-name",
    "generator",
    "author",
    "msapplication-tilecolor",
    "msapplication-config",
]

NON_SEO_HTTP_EQUIV = [
    "content-security-policy",
    "x-ua-compatible",
    "content-language",
]


@dataclass
class Snapshot:
    status_code: Optional[int]
    title: str
    meta_description: str
    canonical: str
    headings: List[str]
    links: List[str]
    images: List[str]
    structured_data: List[str]
    visible_text: List[str]
    meta_non_seo: Dict[str, str]
    html_bytes: int
    errors: List[str]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _uniq(items: Iterable[str]) -> List[str]:
    out, seen = [], set()
    for item in items:
        key = _norm(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _sample(items: List[str], limit: int = 5) -> List[str]:
    out = []
    for item in items[:limit]:
        text = re.sub(r"\s+", " ", str(item)).strip()
        out.append(text[:177] + "..." if len(text) > 180 else text)
    return out


def _diff(items_a: List[str], items_b: List[str]) -> List[str]:
    b = {_norm(x) for x in items_b}
    return [x for x in items_a if _norm(x) and _norm(x) not in b]


def _count_h(headings: List[str], level: int) -> int:
    prefix = f"h{level}:"
    return sum(1 for h in headings if h.lower().startswith(prefix))


def _schema_types(values: List[str]) -> List[str]:
    out: List[str] = []
    for raw in values:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("@type")
                if isinstance(t, str):
                    out.append(t)
                elif isinstance(t, list):
                    out.extend([x for x in t if isinstance(x, str)])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return _uniq(out)


def _extract_non_seo_meta_soup(soup: BeautifulSoup) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or "").strip().lower()
        http_equiv = (tag.get("http-equiv") or "").strip().lower()
        content = (tag.get("content") or "").strip()
        charset = (tag.get("charset") or "").strip()
        if charset:
            out["meta:charset"] = charset
        if name in NON_SEO_META_NAMES:
            out[f"meta:{name}"] = content
        if http_equiv in NON_SEO_HTTP_EQUIV:
            out[f"http-equiv:{http_equiv}"] = content

    manifest = soup.find("link", attrs={"rel": re.compile(r"\bmanifest\b", re.I)})
    if manifest:
        href = (manifest.get("href") or "").strip()
        if href:
            out["link:manifest"] = href
    return out


def _compare_meta(raw_meta: Dict[str, str], rendered_meta: Dict[str, str]) -> Dict[str, Any]:
    keys = sorted(set(raw_meta.keys()) | set(rendered_meta.keys()))
    items: List[Dict[str, str]] = []
    for key in keys:
        raw_value = (raw_meta.get(key) or "").strip()
        rendered_value = (rendered_meta.get(key) or "").strip()
        if raw_value and rendered_value:
            status = "same" if _norm(raw_value) == _norm(rendered_value) else "changed"
        elif rendered_value:
            status = "only_rendered"
        elif raw_value:
            status = "only_raw"
        else:
            status = "empty"
        items.append({"key": key, "raw": raw_value, "rendered": rendered_value, "status": status})

    return {
        "total": len(items),
        "same": sum(1 for x in items if x["status"] == "same"),
        "changed": sum(1 for x in items if x["status"] == "changed"),
        "only_rendered": sum(1 for x in items if x["status"] == "only_rendered"),
        "only_raw": sum(1 for x in items if x["status"] == "only_raw"),
        "items": items,
    }


def _score(rendered: Snapshot, missing: Dict[str, List[str]]) -> Dict[str, float]:
    total_missing = float(sum(len(v) for v in missing.values()))
    rendered_total = float(
        len(rendered.visible_text)
        + len(rendered.headings)
        + len(rendered.links)
        + len(rendered.images)
        + len(rendered.structured_data)
    )
    if rendered_total <= 0:
        return {"total_missing": 0.0, "rendered_total": 0.0, "missing_pct": 0.0, "score": 100.0}
    missing_pct = min(100.0, (total_missing / rendered_total) * 100.0)
    return {
        "total_missing": total_missing,
        "rendered_total": rendered_total,
        "missing_pct": missing_pct,
        "score": max(0.0, 100.0 - missing_pct),
    }


def _images_without_alt(images: List[str]) -> int:
    count = 0
    for row in images:
        m = re.search(r"\|\s*alt=(.*)$", row or "", flags=re.I)
        if m is None:
            continue
        if not m.group(1).strip():
            count += 1
    return count


def _build_required_seo_checks(raw: Dict[str, Any], rendered: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(
        code: str,
        label: str,
        severity: str,
        ok: bool,
        raw_value: Any,
        rendered_value: Any,
        details: str,
        fix: str,
    ) -> None:
        checks.append(
            {
                "code": code,
                "label": label,
                "severity": severity,
                "status": "pass" if ok else ("fail" if severity == "critical" else "warn"),
                "raw": raw_value,
                "rendered": rendered_value,
                "details": details,
                "fix": fix,
            }
        )

    title_raw = (raw.get("title") or "").strip()
    title_js = (rendered.get("title") or "").strip()
    add(
        "title_present_js",
        "Title присутствует в JS-рендере",
        "critical",
        bool(title_js),
        title_raw,
        title_js,
        "Title обязателен для индексации и формирования сниппета.",
        "Добавьте уникальный <title> в шаблон страницы.",
    )
    add(
        "title_present_nojs",
        "Title присутствует без JavaScript",
        "warning",
        bool(title_raw),
        title_raw,
        title_js,
        "Если title доступен только после JS, робот может получить неполный документ.",
        "Рендерьте <title> на стороне сервера (SSR/шаблон HTML).",
    )
    title_len = len(title_js)
    add(
        "title_length",
        "Длина заголовка title (20-70 символов)",
        "warning",
        20 <= title_len <= 70,
        len(title_raw),
        title_len,
        "Слишком короткий или длинный title ухудшает CTR и релевантность сниппета.",
        "Держите title в диапазоне 20-70 символов с ключевой темой страницы.",
    )

    desc_raw = (raw.get("meta_description") or "").strip()
    desc_js = (rendered.get("meta_description") or "").strip()
    add(
        "description_present_js",
        "Мета-описание (description) присутствует в JS-рендере",
        "critical",
        bool(desc_js),
        desc_raw,
        desc_js,
        "Meta description влияет на описание сниппета в выдаче.",
        "Добавьте <meta name=\"description\" ...> с уникальным содержимым.",
    )
    add(
        "description_present_nojs",
        "Мета-описание (description) присутствует без JavaScript",
        "warning",
        bool(desc_raw),
        desc_raw,
        desc_js,
        "Если description появляется только после JS, сниппет может быть неуправляемым.",
        "Вставляйте description в исходный HTML до выполнения JS.",
    )
    desc_len = len(desc_js)
    add(
        "description_length",
        "Длина Description (70-180 символов)",
        "warning",
        70 <= desc_len <= 180,
        len(desc_raw),
        desc_len,
        "Невалидная длина description ухудшает качество предпросмотра в SERP.",
        "Оптимизируйте длину description до 70-180 символов.",
    )

    canon_raw = (raw.get("canonical") or "").strip()
    canon_js = (rendered.get("canonical") or "").strip()
    add(
        "canonical_present_js",
        "Canonical присутствует в JS-рендере",
        "warning",
        bool(canon_js),
        canon_raw,
        canon_js,
        "Canonical снижает риски дублей URL.",
        "Добавьте <link rel=\"canonical\" href=\"...\"> для канонической версии страницы.",
    )
    add(
        "canonical_present_nojs",
        "Canonical присутствует без JavaScript",
        "warning",
        bool(canon_raw),
        canon_raw,
        canon_js,
        "Canonical должен быть доступен роботу в исходном HTML.",
        "Генерируйте canonical сервером, а не клиентским JS.",
    )

    h1_raw = int(raw.get("h1_count", 0) or 0)
    h1_js = int(rendered.get("h1_count", 0) or 0)
    add(
        "h1_present_js",
        "H1 присутствует в JS-рендере",
        "critical",
        h1_js > 0,
        h1_raw,
        h1_js,
        "Отсутствие H1 усложняет понимание темы страницы поисковыми системами.",
        "Добавьте один основной <h1> с релевантной формулировкой.",
    )
    add(
        "h1_present_nojs",
        "H1 присутствует без JavaScript",
        "warning",
        h1_raw > 0,
        h1_raw,
        h1_js,
        "Если H1 появляется только после JS, no-JS обход теряет важный сигнал релевантности.",
        "Рендерьте H1 в исходном HTML.",
    )

    links_raw = int(raw.get("links_count", 0) or 0)
    links_js = int(rendered.get("links_count", 0) or 0)
    add(
        "links_minimum_js",
        "Внутренние ссылки в JS-рендере (>=3)",
        "warning",
        links_js >= 3,
        links_raw,
        links_js,
        "Малое число внутренних ссылок ухудшает обход и распределение веса.",
        "Добавьте минимум 3 релевантные внутренние ссылки на странице.",
    )

    img_raw = int(raw.get("images_count", 0) or 0)
    img_js = int(rendered.get("images_count", 0) or 0)
    img_wo_alt_raw = int(raw.get("images_without_alt", 0) or 0)
    img_wo_alt_js = int(rendered.get("images_without_alt", 0) or 0)
    add(
        "images_alt_js",
        "Изображения с alt в JS-рендере",
        "warning",
        img_js == 0 or img_wo_alt_js == 0,
        f"{img_raw} img / без alt: {img_wo_alt_raw}",
        f"{img_js} img / без alt: {img_wo_alt_js}",
        "Отсутствующие alt у изображений ухудшают доступность и контекст для SEO.",
        "Добавьте осмысленные alt для контентных изображений.",
    )

    schema_raw = int(raw.get("structured_data_count", 0) or 0)
    schema_js = int(rendered.get("structured_data_count", 0) or 0)
    add(
        "structured_data_presence",
        "Структурированные данные присутствуют",
        "warning",
        schema_js > 0,
        schema_raw,
        schema_js,
        "Разметка schema.org помогает расширенным результатам и пониманию сущностей.",
        "Добавьте JSON-LD schema.org для ключевых сущностей страницы.",
    )

    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    pass_count = sum(1 for c in checks if c["status"] == "pass")
    return {"total": len(checks), "pass": pass_count, "warn": warn_count, "fail": fail_count, "items": checks}


def _build_seo_recommendations(seo_checks: Dict[str, Any]) -> List[str]:
    recs: List[str] = []
    for item in seo_checks.get("items", []):
        if item.get("status") in {"warn", "fail"} and item.get("fix"):
            recs.append(str(item["fix"]))
    # unique, keep order
    out: List[str] = []
    seen = set()
    for rec in recs:
        key = _norm(rec)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def _build_recommendations(missing: Dict[str, List[str]]) -> List[str]:
    recs: List[str] = []
    if len(missing.get("Visible text", [])) > 10:
        recs.append("[Высокий] Обеспечьте доступность ключевого контента без JavaScript (SSR/SSG).")
    if missing.get("Headings"):
        recs.append("[Высокий] Добавьте H1-H2 в исходный HTML.")
    if missing.get("Links"):
        recs.append("[Средний] Разместите важные внутренние ссылки в исходном HTML.")
    if missing.get("Structured data"):
        recs.append("[Средний] По возможности рендерьте JSON-LD на стороне сервера.")
    if missing.get("Images"):
        recs.append("[Низкий] Убедитесь, что критичные изображения и alt доступны до выполнения JS.")
    return recs


class RenderAuditServiceV2:
    def __init__(self, timeout: int = 35, use_proxy: bool = False):
        self.timeout = max(10, int(timeout))
        self.timeout_ms = self.timeout * 1000
        self.use_proxy = use_proxy
        self.debug_enabled = bool(getattr(settings, "RENDER_AUDIT_DEBUG", False) or getattr(settings, "DEBUG", False))

    def _hydration_check(self, raw_snapshot: Dict[str, Any], rendered_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Compare raw (noJS) and rendered snapshots for SSR vs CSR hydration issues."""
        issues: List[Dict[str, Any]] = []
        # Title mismatch
        raw_title = (raw_snapshot.get("title") or "").strip()
        rendered_title = (rendered_snapshot.get("title") or "").strip()
        if raw_title and rendered_title and raw_title != rendered_title:
            issues.append({
                "code": "hydration_title_mismatch",
                "severity": "warning",
                "title": "Title changes after hydration",
                "raw": raw_title,
                "rendered": rendered_title,
            })

        # H1 mismatch
        raw_h1 = int(raw_snapshot.get("h1_count", 0) or 0)
        rendered_h1 = int(rendered_snapshot.get("h1_count", 0) or 0)
        if raw_h1 != rendered_h1:
            issues.append({
                "code": "hydration_h1_change",
                "severity": "warning",
                "title": f"H1 count changes after hydration ({raw_h1} -> {rendered_h1})",
            })

        # Content significantly different
        raw_text_len = int(raw_snapshot.get("visible_text_count", 0) or 0)
        rendered_text_len = int(rendered_snapshot.get("visible_text_count", 0) or 0)
        if rendered_text_len > 0 and raw_text_len < rendered_text_len * 0.5:
            pct = round((1 - raw_text_len / max(1, rendered_text_len)) * 100)
            issues.append({
                "code": "hydration_content_gap",
                "severity": "critical",
                "title": f"{pct}% of content only available after JS hydration",
            })

        # Links count diff
        raw_links = int(raw_snapshot.get("links_count", 0) or 0)
        rendered_links = int(rendered_snapshot.get("links_count", 0) or 0)
        if rendered_links > 0 and raw_links < rendered_links * 0.7:
            issues.append({
                "code": "hydration_links_gap",
                "severity": "warning",
                "title": f"Links change after hydration ({raw_links} -> {rendered_links})",
            })

        return {"hydration_issues": issues, "has_hydration_problems": len(issues) > 0}

    def _dbg(self, task_id: str, message: str) -> None:
        if self.debug_enabled:
            print(f"[RENDER-DEBUG][{task_id}] {message}")

    def _parse_raw(self, html: str, status_code: Optional[int]) -> Snapshot:
        soup = BeautifulSoup(html or "", "lxml")
        title = (soup.title.string if soup.title and soup.title.string else "").strip()
        desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        desc = (desc_tag.get("content") if desc_tag else "") or ""
        canonical_tag = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
        canonical = (canonical_tag.get("href") if canonical_tag else "") or ""
        headings = [f"h{i}: {h.get_text(' ', strip=True)}" for i in range(1, 7) for h in soup.find_all(f"h{i}") if h.get_text(" ", strip=True)]
        links = [f"{(a.get('href') or '').strip()} | {a.get_text(' ', strip=True)}".strip() for a in soup.find_all("a")]
        images = [f"{(img.get('src') or '').strip()} | alt={(img.get('alt') or '').strip()}".strip() for img in soup.find_all("img")]
        schema = []
        for s in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            val = (s.string or s.get_text() or "").strip()
            if val:
                schema.append(val)
        text = _uniq([x.strip() for x in soup.get_text("\n", strip=True).splitlines() if x.strip()])
        meta_non_seo = _extract_non_seo_meta_soup(soup)
        errors = [] if status_code is not None else ["raw fetch failed"]
        return Snapshot(
            status_code,
            title,
            desc.strip(),
            canonical.strip(),
            _uniq(headings),
            _uniq(links),
            _uniq(images),
            _uniq(schema),
            text,
            meta_non_seo,
            len((html or "").encode("utf-8", errors="ignore")),
            errors,
        )

    def _render(self, p: Any, url: str, ua: str, mobile: bool, shot_js: Path, shot_nojs: Path) -> Tuple[Snapshot, Dict[str, float], Dict[str, float], Dict[str, Any], List[str], Dict[str, Any], Dict[str, Any]]:
        errors: List[str] = []
        out: Dict[str, Any] = {}
        js_timing: Dict[str, float] = {}
        nojs_timing: Dict[str, float] = {}
        emulation: Dict[str, Any] = {}
        js_frameworks: List[str] = []
        console_log: Dict[str, Any] = {"errors": [], "warnings": [], "error_count": 0, "warning_count": 0}
        css_hidden_content: Dict[str, Any] = {"hidden_elements": [], "hidden_count": 0}
        launch_kwargs = {"headless": True}
        if self.use_proxy:
            from app.proxy import get_playwright_proxy
            _pw_proxy = get_playwright_proxy()
            if _pw_proxy:
                launch_kwargs["proxy"] = _pw_proxy
        browser = p.chromium.launch(**launch_kwargs)
        try:
            ctx_kwargs: Dict[str, Any] = {"locale": "en-US", "user_agent": ua}
            if mobile:
                # Force real mobile emulation even if Playwright preset is unavailable.
                mobile_preset = p.devices.get("Pixel 5") or p.devices.get("iPhone 13")
                if mobile_preset:
                    ctx_kwargs.update(mobile_preset)
                    ctx_kwargs["user_agent"] = ua
                else:
                    ctx_kwargs.update(
                        {
                            "viewport": {"width": 393, "height": 851},
                            "device_scale_factor": 3,
                            "is_mobile": True,
                            "has_touch": True,
                        }
                    )
            else:
                ctx_kwargs.update({"viewport": {"width": 1366, "height": 900}, "is_mobile": False, "has_touch": False})
            emulation = {
                "requested_mobile": bool(mobile),
                "is_mobile": bool(ctx_kwargs.get("is_mobile", False)),
                "has_touch": bool(ctx_kwargs.get("has_touch", False)),
                "viewport": ctx_kwargs.get("viewport"),
                "user_agent": ctx_kwargs.get("user_agent", ua),
            }

            extra_headers = get_scan_token_headers()
            if extra_headers:
                ctx_kwargs["extra_http_headers"] = {str(k): str(v) for k, v in extra_headers.items()}

            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            page.set_default_timeout(self.timeout_ms)

            # Task 4.2: Capture console messages
            console_messages: List[Dict[str, str]] = []
            page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))

            try:
                page.goto(url, wait_until="networkidle")
            except Exception:
                page.goto(url, wait_until="load")
            script = textwrap.dedent("""
            () => {
              const n=(t)=> (t||'').trim();
              const v=(el)=>{const s=getComputedStyle(el); if(!s||s.display==='none'||s.visibility==='hidden'||s.opacity==='0') return false; const r=el.getClientRects(); return r&&r.length>0&&el.offsetWidth>0&&el.offsetHeight>0;};
              const o={title:n(document.title||''),meta_description:'',canonical:'',headings:[],links:[],images:[],structured_data:[],visible_text:[],meta_non_seo:{}};
              const m=[...document.querySelectorAll('meta[name]')].find(x=>(x.getAttribute('name')||'').toLowerCase()==='description'); o.meta_description=n(m?.getAttribute('content')||'');
              const c=[...document.querySelectorAll('link[rel]')].find(x=>(x.getAttribute('rel')||'').toLowerCase().includes('canonical')); o.canonical=n(c?.getAttribute('href')||'');
              const nameKeys=new Set(['viewport','charset','theme-color','color-scheme','referrer','format-detection','mobile-web-app-capable','apple-mobile-web-app-capable','apple-mobile-web-app-status-bar-style','apple-mobile-web-app-title','application-name','generator','author','msapplication-tilecolor','msapplication-config']);
              const equivKeys=new Set(['content-security-policy','x-ua-compatible','content-language']);
              document.querySelectorAll('meta').forEach(meta=>{
                const name=n(meta.getAttribute('name')||'').toLowerCase();
                const equiv=n(meta.getAttribute('http-equiv')||'').toLowerCase();
                const content=n(meta.getAttribute('content')||'');
                const charset=n(meta.getAttribute('charset')||'');
                if(charset){o.meta_non_seo['meta:charset']=charset;}
                if(nameKeys.has(name)){o.meta_non_seo['meta:'+name]=content;}
                if(equivKeys.has(equiv)){o.meta_non_seo['http-equiv:'+equiv]=content;}
              });
              const manifest=[...document.querySelectorAll('link[rel]')].find(x=>(x.getAttribute('rel')||'').toLowerCase().includes('manifest'));
              if(manifest){const href=n(manifest.getAttribute('href')||''); if(href){o.meta_non_seo['link:manifest']=href;}}
              for(let i=1;i<=6;i++){document.querySelectorAll('h'+i).forEach(h=>{if(v(h)){const t=n(h.innerText||h.textContent||''); if(t) o.headings.push('h'+i+': '+t);}});}
              document.querySelectorAll('a').forEach(a=>{if(!v(a)) return; const h=n(a.getAttribute('href')||''); const t=n(a.innerText||a.textContent||''); if(h||t) o.links.push((h+' | '+t).trim());});
              document.querySelectorAll('img').forEach(i=>{if(!v(i)) return; const s=n(i.getAttribute('src')||''); const a=n(i.getAttribute('alt')||''); if(s||a) o.images.push((s+' | alt='+a).trim());});
              document.querySelectorAll('script[type=\"application/ld+json\"]').forEach(s=>{const t=n(s.textContent||''); if(t) o.structured_data.push(t);});
              (document.body?.innerText||'').split('\\n').forEach(l=>{const t=n(l); if(t) o.visible_text.push(t);});
              return o;
            }""").strip()
            out = page.evaluate(script)
            js_timing = page.evaluate("() => { const nav=performance.getEntriesByType('navigation')[0]||performance.timing||{}; return {tti:(nav.loadEventEnd||0), dcl:(nav.domContentLoadedEventEnd||0)}; }")

            # Task 4.1: JS Framework Detection
            frameworks_js = """
            () => {
                const f = [];
                if (window.__NEXT_DATA__ || document.getElementById('__next')) f.push('Next.js');
                if (window.__NUXT__ || window.$nuxt) f.push('Nuxt');
                if (document.querySelector('[data-reactroot]') || document.querySelector('[id="root"]')?.innerHTML || window.__REACT_DEVTOOLS_GLOBAL_HOOK__) f.push('React');
                if (document.querySelector('[data-v-]') || window.__VUE__) f.push('Vue');
                if (window.ng || document.querySelector('[ng-version]') || document.querySelector('[_ngcontent-]')) f.push('Angular');
                if (document.querySelector('[class*="svelte-"]') || window.__svelte) f.push('Svelte');
                if (window.Gatsby) f.push('Gatsby');
                if (window.__REMIX_CONTEXT) f.push('Remix');
                if (document.querySelector('script[src*="astro"]') || document.querySelector('[data-astro-cid-]')) f.push('Astro');
                if (window.jQuery || window.$?.fn?.jquery) f.push('jQuery');
                return f;
            }
            """
            try:
                js_frameworks = page.evaluate(frameworks_js) or []
            except Exception:
                js_frameworks = []

            # Task 4.2: Process console messages
            console_errors_list = [m["text"] for m in console_messages if m["type"] == "error"]
            console_warnings_list = [m["text"] for m in console_messages if m["type"] == "warning"]
            console_log = {
                "errors": console_errors_list[:50],
                "warnings": console_warnings_list[:50],
                "error_count": len(console_errors_list),
                "warning_count": len(console_warnings_list),
            }

            # Task 4.3: CSS-based Content Visibility Detection
            css_hidden_content: Dict[str, Any] = {"hidden_elements": [], "hidden_count": 0}
            try:
                visibility_js = """
                () => {
                    const results = [];
                    const selectors = 'h1, h2, h3, article, main, [role="main"], p, .content, #content';
                    document.querySelectorAll(selectors).forEach(el => {
                        const s = getComputedStyle(el);
                        const text = (el.textContent || '').trim().substring(0, 100);
                        if (!text) return;
                        const hidden = s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0' ||
                                       (parseInt(s.maxHeight) === 0 && s.overflow === 'hidden') ||
                                       el.getAttribute('aria-hidden') === 'true';
                        if (hidden) {
                            results.push({tag: el.tagName.toLowerCase(), text, reason:
                                s.display === 'none' ? 'display:none' : s.visibility === 'hidden' ? 'visibility:hidden' :
                                s.opacity === '0' ? 'opacity:0' : 'other'
                            });
                        }
                    });
                    return {hidden_elements: results, hidden_count: results.length};
                }
                """
                css_hidden_content = page.evaluate(visibility_js) or css_hidden_content
            except Exception:
                pass

            try:
                page.screenshot(path=str(shot_js), full_page=True)
            except Exception:
                # On long/complex mobile pages full-page capture may fail; fallback to viewport screenshot.
                page.screenshot(path=str(shot_js), full_page=False)

            nojs = browser.new_context(**{**ctx_kwargs, "java_script_enabled": False})
            np = nojs.new_page()
            np.set_default_timeout(self.timeout_ms)
            try:
                np.goto(url, wait_until="load")
            except Exception:
                np.goto(url, wait_until="domcontentloaded")
            nojs_timing = np.evaluate("() => { const nav=performance.getEntriesByType('navigation')[0]||performance.timing||{}; return {tti:(nav.loadEventEnd||0), dcl:(nav.domContentLoadedEventEnd||0)}; }")
            try:
                np.screenshot(path=str(shot_nojs), full_page=True)
            except Exception:
                np.screenshot(path=str(shot_nojs), full_page=False)
            try:
                nojs.close()
                ctx.close()
            except Exception:
                pass
        except Exception as exc:
            errors.append(str(exc))
        finally:
            try:
                browser.close()
            except Exception:
                pass

        snap = Snapshot(
            None,
            out.get("title", ""),
            out.get("meta_description", ""),
            out.get("canonical", ""),
            _uniq(out.get("headings", []) or []),
            _uniq(out.get("links", []) or []),
            _uniq(out.get("images", []) or []),
            _uniq(out.get("structured_data", []) or []),
            _uniq(out.get("visible_text", []) or []),
            out.get("meta_non_seo", {}) or {},
            0,
            errors,
        )
        return snap, js_timing, nojs_timing, emulation, js_frameworks, console_log, css_hidden_content

    def run(self, url: str, task_id: str, progress_callback=None) -> Dict[str, Any]:
        def notify(progress: int, message: str) -> None:
            if callable(progress_callback):
                progress_callback(progress, message)

        notify(5, "Подготовка рендер-аудита")
        self._dbg(task_id, f"start url={url} timeout={self.timeout}s")
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self._dbg(task_id, f"playwright import failed: {exc}")
            return {"task_type": "render_audit", "url": url, "completed_at": datetime.utcnow().isoformat(), "results": {"engine": "v2", "variants": [], "issues": [{"severity": "critical", "code": "playwright_unavailable", "title": "Среда Playwright недоступна", "details": str(exc)}], "issues_count": 1, "summary": {"variants_total": 0, "critical_issues": 1, "warning_issues": 0, "info_issues": 0, "score": None, "missing_total": 0, "avg_missing_pct": 0, "avg_raw_load_ms": 0, "avg_js_load_ms": 0}, "recommendations": ["Установите Playwright/Chromium для полноценного рендер-аудита."], "artifacts": {"screenshot_dir": str(Path(settings.REPORTS_DIR) / "render" / task_id / "screenshots"), "screenshots": []}}}

        shot_dir = Path(settings.REPORTS_DIR) / "render" / task_id / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        variants = [("googlebot_desktop", "Googlebot (ПК)", UA_DESKTOP, False), ("googlebot_mobile", "Googlebot (мобильный)", UA_MOBILE, True)]
        all_variants: List[Dict[str, Any]] = []
        all_issues: List[Dict[str, Any]] = []
        all_shots: List[str] = []

        with sync_playwright() as p:
            for idx, (vid, label, ua, mobile) in enumerate(variants, start=1):
                notify(10 + int((idx - 1) * 40), f"Аудит профиля {label} ({idx}/2)")
                self._dbg(task_id, f"variant-start id={vid} label={label} mobile={mobile}")
                try:
                    proxy_kwargs = {}
                    if self.use_proxy:
                        from app.proxy import get_requests_proxies
                        _proxies = get_requests_proxies()
                        if _proxies:
                            proxy_kwargs["proxies"] = _proxies
                    try:
                        r = requests.get(url, timeout=self.timeout, headers={"User-Agent": ua}, allow_redirects=True, **proxy_kwargs)
                        raw = self._parse_raw(decode_response_text(r), r.status_code)
                    except Exception:
                        raw = self._parse_raw("", None)

                    t0 = time.time()
                    base = f"render_{re.sub(r'[^a-zA-Z0-9]+','-',urlparse(url).netloc or 'site').strip('-').lower()}_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M')}_{vid}"
                    shot_js = shot_dir / f"{base}_js.png"
                    shot_nojs = shot_dir / f"{base}_nojs.png"
                    rendered, timing_js, timing_nojs, emulation, detected_frameworks, detected_console_log, detected_css_hidden = self._render(p, url, ua, mobile, shot_js, shot_nojs)
                    elapsed = max(0.0, time.time() - t0)
                    self._dbg(
                        task_id,
                        f"variant-rendered id={vid} elapsed={elapsed:.2f}s raw_status={raw.status_code} "
                        f"render_errors={len(rendered.errors)} raw_text={len(raw.visible_text)} js_text={len(rendered.visible_text)}",
                    )

                    raw_stats = {
                        "status_code": raw.status_code,
                        "title": raw.title,
                        "meta_description": raw.meta_description,
                        "canonical": raw.canonical,
                        "h1_count": _count_h(raw.headings, 1),
                        "h2_count": _count_h(raw.headings, 2),
                        "links_count": len(raw.links),
                        "images_count": len(raw.images),
                        "images_without_alt": _images_without_alt(raw.images),
                        "structured_data_count": len(raw.structured_data),
                        "schema_types": _schema_types(raw.structured_data),
                        "visible_text_count": len(raw.visible_text),
                        "html_bytes": raw.html_bytes,
                        "errors": raw.errors,
                    }
                    rendered_stats = {
                        "title": rendered.title,
                        "meta_description": rendered.meta_description,
                        "canonical": rendered.canonical,
                        "h1_count": _count_h(rendered.headings, 1),
                        "h2_count": _count_h(rendered.headings, 2),
                        "links_count": len(rendered.links),
                        "images_count": len(rendered.images),
                        "images_without_alt": _images_without_alt(rendered.images),
                        "structured_data_count": len(rendered.structured_data),
                        "schema_types": _schema_types(rendered.structured_data),
                        "visible_text_count": len(rendered.visible_text),
                        "html_bytes": rendered.html_bytes,
                        "errors": rendered.errors,
                        "js_frameworks": detected_frameworks,
                        "console_log": detected_console_log,
                        "css_hidden_content": detected_css_hidden,
                    }

                    missing = {
                        "Visible text": _diff(rendered.visible_text, raw.visible_text),
                        "Headings": _diff(rendered.headings, raw.headings),
                        "Links": _diff(rendered.links, raw.links),
                        "Images": _diff(rendered.images, raw.images),
                        "Structured data": _diff(rendered.structured_data, raw.structured_data),
                    }
                    metrics = _score(rendered, missing)
                    meta_cmp = _compare_meta(raw.meta_non_seo, rendered.meta_non_seo)
                    seo_required = _build_required_seo_checks(raw_stats, rendered_stats)
                    issues: List[Dict[str, Any]] = []
                    if missing["Visible text"]:
                        issues.append({"severity": "warning" if len(missing["Visible text"]) <= 10 else "critical", "code": "content_missing_nojs", "title": "Контент появляется только после JavaScript", "details": f"В версии без JS отсутствует {len(missing['Visible text'])} строк текста.", "examples": _sample(missing["Visible text"])})
                    if missing["Headings"]:
                        issues.append({"severity": "critical", "code": "headings_missing_nojs", "title": "Заголовки отсутствуют без JavaScript", "details": f"Найдено {len(missing['Headings'])} заголовков только после JS.", "examples": _sample(missing["Headings"])})
                    if missing["Links"]:
                        issues.append({"severity": "warning", "code": "links_missing_nojs", "title": "Ссылки появляются только после JavaScript", "details": f"Найдено {len(missing['Links'])} ссылок только в JS-рендере.", "examples": _sample(missing["Links"])})
                    if missing["Structured data"]:
                        issues.append({"severity": "warning", "code": "schema_missing_nojs", "title": "Структурированные данные зависят от JavaScript", "details": f"Найдено {len(missing['Structured data'])} элементов JSON-LD только с JS.", "examples": _sample(missing["Structured data"])})
                    if elapsed > 12:
                        issues.append({"severity": "warning", "code": "render_too_slow", "title": "Медленный JS-рендер", "details": f"Время JS-рендера: {elapsed:.2f} сек."})
                    # Task 4.1: JS framework dependency warning
                    if detected_frameworks and len(missing.get("Visible text", [])) > 5:
                        fw_list = ", ".join(detected_frameworks)
                        issues.append({
                            "severity": "warning",
                            "code": "content_js_framework_dependent",
                            "title": f"Content depends on {fw_list} JavaScript rendering",
                            "details": (
                                f"Обнаружены фреймворки: {fw_list}. "
                                f"Rendered версия содержит значительно больше контента ({len(rendered.visible_text)} строк) "
                                f"чем raw HTML ({len(raw.visible_text)} строк). "
                                "Контент критически зависит от выполнения клиентского JavaScript."
                            ),
                            "examples": detected_frameworks,
                        })
                    # Task 4.2: Console errors warning
                    if detected_console_log.get("error_count", 0) > 0:
                        err_count = detected_console_log["error_count"]
                        issues.append({
                            "severity": "warning",
                            "code": "js_console_errors",
                            "title": f"JavaScript errors detected ({err_count}) — may affect content rendering for bots",
                            "details": f"Обнаружено {err_count} ошибок JavaScript в консоли браузера при рендеринге.",
                            "examples": detected_console_log.get("errors", [])[:5],
                        })
                    # Task 4.3: CSS hidden content issue
                    hidden_count = detected_css_hidden.get("hidden_count", 0)
                    if hidden_count > 0:
                        issues.append({
                            "severity": "warning",
                            "code": "css_hidden_content",
                            "title": f"Content hidden via CSS ({hidden_count} elements) — search engines may not index this content",
                            "details": f"Обнаружено {hidden_count} SEO-элементов, скрытых через CSS (display:none, visibility:hidden, opacity:0 и т.д.).",
                            "examples": [f"{el.get('tag')}: {el.get('text', '')[:60]} ({el.get('reason', 'unknown')})" for el in detected_css_hidden.get("hidden_elements", [])[:5]],
                        })

                    # Task 4.4: SSR vs CSR Hydration check
                    hydration = self._hydration_check(raw_stats, rendered_stats)
                    for h_issue in hydration.get("hydration_issues", []):
                        issues.append({
                            "severity": h_issue.get("severity", "warning"),
                            "code": h_issue.get("code", "hydration_issue"),
                            "title": h_issue.get("title", "Hydration issue"),
                            "details": f"Raw: {h_issue.get('raw', '-')}, Rendered: {h_issue.get('rendered', '-')}" if h_issue.get("raw") else h_issue.get("title", ""),
                        })

                    if metrics["score"] < 70:
                        issues.append({"severity": "critical", "code": "low_render_score", "title": "Низкий балл рендеринга", "details": f"Текущий балл: {metrics['score']:.1f}/100."})
                    if meta_cmp["changed"] or meta_cmp["only_rendered"] or meta_cmp["only_raw"]:
                        issues.append({
                            "severity": "warning",
                            "code": "meta_non_seo_diff",
                            "title": "Не-SEO мета-данные отличаются между версиями без JS и с JS",
                            "details": (
                                f"Изменено: {meta_cmp['changed']}, только в JS: {meta_cmp['only_rendered']}, "
                                f"только в no-JS: {meta_cmp['only_raw']}."
                            ),
                            "examples": [x["key"] for x in meta_cmp["items"] if x["status"] != "same"][:8],
                        })
                    if rendered.errors:
                        issues.append({
                            "severity": "critical",
                            "code": "render_profile_runtime_error",
                            "title": "Ошибки выполнения профиля рендеринга",
                            "details": "; ".join(rendered.errors[:3]),
                            "examples": rendered.errors[:5],
                        })
                    for check in seo_required.get("items", []):
                        status = check.get("status")
                        if status not in {"warn", "fail"}:
                            continue
                        issues.append({
                            "severity": "critical" if status == "fail" else "warning",
                            "code": f"seo_required_{check.get('code')}",
                            "title": f"Обязательный SEO-элемент: {check.get('label')}",
                            "details": f"{check.get('details')} Рекомендация: {check.get('fix')}",
                            "examples": [
                                f"Без JS: {check.get('raw', '-')}",
                                f"С JS: {check.get('rendered', '-')}",
                            ],
                        })

                    all_issues.extend([{**i, "variant": label} for i in issues])
                    shots = {}
                    for tag, path in (("js", shot_js), ("nojs", shot_nojs)):
                        if path.exists():
                            shots[tag] = {"path": str(path), "name": path.name, "url": f"/api/render-artifacts/{task_id}/{path.name}"}
                            all_shots.append(str(path))

                    all_variants.append({
                        "variant_id": vid,
                        "variant_label": label,
                        "mobile": mobile,
                        "user_agent": ua,
                        "profile_type": "mobile" if mobile else "desktop",
                        "raw": raw_stats,
                        "rendered": rendered_stats,
                        "missing": {"visible_text": missing["Visible text"], "headings": missing["Headings"], "links": missing["Links"], "images": missing["Images"], "structured_data": missing["Structured data"]},
                        "meta_non_seo": {"raw": raw.meta_non_seo, "rendered": rendered.meta_non_seo, "comparison": meta_cmp},
                        "seo_required": seo_required,
                        "metrics": metrics,
                        "timings": {"raw_s": 0.0, "rendered_s": elapsed},
                        "timing_nojs_ms": timing_nojs,
                        "timing_js_ms": timing_js,
                        "emulation": emulation,
                        "js_frameworks": detected_frameworks,
                        "console_log": detected_console_log,
                        "css_hidden_content": detected_css_hidden,
                        "hydration": hydration,
                        "issues": issues,
                        "recommendations": _build_recommendations(missing) + _build_seo_recommendations(seo_required),
                        "screenshots": shots,
                    })
                    self._dbg(
                        task_id,
                        f"variant-done id={vid} issues={len(issues)} shots={list(shots.keys())} "
                        f"profile_type={'mobile' if mobile else 'desktop'}",
                    )
                except Exception as variant_exc:
                    self._dbg(task_id, f"variant-failed id={vid} error={variant_exc}")
                    issues = [{
                        "severity": "critical",
                        "code": "variant_execution_failed",
                        "title": "Профиль рендеринга не выполнен",
                        "details": str(variant_exc),
                    }]
                    all_issues.extend([{**i, "variant": label} for i in issues])
                    all_variants.append({
                        "variant_id": vid,
                        "variant_label": label,
                        "mobile": mobile,
                        "user_agent": ua,
                        "profile_type": "mobile" if mobile else "desktop",
                        "raw": {},
                        "rendered": {},
                        "missing": {"visible_text": [], "headings": [], "links": [], "images": [], "structured_data": []},
                        "meta_non_seo": {"raw": {}, "rendered": {}, "comparison": {"total": 0, "same": 0, "changed": 0, "only_rendered": 0, "only_raw": 0, "items": []}},
                        "seo_required": {"total": 0, "pass": 0, "warn": 0, "fail": 0, "items": []},
                        "metrics": {"total_missing": 0.0, "rendered_total": 0.0, "missing_pct": 0.0, "score": 0.0},
                        "timings": {"raw_s": 0.0, "rendered_s": 0.0},
                        "timing_nojs_ms": {},
                        "timing_js_ms": {},
                        "issues": issues,
                        "recommendations": ["Проверьте окружение Playwright и доступность целевого URL."],
                        "screenshots": {},
                    })
                notify(10 + int(idx * 40), f"Завершено: {label}")

        self._dbg(
            task_id,
            "variants-collected=" + ", ".join(
                [f"{v.get('variant_id')}:{v.get('variant_label')}:{v.get('profile_type')}" for v in all_variants]
            ),
        )
        critical = sum(1 for i in all_issues if i.get("severity") == "critical")
        warning = sum(1 for i in all_issues if i.get("severity") == "warning")
        info = sum(1 for i in all_issues if i.get("severity") == "info")
        scores = [float(v.get("metrics", {}).get("score", 100)) for v in all_variants]
        missing_total = int(sum(float(v.get("metrics", {}).get("total_missing", 0)) for v in all_variants))
        missing_pct = [float(v.get("metrics", {}).get("missing_pct", 0)) for v in all_variants]
        avg_js = [float(v.get("timings", {}).get("rendered_s", 0)) * 1000 for v in all_variants]
        seo_required_failures = sum(int(v.get("seo_required", {}).get("fail", 0) or 0) for v in all_variants)
        seo_required_warnings = sum(int(v.get("seo_required", {}).get("warn", 0) or 0) for v in all_variants)

        notify(98, "Формирование итогового рендер-отчета")
        self._dbg(
            task_id,
            f"final summary variants={len(all_variants)} issues={len(all_issues)} "
            f"critical={critical} warning={warning} info={info}",
        )
        return {
            "task_type": "render_audit",
            "url": url,
            "completed_at": datetime.utcnow().isoformat(),
            "results": {
                "engine": "v2",
                "summary": {
                    "variants_total": len(all_variants),
                    "critical_issues": critical,
                    "warning_issues": warning,
                    "info_issues": info,
                    "score": round(sum(scores) / len(scores), 1) if scores else None,
                    "missing_total": missing_total,
                    "avg_missing_pct": round(sum(missing_pct) / len(missing_pct), 1) if missing_pct else 0,
                    "avg_raw_load_ms": 0,
                    "avg_js_load_ms": round(sum(avg_js) / len(avg_js), 1) if avg_js else 0,
                    "seo_required_failures": seo_required_failures,
                    "seo_required_warnings": seo_required_warnings,
                },
                "variants": all_variants,
                "issues": all_issues,
                "issues_count": len(all_issues),
                "recommendations": [
                    "Сначала исправьте критичные расхождения между JS и no-JS." if critical > 0 else "Критичных расхождений между JS и no-JS не обнаружено.",
                    "Снизьте зависимость SEO-контента от клиентского JavaScript." if warning > 0 else "",
                ],
                "artifacts": {"screenshot_dir": str(shot_dir), "screenshots": all_shots},
            },
        }
