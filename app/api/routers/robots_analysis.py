import re
from collections import defaultdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp
import requests

from app.api.routers import robots_fetch as _robots_fetch
from app.api.routers.robots_fetch import (
    _decode_sitemap_payload,
    _get_public_target_error,
    _looks_like_sitemap_bytes,
    _safe_fetch_with_redirects_sync,
)


async def _safe_fetch_with_redirects_async(*args, **kwargs):
    return await _robots_fetch._safe_fetch_with_redirects_async(*args, **kwargs)


EXPECTED_BOTS = ["googlebot", "yandex", "bingbot"]

SENSITIVE_PATHS = [
    "/admin", "/wp-admin", "/administrator", "/cgi-bin", "/config",
    "/database", "/backup", "/logs", "/phpmyadmin", "/.git", "/.env",
    "/wp-config", "/include", "/tmp", "/temp", "/private", "/secret",
    "/uploads",
]

RECOMMENDATIONS = [
    "Группируйте правила по User-agent для лучшей читаемости",
    "Блокируйте служебные папки: /admin, /tmp, /backup, /.git",
    "Не блокируйте CSS и JS файлы - это мешает сканированию",
    "Используйте Crawl-delay для больших сайтов",
    "Всегда указывайте Sitemap с полным URL",
    "Удаляйте дублирующиеся правила",
    "Избегайте Disallow: / если не хотите полностью заблокировать сайт",
    "Используйте Allow для создания исключений из Disallow",
    "Проверяйте файл в Google Search Console",
    "Учитывайте различия интерпретации директив разными поисковиками",
]

# Google ignores Crawl-delay; keep default recommendations aligned with current search guidance.
RECOMMENDATIONS = [item for item in RECOMMENDATIONS if "Crawl-delay" not in item]

UNSUPPORTED_ROBOTS_DIRECTIVES = {
    "noindex",
    "nofollow",
    "index",
    "noarchive",
    "nosnippet",
    "unavailable_after",
}


class Rule:
    def __init__(self, user_agent: str, path: str, line: int):
        self.user_agent = user_agent
        self.path = path
        self.line = line


class Group:
    def __init__(self):
        self.user_agents = []
        self.disallow = []
        self.allow = []


class ParseResult:
    def __init__(self):
        self.groups = []
        self.sitemaps = []
        self.crawl_delays = {}
        self.clean_params = []
        self.hosts = []
        self.raw_lines = []
        self.syntax_errors = []
        self.warnings = []
        self.unsupported_directives = []
        # For compatibility
        self.all_disallow = []
        self.all_allow = []


def parse_robots(text: str) -> ParseResult:
    """Parse robots.txt content - FULL original implementation"""
    lines = text.splitlines()
    result = ParseResult()
    result.raw_lines = lines
    
    current_group = None
    current_group_closed = False
    
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            err = f"Строка {idx}: Неверный синтаксис - отсутствует ':'"
            result.syntax_errors.append({"line": idx, "error": err, "content": raw})
            result.warnings.append(err)
            continue
        
        key, value = stripped.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        
        if key == "user-agent":
            if current_group is None or current_group_closed:
                current_group = Group()
                result.groups.append(current_group)
                current_group_closed = False
            current_group.user_agents.append(value)
            
        elif key == "disallow":
            current_group_closed = True
            if not current_group or not current_group.user_agents:
                err = f"Строка {idx}: Disallow без предшествующего User-agent"
                result.syntax_errors.append({"line": idx, "error": err, "content": raw})
                result.warnings.append(err)
                continue
            if value and not value.startswith("/"):
                result.warnings.append(f"Строка {idx}: Путь '{value}' должен начинаться с '/'")
            for ua in current_group.user_agents:
                rule = Rule(ua, value, idx)
                current_group.disallow.append(rule)
                result.all_disallow.append(rule)
                
        elif key == "allow":
            current_group_closed = True
            if not current_group or not current_group.user_agents:
                err = f"Строка {idx}: Allow без предшествующего User-agent"
                result.syntax_errors.append({"line": idx, "error": err, "content": raw})
                result.warnings.append(err)
                continue
            if value and not value.startswith("/"):
                result.warnings.append(f"Строка {idx}: Путь '{value}' должен начинаться с '/'")
            for ua in current_group.user_agents:
                rule = Rule(ua, value, idx)
                current_group.allow.append(rule)
                result.all_allow.append(rule)
                
        elif key == "sitemap":
            current_group_closed = True
            if value and not value.startswith("http://") and not value.startswith("https://"):
                result.warnings.append(f"Строка {idx}: Sitemap должен содержать полный URL")
            result.sitemaps.append(value)
            
        elif key == "crawl-delay":
            current_group_closed = True
            if not current_group or not current_group.user_agents:
                err = f"Строка {idx}: Crawl-delay без предшествующего User-agent"
                result.syntax_errors.append({"line": idx, "error": err, "content": raw})
                result.warnings.append(err)
                continue
            try:
                delay = float(value)
                if delay < 0:
                    raise ValueError("negative")
                for ua in current_group.user_agents:
                    result.crawl_delays[ua] = delay
            except Exception:
                err = f"Строка {idx}: Некорректный Crawl-delay: '{value}'"
                result.syntax_errors.append({"line": idx, "error": err, "content": raw})
                result.warnings.append(err)
                continue
                
        elif key == "clean-param":
            current_group_closed = True
            result.clean_params.append(value)
            
        elif key == "host":
            current_group_closed = True
            result.hosts.append(value)

        elif key in UNSUPPORTED_ROBOTS_DIRECTIVES:
            current_group_closed = True
            result.unsupported_directives.append({
                "line": idx,
                "directive": key,
                "value": value,
            })
            result.warnings.append(
                f"Line {idx}: '{key}' in robots.txt is not supported by Google; use meta robots or X-Robots-Tag."
            )

        else:
            current_group_closed = True
            result.warnings.append(f"Строка {idx}: Неизвестная директива '{key}' - будет проигнорирована")
    
    return result


def find_duplicates(all_rules: List[Rule], label: str) -> List[str]:
    """Find duplicate rules"""
    warnings = []
    by_value: Dict[str, List[int]] = defaultdict(list)
    for rule in all_rules:
        key = f"{rule.user_agent.lower()}|{rule.path}"
        by_value[key].append(rule.line)
    for key, line_nos in by_value.items():
        if len(line_nos) > 1:
            ua, path = key.split("|", 1)
            lines = ", ".join(str(n) for n in line_nos)
            warnings.append(f"Дублирующееся правило {label} для {ua}: {path} (строки: {lines})")
    return warnings


def dedupe_keep_order(items: List[str]) -> List[str]:
    """Remove duplicates preserving original order."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_param_merge_recommendations(result: ParseResult) -> List[str]:
    """Yandex Clean-param checks and recommendations."""
    recs: List[str] = []
    clean_params = [str(v or "").strip() for v in result.clean_params if str(v or "").strip()]
    if not clean_params:
        query_disallow = 0
        for group in result.groups:
            for rule in (group.disallow or []):
                path = str(rule.path or "")
                if "?" in path or "=" in path:
                    query_disallow += 1
        if query_disallow >= 3:
            recs.append(
                "Detected many parameterized Disallow rules. For Yandex crawling optimization, "
                "consider using 'Clean-param' for non-content query params."
            )
        return recs

    auto_ignored = {
        "ysclid", "yrclid", "utm_source", "utm_medium", "utm_campaign",
        "utm_term", "utm_content", "yclid", "gclid", "fbclid",
    }

    has_yandex_group = any(
        "yandex" in str(ua or "").lower()
        for group in result.groups
        for ua in (group.user_agents or [])
    )
    if not has_yandex_group:
        recs.append(
            "Clean-param is Yandex-specific. Consider a dedicated 'User-agent: Yandex' section for these rules."
        )

    seen_rules: Dict[str, int] = defaultdict(int)
    path_to_params: Dict[str, List[str]] = defaultdict(list)

    for raw in clean_params:
        seen_rules[raw] += 1
        if len(raw) > 500:
            recs.append(
                f"Clean-param rule exceeds 500 chars and may be ignored by Yandex: '{raw[:80]}...'"
            )

        parts = raw.split(None, 1)
        params_part = parts[0].strip() if parts else ""
        path_part = parts[1].strip() if len(parts) > 1 else ""

        params = [p.strip() for p in params_part.split("&") if p.strip()]
        if not params:
            recs.append(f"Invalid Clean-param syntax: '{raw}'. Expected 'param1&param2 [path]'.")
            continue

        invalid_params = [
            p for p in params
            if ("?" in p or "=" in p or "/" in p or " " in p or "&" in p)
        ]
        if invalid_params:
            recs.append(
                f"Invalid parameter token(s) in Clean-param '{raw}': {', '.join(invalid_params)}."
            )

        if path_part:
            if not path_part.startswith("/"):
                recs.append(
                    f"Clean-param path should start with '/': '{raw}'."
                )
            if "?" in path_part or "&" in path_part:
                recs.append(
                    f"Clean-param path should be URL prefix only (no query): '{raw}'."
                )

        lower_set = {p.lower() for p in params}
        if lower_set and lower_set.issubset(auto_ignored):
            recs.append(
                f"Rule '{raw}' targets mostly tracking params that Yandex can often ignore automatically. "
                "Keep it only if duplicate URLs are still reported in Webmaster."
            )

        path_key = path_part or "*"
        path_to_params[path_key].extend(params)

    for raw, cnt in seen_rules.items():
        if cnt > 1:
            recs.append(f"Duplicate Clean-param rule repeated {cnt} times: '{raw}'.")

    for path_key, params in path_to_params.items():
        uniq = dedupe_keep_order([p for p in params if p])
        if len(uniq) >= 3:
            merged = "&".join(uniq[:8])
            if path_key == "*":
                recs.append(
                    "Potential optimization (Yandex-only, requires validation): "
                    f"if ALL these params never change page content, you may merge into one global rule: "
                    f"'Clean-param: {merged}'. Otherwise keep path-specific rules."
                )
            else:
                recs.append(
                    "Potential optimization (Yandex-only, requires validation): "
                    f"for path '{path_key}', if these params do not affect document content, "
                    f"you may merge into: 'Clean-param: {merged} {path_key}'."
                )

    return dedupe_keep_order(recs)




def _normalize_rule_path(path: str) -> str:
    value = (path or "").strip()
    if value.endswith("*") and len(value) > 1:
        value = value[:-1]
    return value


def analyze_group_and_rule_conflicts(result: ParseResult) -> Dict[str, Any]:
    warnings: List[str] = []
    details: List[Dict[str, Any]] = []
    groups_by_ua: Dict[str, int] = defaultdict(int)
    rules_by_ua: Dict[str, Dict[str, set]] = defaultdict(lambda: {"allow": set(), "disallow": set()})

    for group in result.groups:
        for ua in (group.user_agents or []):
            ua_l = (ua or "").strip().lower()
            if not ua_l:
                continue
            groups_by_ua[ua_l] += 1
            for rule in (group.allow or []):
                rules_by_ua[ua_l]["allow"].add(_normalize_rule_path(rule.path))
            for rule in (group.disallow or []):
                rules_by_ua[ua_l]["disallow"].add(_normalize_rule_path(rule.path))

    for ua, count in groups_by_ua.items():
        if count > 1:
            warnings.append(
                f"User-agent '{ua}' appears in multiple groups ({count}). "
                "Merge into one group to avoid ambiguous interpretation."
            )
            details.append({"type": "ua_fragmented_groups", "user_agent": ua, "groups": count})

    for ua, packs in rules_by_ua.items():
        conflicted = sorted((packs["allow"] & packs["disallow"]) - {""})
        for path in conflicted:
            warnings.append(
                f"Conflicting directives for '{ua}': both Allow and Disallow for '{path}'. "
                "This can lead to crawler-specific behavior differences."
            )
            details.append({"type": "allow_disallow_same_path", "user_agent": ua, "path": path})

    return {"warnings": warnings, "details": details}


def analyze_longest_match_behaviour(result: ParseResult) -> Dict[str, Any]:
    notes: List[str] = []
    details: List[Dict[str, Any]] = []
    rules_by_ua: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"allow": [], "disallow": []})

    for group in result.groups:
        for ua in (group.user_agents or []):
            ua_l = (ua or "").strip().lower()
            if not ua_l:
                continue
            for rule in (group.allow or []):
                path = _normalize_rule_path(rule.path)
                if path:
                    rules_by_ua[ua_l]["allow"].append(path)
            for rule in (group.disallow or []):
                path = _normalize_rule_path(rule.path)
                if path:
                    rules_by_ua[ua_l]["disallow"].append(path)

    for ua, packs in rules_by_ua.items():
        for allow_path in packs["allow"]:
            for disallow_path in packs["disallow"]:
                if disallow_path.startswith(allow_path) and len(disallow_path) > len(allow_path):
                    notes.append(
                        f"Longest-match note for '{ua}': Allow '{allow_path}' is broader than "
                        f"Disallow '{disallow_path}'. Deeper URLs may remain blocked."
                    )
                    details.append(
                        {
                            "user_agent": ua,
                            "allow_path": allow_path,
                            "disallow_path": disallow_path,
                            "type": "allow_broader_than_disallow",
                        }
                    )
        if len(notes) >= 30:
            break

    return {"notes": dedupe_keep_order(notes)[:30], "details": details[:100]}


def validate_host_directives(hosts: List[str]) -> Dict[str, Any]:
    warnings: List[str] = []
    normalized_hosts = [str(h or "").strip() for h in hosts if str(h or "").strip()]
    uniq = dedupe_keep_order(normalized_hosts)
    host_re = re.compile(r"^[a-z0-9.-]+(?::\d+)?$", re.I)

    if len(uniq) > 1:
        warnings.append(
            f"Multiple Host directives found ({len(uniq)}). "
            "Yandex expects a single canonical host value."
        )

    for host in uniq:
        if host.startswith(("http://", "https://")) or "/" in host:
            warnings.append(
                f"Host directive '{host}' looks invalid. Use host only (no scheme/path), e.g. 'example.com'."
            )
        elif not host_re.fullmatch(host):
            warnings.append(
                f"Host directive '{host}' has non-standard format for Yandex."
            )

    return {"warnings": warnings, "hosts": uniq}

def validate_sitemaps(sitemaps: List[str], timeout: int = 4, max_checks: int = 5) -> List[Dict[str, Any]]:
    """Validate sitemap URLs declared in robots.txt."""
    checks: List[Dict[str, Any]] = []
    unique_sitemaps = dedupe_keep_order([s for s in sitemaps if isinstance(s, str) and s.strip()])
    for index, sm in enumerate(unique_sitemaps):
        sm = sm.strip()
        if index >= max_checks:
            checks.append({
                "url": sm,
                "ok": None,
                "status_code": None,
                "content_type": None,
                "error": "Skipped (limit reached)"
            })
            continue
        try:
            parsed = urlparse(sm)
            if parsed.scheme not in ("http", "https"):
                checks.append({
                    "url": sm,
                    "ok": False,
                    "status_code": None,
                    "content_type": None,
                    "error": "Invalid sitemap URL scheme"
                })
                continue
            target_error = _get_public_target_error(sm)
            if target_error:
                checks.append({
                    "url": sm,
                    "ok": False,
                    "status_code": None,
                    "content_type": None,
                    "error": target_error
                })
                continue
            resp = _safe_fetch_with_redirects_sync(
                requests,
                sm,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            decoded_content, _ = _decode_sitemap_payload(
                resp.content,
                getattr(resp, "url", sm),
                getattr(resp, "headers", {}),
            )
            content_type = (resp.headers.get("Content-Type") or "").lower()
            looks_xml = _looks_like_sitemap_bytes(decoded_content) or (
                "xml" in content_type and decoded_content[:20000].strip()
            )
            ok = resp.status_code == 200 and looks_xml
            checks.append({
                "url": sm,
                "ok": ok,
                "status_code": resp.status_code,
                "content_type": content_type,
                "error": None if ok else "Sitemap not accessible or not XML"
            })
        except Exception as e:
            checks.append({
                "url": sm,
                "ok": False,
                "status_code": None,
                "content_type": None,
                "error": str(e)
            })
    return checks


async def validate_sitemaps_async(sitemaps: List[str], timeout: int = 4, max_checks: int = 5) -> List[Dict[str, Any]]:
    """Async validation for sitemap URLs declared in robots.txt."""
    checks: List[Dict[str, Any]] = []
    unique_sitemaps = dedupe_keep_order([s for s in sitemaps if isinstance(s, str) and s.strip()])
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        for index, sm in enumerate(unique_sitemaps):
            sm = sm.strip()
            if index >= max_checks:
                checks.append({
                    "url": sm,
                    "ok": None,
                    "status_code": None,
                    "content_type": None,
                    "error": "Skipped (limit reached)"
                })
                continue
            try:
                parsed = urlparse(sm)
                if parsed.scheme not in ("http", "https"):
                    checks.append({
                        "url": sm,
                        "ok": False,
                        "status_code": None,
                        "content_type": None,
                        "error": "Invalid sitemap URL scheme"
                    })
                    continue
                target_error = _get_public_target_error(sm)
                if target_error:
                    checks.append({
                        "url": sm,
                        "ok": False,
                        "status_code": None,
                        "content_type": None,
                        "error": target_error
                    })
                    continue
                resp = await _safe_fetch_with_redirects_async(
                    session,
                    sm,
                    timeout=timeout,
                    read_text=False,
                )
                decoded_content, _ = _decode_sitemap_payload(
                    resp.content,
                    resp.url,
                    resp.headers,
                )
                content_type = str(resp.headers.get("Content-Type") or "").lower()
                looks_xml = _looks_like_sitemap_bytes(decoded_content) or (
                    "xml" in content_type and decoded_content[:20000].strip()
                )
                ok = resp.status_code == 200 and looks_xml
                checks.append({
                    "url": sm,
                    "ok": ok,
                    "status_code": resp.status_code,
                    "content_type": content_type,
                    "error": None if ok else "Sitemap not accessible or not XML"
                })
            except Exception as e:
                checks.append({
                    "url": sm,
                    "ok": False,
                    "status_code": None,
                    "content_type": None,
                    "error": str(e)
                })
    return checks


def build_quality_metrics(
    issues: List[str],
    warnings: List[str],
    syntax_errors: List[Dict[str, Any]],
    missing_bots: List[str],
    sitemap_checks: List[Dict[str, Any]],
    full_block: bool,
    blocked_ext: List[str]
) -> Dict[str, Any]:
    """Build score, grade, production readiness and top fixes."""
    score = 100
    critical_count = len(issues)
    warning_count = len(warnings)
    syntax_count = len(syntax_errors)

    if full_block:
        score -= 70
    if blocked_ext:
        score -= 20
    score -= min(20, syntax_count * 2)
    score -= min(22, warning_count)
    if missing_bots:
        score -= min(8, len(missing_bots) * 2)

    has_sitemap = any(check.get("ok") for check in sitemap_checks) if sitemap_checks else False
    if sitemap_checks and not has_sitemap:
        score -= 10
    if not sitemap_checks:
        score -= 8

    score = max(0, min(100, score))
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    top_fixes: List[Dict[str, str]] = []
    if full_block:
        top_fixes.append({
            "priority": "critical",
            "title": "Уберите глобальную блокировку сайта",
            "why": "Disallow: / для * блокирует индексацию всего сайта.",
            "action": "Оставьте только точечные запреты для служебных разделов."
        })
    if blocked_ext:
        top_fixes.append({
            "priority": "high",
            "title": "Разблокируйте CSS/JS",
            "why": "Блокировка CSS/JS ухудшает рендеринг для поисковых роботов.",
            "action": "Удалите правила, блокирующие .css и .js."
        })
    if missing_bots:
        top_fixes.append({
            "priority": "medium",
            "title": "Добавьте группы для ключевых ботов",
            "why": "Явные правила для поисковых ботов делают поведение предсказуемым.",
            "action": f"Добавьте User-agent группы для: {', '.join(missing_bots)}."
        })
    if sitemap_checks and not has_sitemap:
        top_fixes.append({
            "priority": "high",
            "title": "Исправьте sitemap URL",
            "why": "Sitemap недоступен или возвращает некорректный контент.",
            "action": "Проверьте URL sitemap в robots.txt и доступность по HTTP 200."
        })
    if syntax_count:
        top_fixes.append({
            "priority": "high",
            "title": "Исправьте синтаксис robots.txt",
            "why": "Синтаксические ошибки могут приводить к игнорированию правил.",
            "action": "Исправьте строки с ошибками в блоке syntax_errors."
        })

    production_ready = score >= 75 and not full_block and not blocked_ext
    return {
        "quality_score": score,
        "quality_grade": grade,
        "production_ready": production_ready,
        "top_fixes": top_fixes[:5],
        "severity_counts": {
            "critical": critical_count,
            "warning": warning_count,
            "info": 0
        }
    }


def build_issues_and_warnings(result: ParseResult, sitemap_checks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build issues, warnings, and recommendations - FULL original implementation"""
    issues: List[str] = []
    warnings: List[str] = list(result.warnings)
    
    all_disallow: List[Rule] = []
    all_allow: List[Rule] = []
    for group in result.groups:
        all_disallow.extend(group.disallow)
        all_allow.extend(group.allow)
    
    warnings.extend(find_duplicates(all_disallow, "Disallow"))
    warnings.extend(find_duplicates(all_allow, "Allow"))

    conflict_scan = analyze_group_and_rule_conflicts(result)
    warnings.extend(conflict_scan["warnings"])

    longest_match_scan = analyze_longest_match_behaviour(result)
    warnings.extend(longest_match_scan["notes"])

    host_scan = validate_host_directives(result.hosts)
    warnings.extend(host_scan["warnings"])
    if result.crawl_delays:
        warnings.append("Crawl-delay found: Google ignores this directive.")
    
    if not result.sitemaps:
        warnings.append("Не указана директива Sitemap")
    
    # Check for full site block
    full_block = any(
        rule.user_agent.lower() == "*" and rule.path.strip() == "/"
        for rule in all_disallow
    )
    if full_block:
        issues.append("КРИТИЧНО: Весь сайт заблокирован для всех роботов (Disallow: /)")
    
    # Check blocked extensions
    blocked_ext = []
    for ext in [".css", ".js"]:
        if any(ext in rule.path for rule in all_disallow):
            blocked_ext.append(ext)
    if blocked_ext:
        issues.append(
            "Заблокированы важные ресурсы: "
            + ", ".join(blocked_ext)
            + " - это мешает сканированию"
        )
    
    # Check expected bots
    present_agents = set()
    for group in result.groups:
        for ua in group.user_agents:
            present_agents.add(ua.lower())
    
    missing_bots = [
        bot for bot in EXPECTED_BOTS if not any(bot in ua for ua in present_agents)
    ]
    if missing_bots:
        warnings.append("Рекомендуется добавить правила для: " + ", ".join(missing_bots))
    
    # Check sensitive paths
    unblocked_sensitive = []
    blocked_paths = set(rule.path for rule in all_disallow)
    for path in SENSITIVE_PATHS:
        if path not in blocked_paths:
            unblocked_sensitive.append(path)
    if unblocked_sensitive:
        warnings.append(
            "Рекомендуется заблокировать: "
            + ", ".join(unblocked_sensitive[:8])
        )
    
    # Generate recommendations and quality metrics
    if result.unsupported_directives:
        warnings.append(
            "Unsupported directives found in robots.txt (e.g., noindex/nofollow). "
            "Use meta robots or X-Robots-Tag for indexation control."
        )
    param_recs = build_param_merge_recommendations(result)
    all_recommendations = dedupe_keep_order(RECOMMENDATIONS.copy() + param_recs)
    warnings = dedupe_keep_order(warnings)
    issues = dedupe_keep_order(issues)

    if sitemap_checks is None:
        sitemap_checks = validate_sitemaps(result.sitemaps)
    metrics = build_quality_metrics(
        issues=issues,
        warnings=warnings,
        syntax_errors=result.syntax_errors,
        missing_bots=missing_bots,
        sitemap_checks=sitemap_checks,
        full_block=full_block,
        blocked_ext=blocked_ext
    )
    info_issues = [
        f"Обнаружено групп правил: {len(result.groups)}",
        f"Проверено sitemap URL: {len(sitemap_checks)}"
    ]
    if metrics["production_ready"]:
        info_issues.append("Robots.txt готов к продакшн-использованию.")
    else:
        info_issues.append("Требуются правки перед продакшн-использованием.")
    
    return {
        "issues": issues,
        "critical_issues": issues,
        "warnings": warnings,
        "warning_issues": warnings,
        "info_issues": info_issues,
        "recommendations": all_recommendations,
        "param_recommendations": param_recs,
        "present_agents": list(present_agents),
        "missing_bots": missing_bots,
        "sitemaps": result.sitemaps,
        "sitemap_checks": sitemap_checks,
        "crawl_delays": result.crawl_delays,
        "hosts": result.hosts,
        "host_validation": host_scan,
        "directive_conflicts": conflict_scan,
        "longest_match_analysis": longest_match_scan,
        "unsupported_directives": result.unsupported_directives,
        "syntax_errors": result.syntax_errors,
        "quality_score": metrics["quality_score"],
        "quality_grade": metrics["quality_grade"],
        "production_ready": metrics["production_ready"],
        "top_fixes": metrics["top_fixes"],
        "severity_counts": {
            "critical": metrics["severity_counts"]["critical"],
            "warning": metrics["severity_counts"]["warning"],
            "info": len(info_issues)
        },
        "quick_status": "pass" if metrics["production_ready"] else ("fail" if metrics["quality_score"] < 60 else "warn")
    }


def collect_stats(result: ParseResult) -> Dict[str, int]:
    """Collect statistics from parsed result"""
    stats = {
        "user_agents": 0,
        "disallow_rules": 0,
        "allow_rules": 0,
        "sitemaps": len(result.sitemaps),
        "crawl_delays": len(result.crawl_delays),
        "clean_params": len(result.clean_params),
        "hosts": len(result.hosts),
        "lines_count": len(result.raw_lines),
    }
    for group in result.groups:
        stats["user_agents"] += len(group.user_agents)
        stats["disallow_rules"] += len(group.disallow)
        stats["allow_rules"] += len(group.allow)
    return stats
