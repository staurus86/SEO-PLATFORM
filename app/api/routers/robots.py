"""
Robots.txt Checker, Sitemap Validator, Bot Accessibility Checker,
Robots.txt Visual Constructor & URL Validator router.
"""
import asyncio
import re
import json
import time
import math
import random
import aiohttp
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union, Tuple
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.validators import URLModel, normalize_http_input as _normalize_http_input
from app.api.routers._task_store import create_task_result, create_task_pending, update_task_state

from app.api.routers import robots_fetch as _robots_fetch
from app.api.routers.robots_fetch import (
    _AsyncSessionShim,
    _HttpResponseData,
    _async_http_get,
    _decode_http_text,
    _decode_sitemap_payload,
    _get_public_target_error,
    _is_public_hostname,
    _is_public_ip_address,
    _looks_like_sitemap_bytes,
    _response_header,
    _response_looks_gzipped_sitemap,
    _root_site_url,
    _safe_fetch_with_redirects_sync,
)

router = APIRouter(tags=["SEO Tools"])


# ============ ORIGINAl ROBOTS AUDIT LOGIC ============
from app.api.routers.robots_analysis import (
    EXPECTED_BOTS,
    Group,
    ParseResult,
    RECOMMENDATIONS,
    Rule,
    SENSITIVE_PATHS,
    UNSUPPORTED_ROBOTS_DIRECTIVES,
    analyze_group_and_rule_conflicts,
    analyze_longest_match_behaviour,
    build_issues_and_warnings,
    build_param_merge_recommendations,
    build_quality_metrics,
    collect_stats,
    dedupe_keep_order,
    find_duplicates,
    parse_robots,
    validate_host_directives,
    validate_sitemaps,
    validate_sitemaps_async,
)


async def _safe_fetch_with_redirects_async(*args, **kwargs):
    kwargs.setdefault("async_http_get_fn", _async_http_get)
    return await _robots_fetch._safe_fetch_with_redirects_async(*args, **kwargs)


def fetch_robots(url: str, timeout: int = 20) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """Fetch robots.txt and return (content, status_code, error)"""
    try:
        normalized = _normalize_http_input(url)
        if not normalized:
            return None, None, "Invalid URL"
        root = _root_site_url(normalized)
        safety_error = _get_public_target_error(root)
        if safety_error:
            return None, None, safety_error
        robots_url = urljoin(root + "/", "robots.txt")
        resp = _safe_fetch_with_redirects_sync(
            requests,
            robots_url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        return resp.text, resp.status_code, None
    except requests.exceptions.Timeout:
        return None, None, "Timeout"
    except requests.exceptions.ConnectionError:
        return None, None, "Connection Error"
    except Exception as e:
        return None, None, str(e)


async def fetch_robots_async(url: str, timeout: int = 20, use_proxy: bool = False) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """Async fetch for robots.txt returning (content, status_code, error)."""
    try:
        normalized = _normalize_http_input(url)
        if not normalized:
            return None, None, "Invalid URL"
        root = _root_site_url(normalized)
        safety_error = _get_public_target_error(root)
        if safety_error:
            return None, None, safety_error
        robots_url = urljoin(root + "/", "robots.txt")
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
            resp = await _safe_fetch_with_redirects_async(
                session,
                robots_url,
                timeout=timeout,
                read_text=True,
                use_proxy=use_proxy,
            )
        return resp.text, resp.status_code, None
    except asyncio.TimeoutError:
        return None, None, "Timeout"
    except aiohttp.ClientConnectionError:
        return None, None, "Connection Error"
    except Exception as e:
        return None, None, str(e)


def check_robots_full(url: str) -> Dict[str, Any]:
    """
    FULL robots.txt audit - полная интеграция оригинального скрипта
    Returns complete analysis matching original robots_audit.py
    """
    print(f"[ROBOTS] Starting full audit for: {url}")
    
    raw_text, status_code, error = fetch_robots(url)
    
    if error:
        return {
            "task_type": "robots_check",
            "url": url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "robots_txt_found": False,
                "status_code": None,
                "content_length": 0,
                "lines_count": 0,
                "user_agents": 0,
                "disallow_rules": 0,
                "allow_rules": 0,
                "sitemaps": [],
                "issues": [f"Ошибка загрузки: {error}"],
                "warnings": [],
                "recommendations": RECOMMENDATIONS,
                "syntax_errors": [],
                "critical_issues": [f"Ошибка загрузки: {error}"],
                "warning_issues": [],
                "info_issues": [],
                "hosts": [],
                "sitemap_checks": [],
                "quality_score": 0,
                "quality_grade": "F",
                "production_ready": False,
                "top_fixes": [{
                    "priority": "critical",
                    "title": "Обеспечьте доступность robots.txt",
                    "why": "Файл robots.txt недоступен, анализ и управление индексацией невозможны.",
                    "action": "Проверьте DNS/SSL/доступность сайта и путь /robots.txt."
                }],
                "severity_counts": {"critical": 1, "warning": 0, "info": 0},
                "error": error,
                "can_continue": False,
                "raw_content": "",
            }
        }
    
    if status_code != 200:
        http_notes: List[str] = []
        issues: List[str] = []
        warnings: List[str] = []
        top_fixes: List[Dict[str, str]] = []
        severity_counts = {"critical": 0, "warning": 0, "info": 0}
        quality_score = 35
        quality_grade = "F"

        if status_code in (404, 410):
            warnings.append(
                f"Robots.txt returns HTTP {status_code}. Google treats this as no robots restrictions (except 429 case)."
            )
            http_notes.append("Google: 4xx (except 429) is processed like missing robots.txt.")
            http_notes.append("Yandex: robots rules are unavailable for reading until file is restored.")
            top_fixes.append({
                "priority": "medium",
                "title": "Create /robots.txt",
                "why": "Search bots cannot read explicit crawl/indexing rules from your domain.",
                "action": "Publish /robots.txt and include at least User-agent and Sitemap directives."
            })
            severity_counts["warning"] = 1
            quality_score = 55
            quality_grade = "D"
        elif status_code == 429:
            issues.append("Robots.txt returns HTTP 429 (Too Many Requests). Bots may postpone crawling.")
            warnings.append("429 for robots.txt can delay crawling and make rules temporarily unavailable.")
            http_notes.append("Google: 429 is not treated as normal 4xx missing-file behavior.")
            top_fixes.append({
                "priority": "high",
                "title": "Stabilize robots.txt availability",
                "why": "Rate limiting blocks crawler access to robots directives.",
                "action": "Allow reliable access to /robots.txt without aggressive rate limits."
            })
            severity_counts["critical"] = 1
            severity_counts["warning"] = 1
            quality_score = 25
            quality_grade = "F"
        elif status_code in (401, 403):
            issues.append(f"Robots.txt returns HTTP {status_code}. Access to rules is restricted.")
            warnings.append("Bots may apply fallback behavior when robots.txt cannot be read.")
            http_notes.append("Google: 4xx (except 429) is generally treated as no robots file.")
            http_notes.append("Yandex: inaccessible robots.txt can affect predictable crawl control.")
            top_fixes.append({
                "priority": "high",
                "title": "Open access to /robots.txt",
                "why": "Crawler cannot read crawl policy due to authorization/forbidden response.",
                "action": "Return HTTP 200 for public /robots.txt and remove auth blocks."
            })
            severity_counts["critical"] = 1
            severity_counts["warning"] = 1
            quality_score = 30
            quality_grade = "F"
        elif 500 <= status_code < 600:
            issues.append(f"Robots.txt returns server error HTTP {status_code}.")
            warnings.append("Server errors on robots.txt can pause or destabilize crawler behavior.")
            http_notes.append("Google: on 5xx, crawling may pause; cached robots may be reused for a limited period.")
            http_notes.append("Yandex: unavailable robots.txt reduces crawl predictability until recovered.")
            top_fixes.append({
                "priority": "critical",
                "title": "Fix server errors on /robots.txt",
                "why": "Search bots cannot reliably fetch robots directives.",
                "action": "Return stable HTTP 200 and monitor uptime for /robots.txt."
            })
            severity_counts["critical"] = 1
            severity_counts["warning"] = 1
            quality_score = 20
            quality_grade = "F"
        elif 300 <= status_code < 400:
            warnings.append(f"Robots.txt returns redirect HTTP {status_code}.")
            http_notes.append("Keep redirects short and stable; long redirect chains may break robots fetch.")
            top_fixes.append({
                "priority": "medium",
                "title": "Serve robots.txt directly",
                "why": "Redirect chains can prevent some crawlers from reaching final robots content.",
                "action": "Return HTTP 200 on canonical /robots.txt URL."
            })
            severity_counts["warning"] = 1
            quality_score = 45
            quality_grade = "E"
        else:
            issues.append(f"Robots.txt is unavailable with HTTP {status_code}.")
            top_fixes.append({
                "priority": "high",
                "title": "Restore robots.txt availability",
                "why": "Search bots cannot consume expected crawl rules.",
                "action": "Ensure /robots.txt returns HTTP 200 with valid directives."
            })
            severity_counts["critical"] = 1
            quality_score = 30
            quality_grade = "F"

        return {
            "task_type": "robots_check",
            "url": url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "robots_txt_found": False,
                "status_code": status_code,
                "content_length": len(raw_text) if raw_text else 0,
                "lines_count": len(raw_text.splitlines()) if raw_text else 0,
                "user_agents": 0,
                "disallow_rules": 0,
                "allow_rules": 0,
                "sitemaps": [],
                "issues": issues,
                "warnings": warnings,
                "recommendations": RECOMMENDATIONS,
                "syntax_errors": [],
                "critical_issues": issues,
                "warning_issues": warnings,
                "info_issues": http_notes,
                "hosts": [],
                "sitemap_checks": [],
                "quality_score": quality_score,
                "quality_grade": quality_grade,
                "production_ready": False,
                "top_fixes": top_fixes,
                "severity_counts": severity_counts,
                "error": None,
                "can_continue": True,
                "raw_content": raw_text or "",
                "http_status_analysis": {
                    "status_code": status_code,
                    "notes": http_notes
                }
            }
        }

    # Full parsing and analysis
    result = parse_robots(raw_text)
    stats = collect_stats(result)
    analysis = build_issues_and_warnings(result)
    if len(raw_text.encode("utf-8")) > 512000:
        analysis["warnings"] = dedupe_keep_order(
            analysis["warnings"] + ["robots.txt is larger than 500 KiB; Google ignores content after this limit."]
        )
        analysis["warning_issues"] = analysis["warnings"]
    
    # Build detailed response
    response = {
        "task_type": "robots_check",
        "url": url,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "results": {
            "robots_txt_found": True,
            "status_code": status_code,
            "content_length": len(raw_text),
            "lines_count": stats["lines_count"],
            "user_agents": stats["user_agents"],
            "disallow_rules": stats["disallow_rules"],
            "allow_rules": stats["allow_rules"],
            "sitemaps": analysis["sitemaps"],
            "issues": analysis["issues"],
            "critical_issues": analysis.get("critical_issues", analysis["issues"]),
            "warnings": analysis["warnings"],
            "warning_issues": analysis.get("warning_issues", analysis["warnings"]),
            "info_issues": analysis.get("info_issues", []),
            "recommendations": analysis["recommendations"],
            "syntax_errors": analysis["syntax_errors"],
            "hosts": analysis["hosts"],
            "crawl_delays": analysis["crawl_delays"],
            "clean_params": result.clean_params,
            "param_recommendations": analysis.get("param_recommendations", []),
            "present_agents": analysis["present_agents"],
            "missing_bots": analysis.get("missing_bots", []),
            "sitemap_checks": analysis.get("sitemap_checks", []),
            "quality_score": analysis.get("quality_score", 0),
            "quality_grade": analysis.get("quality_grade", "F"),
            "production_ready": analysis.get("production_ready", False),
            "top_fixes": analysis.get("top_fixes", []),
            "severity_counts": analysis.get("severity_counts", {"critical": len(analysis["issues"]), "warning": len(analysis["warnings"]), "info": 0}),
            "quick_status": analysis.get("quick_status", "warn"),
            "machine_summary": {
                "user_agents_count": stats["user_agents"],
                "disallow_count": stats["disallow_rules"],
                "allow_count": stats["allow_rules"],
                "sitemap_count": len(analysis.get("sitemaps", [])),
                "critical_count": len(analysis.get("critical_issues", analysis["issues"])),
                "warning_count": len(analysis.get("warning_issues", analysis["warnings"])),
                "score": analysis.get("quality_score", 0),
                "grade": analysis.get("quality_grade", "F"),
                "production_ready": analysis.get("production_ready", False)
            },
            "error": None,
            "can_continue": True,
            "raw_content": raw_text,
            # Detailed groups for UI
            "groups_detail": [
                {
                    "user_agents": group.user_agents,
                    "disallow": [{"path": r.path, "line": r.line} for r in group.disallow],
                    "allow": [{"path": r.path, "line": r.line} for r in group.allow],
                }
                for group in result.groups
            ],
        }
    }
    
    print(f"[ROBOTS] Audit completed: {stats['user_agents']} UAs, {stats['disallow_rules']} disallow rules")
    
    return response


async def check_robots_full_async(url: str, use_proxy: bool = False) -> Dict[str, Any]:
    """Async robots.txt audit using aiohttp for network-bound work."""
    print(f"[ROBOTS] Starting full audit for: {url}")

    raw_text, status_code, error = await fetch_robots_async(url, use_proxy=use_proxy)

    if error:
        return {
            "task_type": "robots_check",
            "url": url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "robots_txt_found": False,
                "status_code": None,
                "content_length": 0,
                "lines_count": 0,
                "user_agents": 0,
                "disallow_rules": 0,
                "allow_rules": 0,
                "sitemaps": [],
                "issues": [f"Ошибка загрузки: {error}"],
                "warnings": [],
                "recommendations": RECOMMENDATIONS,
                "syntax_errors": [],
                "critical_issues": [f"Ошибка загрузки: {error}"],
                "warning_issues": [],
                "info_issues": [],
                "hosts": [],
                "sitemap_checks": [],
                "quality_score": 0,
                "quality_grade": "F",
                "production_ready": False,
                "top_fixes": [{
                    "priority": "critical",
                    "title": "Обеспечьте доступность robots.txt",
                    "why": "Файл robots.txt недоступен, анализ и управление индексацией невозможны.",
                    "action": "Проверьте DNS/SSL/доступность сайта и путь /robots.txt."
                }],
                "severity_counts": {"critical": 1, "warning": 0, "info": 0},
                "error": error,
                "can_continue": False,
                "raw_content": "",
            }
        }

    if status_code != 200:
        http_notes: List[str] = []
        issues: List[str] = []
        warnings: List[str] = []
        top_fixes: List[Dict[str, str]] = []
        severity_counts = {"critical": 0, "warning": 0, "info": 0}
        quality_score = 35
        quality_grade = "F"

        if status_code in (404, 410):
            warnings.append(
                f"Robots.txt returns HTTP {status_code}. Google treats this as no robots restrictions (except 429 case)."
            )
            http_notes.append("Google: 4xx (except 429) is processed like missing robots.txt.")
            http_notes.append("Yandex: robots rules are unavailable for reading until file is restored.")
            top_fixes.append({
                "priority": "medium",
                "title": "Create /robots.txt",
                "why": "Search bots cannot read explicit crawl/indexing rules from your domain.",
                "action": "Publish /robots.txt and include at least User-agent and Sitemap directives."
            })
            severity_counts["warning"] = 1
            quality_score = 55
            quality_grade = "D"
        elif status_code == 429:
            issues.append("Robots.txt returns HTTP 429 (Too Many Requests). Bots may postpone crawling.")
            warnings.append("429 for robots.txt can delay crawling and make rules temporarily unavailable.")
            http_notes.append("Google: 429 is not treated as normal 4xx missing-file behavior.")
            top_fixes.append({
                "priority": "high",
                "title": "Stabilize robots.txt availability",
                "why": "Rate limiting blocks crawler access to robots directives.",
                "action": "Allow reliable access to /robots.txt without aggressive rate limits."
            })
            severity_counts["critical"] = 1
            severity_counts["warning"] = 1
            quality_score = 25
            quality_grade = "F"
        elif status_code in (401, 403):
            issues.append(f"Robots.txt returns HTTP {status_code}. Access to rules is restricted.")
            warnings.append("Bots may apply fallback behavior when robots.txt cannot be read.")
            http_notes.append("Google: 4xx (except 429) is generally treated as no robots file.")
            http_notes.append("Yandex: inaccessible robots.txt can affect predictable crawl control.")
            top_fixes.append({
                "priority": "high",
                "title": "Open access to /robots.txt",
                "why": "Crawler cannot read crawl policy due to authorization/forbidden response.",
                "action": "Return HTTP 200 for public /robots.txt and remove auth blocks."
            })
            severity_counts["critical"] = 1
            severity_counts["warning"] = 1
            quality_score = 30
            quality_grade = "F"
        elif 500 <= status_code < 600:
            issues.append(f"Robots.txt returns server error HTTP {status_code}.")
            warnings.append("Server errors on robots.txt can pause or destabilize crawler behavior.")
            http_notes.append("Google: on 5xx, crawling may pause; cached robots may be reused for a limited period.")
            http_notes.append("Yandex: unavailable robots.txt reduces crawl predictability until recovered.")
            top_fixes.append({
                "priority": "critical",
                "title": "Fix server errors on /robots.txt",
                "why": "Search bots cannot reliably fetch robots directives.",
                "action": "Return stable HTTP 200 and monitor uptime for /robots.txt."
            })
            severity_counts["critical"] = 1
            severity_counts["warning"] = 1
            quality_score = 20
            quality_grade = "F"
        elif 300 <= status_code < 400:
            warnings.append(f"Robots.txt returns redirect HTTP {status_code}.")
            http_notes.append("Keep redirects short and stable; long redirect chains may break robots fetch.")
            top_fixes.append({
                "priority": "medium",
                "title": "Serve robots.txt directly",
                "why": "Redirect chains can prevent some crawlers from reaching final robots content.",
                "action": "Return HTTP 200 on canonical /robots.txt URL."
            })
            severity_counts["warning"] = 1
            quality_score = 45
            quality_grade = "E"
        else:
            issues.append(f"Robots.txt is unavailable with HTTP {status_code}.")
            top_fixes.append({
                "priority": "high",
                "title": "Restore robots.txt availability",
                "why": "Search bots cannot consume expected crawl rules.",
                "action": "Ensure /robots.txt returns HTTP 200 with valid directives."
            })
            severity_counts["critical"] = 1
            quality_score = 30
            quality_grade = "F"

        return {
            "task_type": "robots_check",
            "url": url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "robots_txt_found": False,
                "status_code": status_code,
                "content_length": len(raw_text) if raw_text else 0,
                "lines_count": len(raw_text.splitlines()) if raw_text else 0,
                "user_agents": 0,
                "disallow_rules": 0,
                "allow_rules": 0,
                "sitemaps": [],
                "issues": issues,
                "warnings": warnings,
                "recommendations": RECOMMENDATIONS,
                "syntax_errors": [],
                "critical_issues": issues,
                "warning_issues": warnings,
                "info_issues": http_notes,
                "hosts": [],
                "sitemap_checks": [],
                "quality_score": quality_score,
                "quality_grade": quality_grade,
                "production_ready": False,
                "top_fixes": top_fixes,
                "severity_counts": severity_counts,
                "error": None,
                "can_continue": True,
                "raw_content": raw_text or "",
                "http_status_analysis": {
                    "status_code": status_code,
                    "notes": http_notes
                }
            }
        }

    result = parse_robots(raw_text)
    stats = collect_stats(result)
    sitemap_checks = await validate_sitemaps_async(result.sitemaps)
    analysis = build_issues_and_warnings(result, sitemap_checks=sitemap_checks)
    if len(raw_text.encode("utf-8")) > 512000:
        analysis["warnings"] = dedupe_keep_order(
            analysis["warnings"] + ["robots.txt is larger than 500 KiB; Google ignores content after this limit."]
        )
        analysis["warning_issues"] = analysis["warnings"]

    response = {
        "task_type": "robots_check",
        "url": url,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "results": {
            "robots_txt_found": True,
            "status_code": status_code,
            "content_length": len(raw_text),
            "lines_count": stats["lines_count"],
            "user_agents": stats["user_agents"],
            "disallow_rules": stats["disallow_rules"],
            "allow_rules": stats["allow_rules"],
            "sitemaps": analysis["sitemaps"],
            "issues": analysis["issues"],
            "critical_issues": analysis.get("critical_issues", analysis["issues"]),
            "warnings": analysis["warnings"],
            "warning_issues": analysis.get("warning_issues", analysis["warnings"]),
            "info_issues": analysis.get("info_issues", []),
            "recommendations": analysis["recommendations"],
            "syntax_errors": analysis["syntax_errors"],
            "hosts": analysis["hosts"],
            "crawl_delays": analysis["crawl_delays"],
            "clean_params": result.clean_params,
            "param_recommendations": analysis.get("param_recommendations", []),
            "present_agents": analysis["present_agents"],
            "missing_bots": analysis.get("missing_bots", []),
            "sitemap_checks": analysis.get("sitemap_checks", []),
            "quality_score": analysis.get("quality_score", 0),
            "quality_grade": analysis.get("quality_grade", "F"),
            "production_ready": analysis.get("production_ready", False),
            "top_fixes": analysis.get("top_fixes", []),
            "severity_counts": analysis.get("severity_counts", {"critical": len(analysis["issues"]), "warning": len(analysis["warnings"]), "info": 0}),
            "quick_status": analysis.get("quick_status", "warn"),
            "machine_summary": {
                "user_agents_count": stats["user_agents"],
                "disallow_count": stats["disallow_rules"],
                "allow_count": stats["allow_rules"],
                "sitemap_count": len(analysis.get("sitemaps", [])),
                "critical_count": len(analysis.get("critical_issues", analysis["issues"])),
                "warning_count": len(analysis.get("warning_issues", analysis["warnings"])),
                "score": analysis.get("quality_score", 0),
                "grade": analysis.get("quality_grade", "F"),
                "production_ready": analysis.get("production_ready", False)
            },
            "error": None,
            "can_continue": True,
            "raw_content": raw_text,
            "groups_detail": [
                {
                    "user_agents": group.user_agents,
                    "disallow": [{"path": r.path, "line": r.line} for r in group.disallow],
                    "allow": [{"path": r.path, "line": r.line} for r in group.allow],
                }
                for group in result.groups
            ],
        }
    }

    print(f"[ROBOTS] Audit completed: {stats['user_agents']} UAs, {stats['disallow_rules']} disallow rules")
    return response


def check_robots_simple(url: str) -> Dict[str, Any]:
    """Simplified robots.txt analysis (legacy fallback)."""
    import requests
    
    robots_url = url.rstrip('/') + '/robots.txt'
    
    try:
        response = requests.get(robots_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        raw_text = response.text
        
        result = parse_robots(raw_text)
        analysis = build_issues_and_warnings(result)
        
        return {
            "task_type": "robots_check",
            "url": url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "robots_txt_found": response.status_code == 200,
                "status_code": response.status_code,
                "content_length": len(raw_text),
                "lines_count": len(raw_text.splitlines()),
                "user_agents": len(analysis["present_agents"]),
                "disallow_rules": len(result.all_disallow),
                "allow_rules": len(result.all_allow),
                "sitemaps": analysis["sitemaps"],
                "issues": analysis["issues"],
                "critical_issues": analysis.get("critical_issues", analysis["issues"]),
                "warnings": analysis["warnings"],
                "warning_issues": analysis.get("warning_issues", analysis["warnings"]),
                "info_issues": analysis.get("info_issues", []),
                "recommendations": analysis["recommendations"],
                "syntax_errors": analysis["syntax_errors"],
                "hosts": analysis["hosts"],
                "sitemap_checks": analysis.get("sitemap_checks", []),
                "quality_score": analysis.get("quality_score", 0),
                "quality_grade": analysis.get("quality_grade", "F"),
                "production_ready": analysis.get("production_ready", False),
                "top_fixes": analysis.get("top_fixes", []),
                "severity_counts": analysis.get("severity_counts", {"critical": len(analysis["issues"]), "warning": len(analysis["warnings"]), "info": 0}),
                "quick_status": analysis.get("quick_status", "warn"),
                "raw_content": raw_text,
            }
        }
    except Exception as e:
        return {
            "task_type": "robots_check",
            "url": url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "error": str(e),
                "robots_txt_found": False,
                "quality_score": 0,
                "quality_grade": "F",
                "production_ready": False,
                "critical_issues": [str(e)],
                "warning_issues": [],
                "info_issues": [],
                "top_fixes": [],
                "severity_counts": {"critical": 1, "warning": 0, "info": 0}
            }
        }


def check_sitemap_full(url: Union[str, List[str]]) -> Dict[str, Any]:
    """Full sitemap validation with sitemap index traversal and URL export."""
    import xml.etree.ElementTree as ET
    from app.config import settings

    def local_name(tag: str) -> str:
        if not tag:
            return ""
        return tag.split("}", 1)[1] if "}" in tag else tag

    def find_child_text(node: ET.Element, child_name: str) -> str:
        for child in list(node):
            if local_name(child.tag).lower() == child_name.lower():
                return (child.text or "").strip()
        return ""

    def find_children(node: ET.Element, child_name: str) -> List[ET.Element]:
        out: List[ET.Element] = []
        for child in list(node):
            if local_name(child.tag).lower() == child_name.lower():
                out.append(child)
        return out

    def is_http_url(value: str) -> bool:
        try:
            v = str(value or "").strip()
            # Guard against broken concatenated values like "...xmlhttps://...".
            if not v or any(ch in v for ch in [" ", "\n", "\r", "\t"]):
                return False
            if (v.count("http://") + v.count("https://")) > 1:
                return False
            p = urlparse(v)
            return p.scheme in ("http", "https") and bool(p.netloc)
        except Exception:
            return False

    def is_valid_lastmod(value: str) -> bool:
        if not value:
            return True
        date_only = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        dt_utc = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
        dt_tz = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\+|-)\d{2}:\d{2}", value)
        dt_frac_utc = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", value)
        dt_frac_tz = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+(?:\+|-)\d{2}:\d{2}", value)
        return bool(date_only or dt_utc or dt_tz or dt_frac_utc or dt_frac_tz)

    def parse_lastmod_dt(value: str) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            try:
                return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                return None

    def is_valid_hreflang_code(value: str) -> bool:
        v = str(value or "").strip().lower()
        if not v:
            return False
        if v == "x-default":
            return True
        return bool(re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", v))

    def sample_spread(items: List[str], size: int) -> List[str]:
        if size <= 0 or not items:
            return []
        if len(items) <= size:
            return items
        if size == 1:
            return [items[0]]
        step = (len(items) - 1) / float(size - 1)
        picks = sorted({int(round(i * step)) for i in range(size)})
        return [items[idx] for idx in picks if 0 <= idx < len(items)]

    def build_issue(severity: str, code: str, title: str, details: str, action: str, owner: str = "SEO") -> Dict[str, Any]:
        return {
            "severity": severity,
            "code": code,
            "title": title,
            "details": details,
            "action": action,
            "owner": owner,
        }

    max_sitemaps = max(10, min(2000, int(getattr(settings, "SITEMAP_MAX_FILES", 500) or 500)))
    max_export_urls = max(1000, int(getattr(settings, "SITEMAP_MAX_EXPORT_URLS", 100000) or 100000))
    export_chunk_size = 25000
    max_urls_preview_per_sitemap = 2000
    max_file_size = 52428800
    max_urls_per_sitemap = 50000
    stale_days = max(30, min(3650, int(getattr(settings, "SITEMAP_STALE_DAYS", 180) or 180)))
    live_check_sample_size = max(10, min(20, int(getattr(settings, "SITEMAP_LIVE_CHECK_SAMPLE", 15) or 15)))
    live_check_timeout = max(2, min(15, int(getattr(settings, "SITEMAP_LIVE_CHECK_TIMEOUT", 6) or 6)))
    root_urls: List[str]
    if isinstance(url, list):
        root_urls = [str(u).strip() for u in url if str(u).strip()]
    else:
        root_urls = [str(url).strip()] if str(url).strip() else []
    root_urls = list(dict.fromkeys(root_urls))
    primary_root_url = root_urls[0] if root_urls else ""
    queue: List[str] = list(root_urls)
    visited: set = set()
    sitemap_files: List[Dict[str, Any]] = []
    all_urls: List[str] = []
    seen_urls: set = set()
    url_first_seen_in: Dict[str, str] = {}
    duplicate_urls_count = 0
    duplicate_details: List[Dict[str, str]] = []
    duplicate_details_truncated = False
    max_duplicate_details = 500
    invalid_urls_count = 0
    invalid_lastmod_count = 0
    invalid_changefreq_count = 0
    invalid_priority_count = 0
    # Freshness metrics
    lastmod_present_count = 0
    lastmod_missing_count = 0
    lastmod_future_count = 0
    stale_lastmod_count = 0
    uniform_lastmod_files = 0
    # Hreflang metrics
    hreflang_links_count = 0
    hreflang_urls_count = 0
    hreflang_invalid_code_count = 0
    hreflang_invalid_href_count = 0
    hreflang_duplicate_lang_count = 0
    hreflang_has_x_default = False
    # Media extensions metrics
    image_tags_count = 0
    image_missing_loc_count = 0
    video_tags_count = 0
    video_missing_required_count = 0
    news_tags_count = 0
    news_missing_required_count = 0
    # Structure metrics
    repeated_child_refs = 0
    self_child_refs = 0
    max_depth_seen = 0
    warnings: List[str] = []
    errors: List[str] = []
    tool_notes: List[str] = []
    allowed_changefreq = {"always", "hourly", "daily", "weekly", "monthly", "yearly", "never"}
    root_status_code = None
    now_utc = datetime.now(timezone.utc)

    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        while queue and len(visited) < max_sitemaps:
            sitemap_url = queue.pop(0).strip()
            if not sitemap_url or sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            parsed_depth = max(0, str(urlparse(sitemap_url).path or "").count("/") - 1)
            max_depth_seen = max(max_depth_seen, parsed_depth)

            file_report: Dict[str, Any] = {
                "sitemap_url": sitemap_url,
                "ok": False,
                "status_code": None,
                "type": "unknown",
                "compression": "none",
                "size_bytes": 0,
                "urls_count": 0,
                "duplicate_count": 0,
                "duplicate_urls": [],
                "urls_omitted": 0,
                "errors": [],
                "warnings": [],
                "tool_notes": [],
                "urls": [],
            }

            try:
                target_error = _get_public_target_error(sitemap_url)
                if target_error:
                    file_report["errors"].append(target_error)
                    sitemap_files.append(file_report)
                    continue
                response = _safe_fetch_with_redirects_sync(
                    session,
                    sitemap_url,
                    timeout=20,
                )
                file_report["status_code"] = response.status_code
                file_report["compressed_size_bytes"] = len(response.content or b"")
                file_report["size_bytes"] = file_report["compressed_size_bytes"]
                if root_status_code is None:
                    root_status_code = response.status_code

                if response.status_code != 200:
                    file_report["errors"].append(f"HTTP {response.status_code}")
                    sitemap_files.append(file_report)
                    continue

                if file_report["size_bytes"] > max_file_size:
                    file_report["warnings"].append("Размер файла превышает 50 МиБ.")

                try:
                    decoded_content, was_gzip = _decode_sitemap_payload(
                        response.content,
                        getattr(response, "url", sitemap_url),
                        getattr(response, "headers", {}),
                        max_decoded_bytes=max_file_size,
                    )
                    if was_gzip:
                        file_report["compression"] = "gzip"
                    file_report["size_bytes"] = len(decoded_content or b"")
                    if file_report["size_bytes"] > max_file_size:
                        file_report["warnings"].append("Размер файла превышает 50 МиБ.")
                    root = ET.fromstring(decoded_content)
                except (ET.ParseError, ValueError) as parse_error:
                    file_report["errors"].append(f"Ошибка парсинга XML: {parse_error}")
                    sitemap_files.append(file_report)
                    continue

                root_tag = local_name(root.tag).lower()
                file_report["type"] = root_tag

                if root_tag == "sitemapindex":
                    child_count = 0
                    for sm_node in root.iter():
                        if local_name(sm_node.tag).lower() != "sitemap":
                            continue
                        loc = find_child_text(sm_node, "loc")
                        if not loc:
                            file_report["warnings"].append("В sitemap-индексе найден элемент без <loc>.")
                            continue
                        if not is_http_url(loc):
                            file_report["warnings"].append(f"Некорректный URL дочернего sitemap: {loc}")
                            continue
                        if loc == sitemap_url:
                            self_child_refs += 1
                            file_report["warnings"].append(f"Самоссылка в sitemap-индексе: {loc}")
                            continue
                        child_error = _get_public_target_error(loc)
                        if child_error:
                            file_report["warnings"].append(f"Небезопасный URL дочернего sitemap пропущен: {loc}")
                            continue
                        child_count += 1
                        if loc in visited or loc in queue:
                            repeated_child_refs += 1
                            file_report["warnings"].append(f"Дочерний sitemap указан несколько раз: {loc}")
                            continue
                        if (len(visited) + len(queue) < max_sitemaps):
                            queue.append(loc)
                    if child_count == 0:
                        file_report["warnings"].append("Sitemap-индекс не содержит дочерних sitemap.")
                    file_report["ok"] = len(file_report["errors"]) == 0

                elif root_tag == "urlset":
                    file_urls: List[str] = []
                    file_duplicate_urls: List[str] = []
                    file_duplicate_occurrences = 0
                    file_lastmods: List[str] = []
                    file_invalid_lastmod_count = 0
                    file_invalid_changefreq_count = 0
                    file_invalid_priority_count = 0
                    file_future_lastmod_count = 0
                    file_stale_lastmod_count = 0
                    file_invalid_lastmod_examples: List[str] = []
                    file_future_lastmod_examples: List[str] = []
                    file_stale_lastmod_examples: List[str] = []
                    file_lastmod_url_samples: Dict[str, List[str]] = {}
                    for url_node in root.iter():
                        if local_name(url_node.tag).lower() != "url":
                            continue
                        loc = find_child_text(url_node, "loc")
                        if not loc:
                            file_report["warnings"].append("В urlset найден элемент без <loc>.")
                            continue
                        if not is_http_url(loc):
                            invalid_urls_count += 1
                            file_report["warnings"].append(f"Некорректный URL в <loc>: {loc}")
                            continue

                        lastmod = find_child_text(url_node, "lastmod")
                        if lastmod:
                            parsed_lastmod = parse_lastmod_dt(lastmod)
                            if not is_valid_lastmod(lastmod) or parsed_lastmod is None:
                                invalid_lastmod_count += 1
                                file_invalid_lastmod_count += 1
                                if len(file_invalid_lastmod_examples) < 5:
                                    file_invalid_lastmod_examples.append(loc)
                            else:
                                lastmod_present_count += 1
                                lastmod_iso_date = parsed_lastmod.date().isoformat()
                                file_lastmods.append(lastmod_iso_date)
                                bucket = file_lastmod_url_samples.setdefault(lastmod_iso_date, [])
                                if len(bucket) < 3:
                                    bucket.append(loc)
                                if parsed_lastmod > now_utc:
                                    lastmod_future_count += 1
                                    file_future_lastmod_count += 1
                                    if len(file_future_lastmod_examples) < 5:
                                        file_future_lastmod_examples.append(loc)
                                if (now_utc - parsed_lastmod).days > stale_days:
                                    stale_lastmod_count += 1
                                    file_stale_lastmod_count += 1
                                    if len(file_stale_lastmod_examples) < 5:
                                        file_stale_lastmod_examples.append(loc)
                        else:
                            lastmod_missing_count += 1

                        changefreq = find_child_text(url_node, "changefreq").lower()
                        if changefreq and changefreq not in allowed_changefreq:
                            invalid_changefreq_count += 1
                            file_invalid_changefreq_count += 1

                        priority_raw = find_child_text(url_node, "priority")
                        if priority_raw:
                            try:
                                priority_value = float(priority_raw)
                                if priority_value < 0 or priority_value > 1:
                                    invalid_priority_count += 1
                                    file_invalid_priority_count += 1
                            except Exception:
                                invalid_priority_count += 1
                                file_invalid_priority_count += 1

                        # Minimal hreflang validation in sitemap (only when present)
                        local_hreflang_seen = set()
                        local_hreflang_count = 0
                        for child in list(url_node):
                            if local_name(child.tag).lower() != "link":
                                continue
                            rel = str(child.attrib.get("rel", "")).strip().lower()
                            href = str(child.attrib.get("href", "")).strip()
                            hreflang = str(child.attrib.get("hreflang", "")).strip().lower()
                            if rel != "alternate" or not (href or hreflang):
                                continue
                            hreflang_links_count += 1
                            local_hreflang_count += 1
                            if hreflang == "x-default":
                                hreflang_has_x_default = True
                            if not is_valid_hreflang_code(hreflang):
                                hreflang_invalid_code_count += 1
                            if not href or not is_http_url(href):
                                hreflang_invalid_href_count += 1
                            if hreflang in local_hreflang_seen:
                                hreflang_duplicate_lang_count += 1
                            local_hreflang_seen.add(hreflang)
                        if local_hreflang_count > 0:
                            hreflang_urls_count += 1

                        # Media extensions (minimal validation)
                        image_nodes = find_children(url_node, "image")
                        image_tags_count += len(image_nodes)
                        for image_node in image_nodes:
                            image_loc = find_child_text(image_node, "loc")
                            if not image_loc or not is_http_url(image_loc):
                                image_missing_loc_count += 1

                        video_nodes = find_children(url_node, "video")
                        video_tags_count += len(video_nodes)
                        for video_node in video_nodes:
                            has_thumb = bool(find_child_text(video_node, "thumbnail_loc"))
                            has_title = bool(find_child_text(video_node, "title"))
                            has_desc = bool(find_child_text(video_node, "description"))
                            has_content = bool(find_child_text(video_node, "content_loc") or find_child_text(video_node, "player_loc"))
                            if not (has_thumb and has_title and has_desc and has_content):
                                video_missing_required_count += 1

                        news_nodes = find_children(url_node, "news")
                        news_tags_count += len(news_nodes)
                        for news_node in news_nodes:
                            if not find_child_text(news_node, "publication_date") or not find_child_text(news_node, "title"):
                                news_missing_required_count += 1

                        file_urls.append(loc)
                        if loc in seen_urls:
                            duplicate_urls_count += 1
                            file_duplicate_occurrences += 1
                            file_duplicate_urls.append(loc)
                            first_sitemap = url_first_seen_in.get(loc, "")
                            if len(duplicate_details) < max_duplicate_details:
                                duplicate_details.append({
                                    "url": loc,
                                    "first_sitemap": first_sitemap,
                                    "duplicate_sitemap": sitemap_url
                                })
                            else:
                                duplicate_details_truncated = True
                        else:
                            seen_urls.add(loc)
                            url_first_seen_in[loc] = sitemap_url
                            if len(all_urls) < max_export_urls:
                                all_urls.append(loc)

                    file_report["urls_count"] = len(file_urls)
                    file_report["urls"] = file_urls[:max_urls_preview_per_sitemap]
                    file_report["urls_omitted"] = max(0, len(file_urls) - max_urls_preview_per_sitemap)
                    file_report["duplicate_count"] = file_duplicate_occurrences
                    file_report["duplicate_urls"] = dedupe_keep_order(file_duplicate_urls)[:200]
                    if len(file_urls) > max_urls_per_sitemap:
                        file_report["warnings"].append("В одном sitemap-файле более 50 000 URL.")
                    if file_report["urls_omitted"] > 0:
                        file_report["tool_notes"].append(
                            f"Превью URL ограничено для UI/API: скрыто {file_report['urls_omitted']} URL; полный подсчет и валидация выполнены."
                        )
                    if file_invalid_lastmod_count > 0:
                        file_report["warnings"].append(
                            f"Некорректные значения <lastmod>: {file_invalid_lastmod_count}. Примеры: {' | '.join(file_invalid_lastmod_examples[:5])}"
                        )
                    if file_invalid_changefreq_count > 0:
                        file_report["warnings"].append(f"Некорректные значения <changefreq>: {file_invalid_changefreq_count}.")
                    if file_invalid_priority_count > 0:
                        file_report["warnings"].append(f"Некорректные значения <priority>: {file_invalid_priority_count}.")
                    if file_future_lastmod_count > 0:
                        file_report["warnings"].append(
                            f"Будущие значения <lastmod>: {file_future_lastmod_count}. Примеры: {' | '.join(file_future_lastmod_examples[:5])}"
                        )
                    if file_stale_lastmod_count > 0:
                        file_report["warnings"].append(
                            f"Устаревшие значения <lastmod> (> {stale_days} дней): {file_stale_lastmod_count}. Примеры: {' | '.join(file_stale_lastmod_examples[:5])}"
                        )
                    if len(file_lastmods) >= 20:
                        histogram: Dict[str, int] = {}
                        for d in file_lastmods:
                            histogram[d] = histogram.get(d, 0) + 1
                        dominant = max(histogram.values()) if histogram else 0
                        if dominant / max(1, len(file_lastmods)) >= 0.9:
                            uniform_lastmod_files += 1
                            dominant_value = max(histogram, key=histogram.get) if histogram else ""
                            dominant_ratio = round((dominant / max(1, len(file_lastmods))) * 100, 2)
                            dominant_examples = file_lastmod_url_samples.get(dominant_value, [])[:3]
                            file_report["lastmod_uniformity"] = {
                                "dominant_date": dominant_value,
                                "dominant_count": dominant,
                                "total_with_lastmod": len(file_lastmods),
                                "dominant_ratio_pct": dominant_ratio,
                                "sample_urls": dominant_examples,
                            }
                            file_report["warnings"].append(
                                f"Подозрительно однотипные lastmod: {dominant}/{len(file_lastmods)} ({dominant_ratio}%) = {dominant_value}. Примеры: {' | '.join(dominant_examples)}"
                            )
                    file_report["ok"] = len(file_report["errors"]) == 0

                else:
                    file_report["errors"].append(f"Неподдерживаемый корневой XML-тег: {root_tag}")

                sitemap_files.append(file_report)

            except Exception as fetch_error:
                file_report["errors"].append(str(fetch_error))
                sitemap_files.append(file_report)

        if queue:
            tool_notes.append(f"Достигнут лимит обхода sitemap: {max_sitemaps} файлов (осталось в очереди: {len(queue)}).")

        errors.extend([f"{item['sitemap_url']}: {err}" for item in sitemap_files for err in item.get("errors", [])])
        warnings.extend([f"{item['sitemap_url']}: {warn}" for item in sitemap_files for warn in item.get("warnings", [])])

        if duplicate_details_truncated:
            tool_notes.append(f"Список дублей сокращен до {max_duplicate_details} записей.")

        valid_files = sum(1 for item in sitemap_files if item.get("ok"))
        total_urls_discovered = sum(item.get("urls_count", 0) for item in sitemap_files if item.get("type") == "urlset")
        urls_export_truncated = total_urls_discovered > len(all_urls)
        export_parts_count = (len(all_urls) + export_chunk_size - 1) // export_chunk_size if all_urls else 0

        # Lightweight live check (sampled 10..20 URLs only)
        live_indexability_checks: List[Dict[str, Any]] = []
        live_non_indexable_count = 0
        live_check_errors_count = 0
        sampled_urls = random.sample(list(seen_urls), min(live_check_sample_size, len(seen_urls))) if seen_urls else []
        canonical_checked_count = 0
        canonical_missing_count = 0
        canonical_invalid_count = 0
        canonical_non_self_count = 0
        if sampled_urls:
            live_session = requests.Session()
            live_session.headers.update({"User-Agent": "Mozilla/5.0"})
            for sample_url in sampled_urls:
                item = {
                    "url": sample_url,
                    "status_code": None,
                    "indexable": None,
                    "reasons": [],
                    "response_ms": None,
                    "canonical_status": "n/a",
                    "canonical_url": "",
                }
                started = time.time()
                try:
                    live_response = _safe_fetch_with_redirects_sync(
                        live_session,
                        sample_url,
                        timeout=live_check_timeout,
                    )
                    item["status_code"] = live_response.status_code
                    item["response_ms"] = int((time.time() - started) * 1000)
                    reasons: List[str] = []
                    if live_response.status_code >= 400:
                        reasons.append(f"HTTP {live_response.status_code}")
                    x_robots = str(live_response.headers.get("X-Robots-Tag", "") or "")
                    if x_robots and re.search(r"\b(noindex|none)\b", x_robots, flags=re.IGNORECASE):
                        reasons.append(f"X-Robots-Tag: {x_robots}")
                    content_type = str(live_response.headers.get("Content-Type", "") or "").lower()
                    if "html" in content_type:
                        try:
                            body = live_response.text[:200000]
                        except Exception:
                            body = ""
                        if body:
                            m = re.search(r'<meta[^>]+name=["\']robots["\'][^>]*content=["\']([^"\']+)["\']', body, flags=re.IGNORECASE)
                            if m and re.search(r"\b(noindex|none)\b", m.group(1), flags=re.IGNORECASE):
                                reasons.append(f"meta robots: {m.group(1)}")
                            canonical_checked_count += 1
                            c = re.search(r'<link[^>]+rel=["\'][^"\']*\bcanonical\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', body, flags=re.IGNORECASE)
                            if not c:
                                c = re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\'][^"\']*\bcanonical\b[^"\']*["\']', body, flags=re.IGNORECASE)
                            if not c:
                                item["canonical_status"] = "missing"
                                canonical_missing_count += 1
                            else:
                                canonical_raw = str(c.group(1) or "").strip()
                                canonical_abs = urljoin(sample_url, canonical_raw)
                                item["canonical_url"] = canonical_abs
                                if not is_http_url(canonical_abs):
                                    item["canonical_status"] = "invalid"
                                    canonical_invalid_count += 1
                                    reasons.append("canonical: некорректный URL")
                                else:
                                    norm_src = sample_url.rstrip("/")
                                    norm_can = canonical_abs.rstrip("/")
                                    if norm_src == norm_can:
                                        item["canonical_status"] = "self"
                                    else:
                                        item["canonical_status"] = "other"
                                        canonical_non_self_count += 1
                    item["reasons"] = reasons
                    item["indexable"] = (200 <= int(live_response.status_code) < 300) and len(reasons) == 0
                    if item["indexable"] is False:
                        live_non_indexable_count += 1
                except Exception as live_err:
                    item["indexable"] = False
                    item["reasons"] = [str(live_err)]
                    item["response_ms"] = int((time.time() - started) * 1000)
                    live_non_indexable_count += 1
                    live_check_errors_count += 1
                live_indexability_checks.append(item)

        canonical_error_count = canonical_missing_count + canonical_invalid_count

        if canonical_checked_count > 0 and (canonical_error_count + canonical_non_self_count) > 0:
            warnings.append(
                "Проверка canonical на случайной выборке: "
                f"отсутствует={canonical_missing_count}, некорректный={canonical_invalid_count}, не self-canonical={canonical_non_self_count} "
                f"(выборка={canonical_checked_count})."
            )

        recommendations: List[str] = []
        highlights: List[str] = []
        quality_score = 100

        if len(sitemap_files) > 0 and len(errors) == 0 and len(warnings) == 0:
            highlights.append("Структура sitemap валидна, парсинг выполнен без ошибок.")
        if total_urls_discovered > 0:
            highlights.append(f"Обнаружено URL: {total_urls_discovered}. Уникальных URL: {len(seen_urls)}.")
        if duplicate_urls_count == 0 and total_urls_discovered > 0:
            highlights.append("Дубли URL между просканированными sitemap-файлами не обнаружены.")
        if hreflang_links_count > 0:
            highlights.append(f"В sitemap обнаружены hreflang-ссылки: {hreflang_links_count}.")
        if image_tags_count + video_tags_count + news_tags_count > 0:
            highlights.append(f"Обнаружены media-расширения (изображения/видео/новости): {image_tags_count}/{video_tags_count}/{news_tags_count}.")
        if live_indexability_checks:
            highlights.append(f"Проверена live-выборка индексируемости: {len(live_indexability_checks)} URL, неиндексируемых: {live_non_indexable_count}.")

        if invalid_urls_count > 0:
            recommendations.append("Исправьте некорректные <loc> и оставьте только абсолютные HTTP/HTTPS URL.")
            quality_score -= min(25, invalid_urls_count)
        if invalid_lastmod_count > 0:
            recommendations.append("Приведите <lastmod> к формату W3C (YYYY-MM-DD или полный ISO-8601).")
            quality_score -= min(20, invalid_lastmod_count)
        if stale_lastmod_count > 0:
            recommendations.append(
                f"Обновите устаревшие URL (старше {stale_days} дней по <lastmod>) и поддерживайте корректные сигналы обновления."
            )
            quality_score -= min(10, stale_lastmod_count)
        if lastmod_future_count > 0:
            recommendations.append("Исправьте будущие даты в <lastmod>; поисковые системы могут считать такой сигнал недостоверным.")
            quality_score -= min(10, lastmod_future_count)
        if invalid_changefreq_count > 0:
            recommendations.append("Используйте только допустимые значения <changefreq> (always/hourly/daily/weekly/monthly/yearly/never).")
            quality_score -= min(10, invalid_changefreq_count)
        if invalid_priority_count > 0:
            recommendations.append("Используйте значения <priority> только в диапазоне 0.0..1.0.")
            quality_score -= min(10, invalid_priority_count)
        if duplicate_urls_count > 0:
            recommendations.append("Удалите дубли URL между sitemap-файлами.")
            quality_score -= min(20, duplicate_urls_count)
        if self_child_refs > 0 or repeated_child_refs > 0:
            recommendations.append("Исправьте структуру sitemap-индекса (уберите самоссылки и повторяющиеся ссылки на дочерние sitemap).")
            quality_score -= min(10, self_child_refs + repeated_child_refs)
        if queue:
            quality_score -= 10
        if urls_export_truncated:
            tool_notes.append(f"Превью экспорта ограничено до {max_export_urls} URL; для полного списка используйте экспорт частями.")
        if total_urls_discovered > max_urls_per_sitemap:
            recommendations.append("Как минимум один sitemap превышает 50 000 URL; разделите его на несколько файлов.")
            quality_score -= 10
        if any((item.get("size_bytes", 0) or 0) > max_file_size for item in sitemap_files):
            recommendations.append("Как минимум один sitemap-файл превышает 50 МиБ; разделите или сожмите sitemap-файлы.")
            quality_score -= 10
        if hreflang_links_count > 0 and (hreflang_invalid_code_count + hreflang_invalid_href_count + hreflang_duplicate_lang_count) > 0:
            recommendations.append("Исправьте hreflang в sitemap (валидный код, абсолютный href, без дублирования языков в рамках URL).")
            quality_score -= min(10, hreflang_invalid_code_count + hreflang_invalid_href_count + hreflang_duplicate_lang_count)
        if image_tags_count > 0 and image_missing_loc_count > 0:
            recommendations.append("Убедитесь, что каждый <image:image> содержит валидный <image:loc> URL.")
            quality_score -= min(8, image_missing_loc_count)
        if video_tags_count > 0 and video_missing_required_count > 0:
            recommendations.append("Заполните обязательные поля video sitemap (thumbnail_loc, title, description, content_loc/player_loc).")
            quality_score -= min(8, video_missing_required_count)
        if news_tags_count > 0 and news_missing_required_count > 0:
            recommendations.append("Заполните обязательные поля news sitemap (publication_date и title).")
            quality_score -= min(8, news_missing_required_count)
        if live_non_indexable_count > 0:
            recommendations.append("Проверьте неиндексируемые URL из live-выборки (HTTP-ошибки или noindex).")
            quality_score -= min(15, live_non_indexable_count)
        if canonical_error_count > 0:
            recommendations.append("Исправьте отсутствующие и некорректные canonical в выборке URL.")
            quality_score -= min(8, canonical_error_count)
        elif canonical_non_self_count > 0:
            recommendations.append("Проверьте non-self canonical в выборке URL и подтвердите, что он задан намеренно.")

        if not recommendations:
            recommendations.append("Критических проблем sitemap не обнаружено. Поддерживайте текущую структуру и отслеживайте состояние в инструментах вебмастеров.")

        issues: List[Dict[str, Any]] = []
        if invalid_urls_count > 0:
            issues.append(build_issue(
                "critical",
                "invalid_loc_urls",
                "Некорректные URL в <loc>",
                f"Найдено некорректных sitemap URL: {invalid_urls_count}.",
                "Исправьте некорректные <loc> и оставьте только абсолютные HTTP/HTTPS URL.",
                "SEO/Dev",
            ))
        if duplicate_urls_count > 0:
            issues.append(build_issue(
                "warning",
                "duplicate_urls",
                "Дубли URL между sitemap-файлами",
                f"Найдено повторов URL: {duplicate_urls_count}.",
                "Уберите дубли URL во всех sitemap-файлах.",
                "SEO",
            ))
        if self_child_refs > 0 or repeated_child_refs > 0:
            issues.append(build_issue(
                "warning",
                "sitemap_index_structure",
                "Проблемы структуры sitemap-индекса",
                f"Самоссылки: {self_child_refs}, повторные ссылки: {repeated_child_refs}.",
                "Уберите самоссылки и повторные ссылки на дочерние sitemap.",
                "Dev",
            ))
        if invalid_lastmod_count + stale_lastmod_count + lastmod_future_count > 0:
            issues.append(build_issue(
                "warning",
                "lastmod_quality",
                "Проблемы актуальности lastmod",
                f"Некорректных: {invalid_lastmod_count}, устаревших: {stale_lastmod_count}, будущих: {lastmod_future_count}.",
                "Нормализуйте формат lastmod и поддерживайте реалистичные и актуальные даты.",
                "SEO/Content",
            ))
        if hreflang_links_count > 0 and (hreflang_invalid_code_count + hreflang_invalid_href_count + hreflang_duplicate_lang_count) > 0:
            issues.append(build_issue(
                "warning",
                "hreflang_sitemap_issues",
                "Проблемы hreflang в sitemap",
                f"Некорректные коды: {hreflang_invalid_code_count}, некорректные href: {hreflang_invalid_href_count}, дубли языков: {hreflang_duplicate_lang_count}.",
                "Исправьте hreflang и href в alternate-ссылках sitemap.",
                "SEO",
            ))
        if (image_tags_count > 0 and image_missing_loc_count > 0) or (video_tags_count > 0 and video_missing_required_count > 0) or (news_tags_count > 0 and news_missing_required_count > 0):
            issues.append(build_issue(
                "warning",
                "media_extension_issues",
                "Проблемы расширений media/news sitemap",
                f"image: без loc={image_missing_loc_count}, video: без обязательных полей={video_missing_required_count}, news: без обязательных полей={news_missing_required_count}.",
                "Заполните обязательные поля в расширениях image/video/news sitemap.",
                "SEO/Dev",
            ))
        if live_non_indexable_count > 0:
            issues.append(build_issue(
                "critical",
                "live_non_indexable_sample",
                "В live-выборке есть неиндексируемые URL",
                f"{live_non_indexable_count} из {len(live_indexability_checks)} URL в выборке выглядят неиндексируемыми.",
                "Исправьте noindex/HTTP-проблемы на страницах из выборки и запустите проверку повторно.",
                "SEO/Dev",
            ))
        if canonical_error_count > 0:
            issues.append(build_issue(
                "warning",
                "canonical_sample_issues",
                "Проблемы canonical в случайной выборке",
                f"Выборка={canonical_checked_count}, отсутствует={canonical_missing_count}, некорректный={canonical_invalid_count}, non-self={canonical_non_self_count}.",
                "Исправьте отсутствующие и некорректные canonical; non-self canonical проверьте отдельно на предмет осознанной настройки.",
                "SEO/Dev",
            ))

        severity_counts = {
            "critical": sum(1 for it in issues if it.get("severity") == "critical"),
            "warning": sum(1 for it in issues if it.get("severity") == "warning"),
            "info": sum(1 for it in issues if it.get("severity") == "info"),
        }
        severity_weight = {"critical": 0, "warning": 1, "info": 2}
        issues_sorted = sorted(issues, key=lambda x: severity_weight.get(str(x.get("severity")), 9))
        top_fixes = dedupe_keep_order([it.get("action", "") for it in issues_sorted if it.get("action")])[:10]
        action_plan: List[Dict[str, Any]] = []
        for issue in issues_sorted[:12]:
            sev = str(issue.get("severity", "warning"))
            action_plan.append({
                "priority": "P0" if sev == "critical" else ("P1" if sev == "warning" else "P2"),
                "owner": issue.get("owner", "SEO"),
                "issue": issue.get("title", ""),
                "action": issue.get("action", ""),
                "sla": "24h" if sev == "critical" else ("3d" if sev == "warning" else "7d"),
            })
        if not action_plan and recommendations:
            action_plan.append({
                "priority": "P2",
                "owner": "SEO",
                "issue": "Критические блокеры отсутствуют",
                "action": recommendations[0],
                "sla": "7d",
            })

        quality_score = max(0, min(100, quality_score))
        if quality_score >= 90:
            quality_grade = "A"
        elif quality_score >= 80:
            quality_grade = "B"
        elif quality_score >= 70:
            quality_grade = "C"
        elif quality_score >= 60:
            quality_grade = "D"
        else:
            quality_grade = "F"

        return {
            "task_type": "sitemap_validate",
            "url": primary_root_url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "root_sitemaps": root_urls,
                "valid": len(errors) == 0 and len(sitemap_files) > 0,
                "status_code": root_status_code,
                "urls_count": total_urls_discovered,
                "unique_urls_count": len(seen_urls),
                "duplicate_urls_count": duplicate_urls_count,
                "duplicate_details": duplicate_details,
                "duplicate_details_truncated": duplicate_details_truncated,
                "sitemaps_scanned": len(sitemap_files),
                "sitemaps_valid": valid_files,
                "errors": dedupe_keep_order(errors),
                "warnings": dedupe_keep_order(warnings),
                "tool_notes": dedupe_keep_order(tool_notes),
                "recommendations": recommendations,
                "highlights": dedupe_keep_order(highlights),
                "quality_score": quality_score,
                "quality_grade": quality_grade,
                "sitemap_files": sitemap_files,
                "export_urls": all_urls,
                "urls_export_truncated": urls_export_truncated,
                "max_export_urls": max_export_urls,
                "export_chunk_size": export_chunk_size,
                "export_parts_count": export_parts_count,
                "scan_limit_files": max_sitemaps,
                "scan_limit_reached": bool(queue),
                "scan_queue_remaining": len(queue),
                "invalid_lastmod_count": invalid_lastmod_count,
                "invalid_changefreq_count": invalid_changefreq_count,
                "invalid_priority_count": invalid_priority_count,
                "invalid_urls_count": invalid_urls_count,
                "size": sum(item.get("size_bytes", 0) for item in sitemap_files),
                "max_depth_seen": max_depth_seen,
                "self_child_refs": self_child_refs,
                "repeated_child_refs": repeated_child_refs,
                "freshness": {
                    "lastmod_present_count": lastmod_present_count,
                    "lastmod_missing_count": lastmod_missing_count,
                    "lastmod_future_count": lastmod_future_count,
                    "stale_lastmod_count": stale_lastmod_count,
                    "uniform_lastmod_files": uniform_lastmod_files,
                    "stale_threshold_days": stale_days,
                },
                "hreflang": {
                    "detected": hreflang_links_count > 0,
                    "links_count": hreflang_links_count,
                    "urls_count": hreflang_urls_count,
                    "invalid_code_count": hreflang_invalid_code_count,
                    "invalid_href_count": hreflang_invalid_href_count,
                    "duplicate_lang_count": hreflang_duplicate_lang_count,
                    "has_x_default": hreflang_has_x_default,
                },
                "media_extensions": {
                    "image_tags_count": image_tags_count,
                    "image_missing_loc_count": image_missing_loc_count,
                    "video_tags_count": video_tags_count,
                    "video_missing_required_count": video_missing_required_count,
                    "news_tags_count": news_tags_count,
                    "news_missing_required_count": news_missing_required_count,
                },
                "live_indexability_checks": live_indexability_checks,
                "live_check_sample_size": len(live_indexability_checks),
                "live_non_indexable_count": live_non_indexable_count,
                "live_check_errors_count": live_check_errors_count,
                "canonical_sample": {
                    "sample_size": canonical_checked_count,
                    "missing_count": canonical_missing_count,
                    "invalid_count": canonical_invalid_count,
                    "non_self_count": canonical_non_self_count,
                },
                "issues": issues_sorted,
                "severity_counts": severity_counts,
                "top_fixes": top_fixes,
                "action_plan": action_plan,
            }
        }
    except Exception as e:
        return {
            "task_type": "sitemap_validate",
            "url": primary_root_url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "valid": False,
                "error": str(e),
                "urls_count": 0,
                "export_urls": [],
                "sitemap_files": [],
            }
        }



async def check_sitemap_full_async(url: Union[str, List[str]], use_proxy: bool = False) -> Dict[str, Any]:
    """Full sitemap validation with sitemap index traversal and URL export."""
    import xml.etree.ElementTree as ET
    from app.config import settings

    def local_name(tag: str) -> str:
        if not tag:
            return ""
        return tag.split("}", 1)[1] if "}" in tag else tag

    def find_child_text(node: ET.Element, child_name: str) -> str:
        for child in list(node):
            if local_name(child.tag).lower() == child_name.lower():
                return (child.text or "").strip()
        return ""

    def find_children(node: ET.Element, child_name: str) -> List[ET.Element]:
        out: List[ET.Element] = []
        for child in list(node):
            if local_name(child.tag).lower() == child_name.lower():
                out.append(child)
        return out

    def is_http_url(value: str) -> bool:
        try:
            v = str(value or "").strip()
            # Guard against broken concatenated values like "...xmlhttps://...".
            if not v or any(ch in v for ch in [" ", "\n", "\r", "\t"]):
                return False
            if (v.count("http://") + v.count("https://")) > 1:
                return False
            p = urlparse(v)
            return p.scheme in ("http", "https") and bool(p.netloc)
        except Exception:
            return False

    def is_valid_lastmod(value: str) -> bool:
        if not value:
            return True
        date_only = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        dt_utc = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
        dt_tz = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\+|-)\d{2}:\d{2}", value)
        dt_frac_utc = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", value)
        dt_frac_tz = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+(?:\+|-)\d{2}:\d{2}", value)
        return bool(date_only or dt_utc or dt_tz or dt_frac_utc or dt_frac_tz)

    def parse_lastmod_dt(value: str) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            try:
                return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                return None

    def is_valid_hreflang_code(value: str) -> bool:
        v = str(value or "").strip().lower()
        if not v:
            return False
        if v == "x-default":
            return True
        return bool(re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", v))

    def sample_spread(items: List[str], size: int) -> List[str]:
        if size <= 0 or not items:
            return []
        if len(items) <= size:
            return items
        if size == 1:
            return [items[0]]
        step = (len(items) - 1) / float(size - 1)
        picks = sorted({int(round(i * step)) for i in range(size)})
        return [items[idx] for idx in picks if 0 <= idx < len(items)]

    def build_issue(severity: str, code: str, title: str, details: str, action: str, owner: str = "SEO") -> Dict[str, Any]:
        return {
            "severity": severity,
            "code": code,
            "title": title,
            "details": details,
            "action": action,
            "owner": owner,
        }

    max_sitemaps = max(10, min(2000, int(getattr(settings, "SITEMAP_MAX_FILES", 500) or 500)))
    max_export_urls = max(1000, int(getattr(settings, "SITEMAP_MAX_EXPORT_URLS", 100000) or 100000))
    export_chunk_size = 25000
    max_urls_preview_per_sitemap = 2000
    max_file_size = 52428800
    max_urls_per_sitemap = 50000
    stale_days = max(30, min(3650, int(getattr(settings, "SITEMAP_STALE_DAYS", 180) or 180)))
    live_check_sample_size = max(10, min(20, int(getattr(settings, "SITEMAP_LIVE_CHECK_SAMPLE", 15) or 15)))
    live_check_timeout = max(2, min(15, int(getattr(settings, "SITEMAP_LIVE_CHECK_TIMEOUT", 6) or 6)))
    root_urls: List[str]
    if isinstance(url, list):
        root_urls = [str(u).strip() for u in url if str(u).strip()]
    else:
        root_urls = [str(url).strip()] if str(url).strip() else []
    root_urls = list(dict.fromkeys(root_urls))
    primary_root_url = root_urls[0] if root_urls else ""
    queue: List[str] = list(root_urls)
    visited: set = set()
    sitemap_files: List[Dict[str, Any]] = []
    all_urls: List[str] = []
    seen_urls: set = set()
    url_first_seen_in: Dict[str, str] = {}
    duplicate_urls_count = 0
    duplicate_details: List[Dict[str, str]] = []
    duplicate_details_truncated = False
    max_duplicate_details = 500
    invalid_urls_count = 0
    invalid_lastmod_count = 0
    invalid_changefreq_count = 0
    invalid_priority_count = 0
    # Freshness metrics
    lastmod_present_count = 0
    lastmod_missing_count = 0
    lastmod_future_count = 0
    stale_lastmod_count = 0
    uniform_lastmod_files = 0
    # Hreflang metrics
    hreflang_links_count = 0
    hreflang_urls_count = 0
    hreflang_invalid_code_count = 0
    hreflang_invalid_href_count = 0
    hreflang_duplicate_lang_count = 0
    hreflang_has_x_default = False
    # Media extensions metrics
    image_tags_count = 0
    image_missing_loc_count = 0
    video_tags_count = 0
    video_missing_required_count = 0
    news_tags_count = 0
    news_missing_required_count = 0
    # Structure metrics
    repeated_child_refs = 0
    self_child_refs = 0
    max_depth_seen = 0
    warnings: List[str] = []
    errors: List[str] = []
    tool_notes: List[str] = []
    allowed_changefreq = {"always", "hourly", "daily", "weekly", "monthly", "yearly", "never"}
    root_status_code = None
    now_utc = datetime.now(timezone.utc)

    session = _AsyncSessionShim({"User-Agent": "Mozilla/5.0"}, use_proxy=use_proxy)
    await session.open()
    try:

        while queue and len(visited) < max_sitemaps:
            sitemap_url = queue.pop(0).strip()
            if not sitemap_url or sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            parsed_depth = max(0, str(urlparse(sitemap_url).path or "").count("/") - 1)
            max_depth_seen = max(max_depth_seen, parsed_depth)

            file_report: Dict[str, Any] = {
                "sitemap_url": sitemap_url,
                "ok": False,
                "status_code": None,
                "type": "unknown",
                "compression": "none",
                "size_bytes": 0,
                "urls_count": 0,
                "duplicate_count": 0,
                "duplicate_urls": [],
                "urls_omitted": 0,
                "errors": [],
                "warnings": [],
                "tool_notes": [],
                "urls": [],
            }

            try:
                target_error = _get_public_target_error(sitemap_url)
                if target_error:
                    file_report["errors"].append(target_error)
                    sitemap_files.append(file_report)
                    continue
                response = await _safe_fetch_with_redirects_async(
                    session,
                    sitemap_url,
                    timeout=20,
                    read_text=False,
                )
                file_report["status_code"] = response.status_code
                file_report["compressed_size_bytes"] = len(response.content or b"")
                file_report["size_bytes"] = file_report["compressed_size_bytes"]
                if root_status_code is None:
                    root_status_code = response.status_code

                if response.status_code != 200:
                    file_report["errors"].append(f"HTTP {response.status_code}")
                    sitemap_files.append(file_report)
                    continue

                if file_report["size_bytes"] > max_file_size:
                    file_report["warnings"].append("Размер файла превышает 50 МиБ.")

                try:
                    decoded_content, was_gzip = _decode_sitemap_payload(
                        response.content,
                        getattr(response, "url", sitemap_url),
                        getattr(response, "headers", {}),
                        max_decoded_bytes=max_file_size,
                    )
                    if was_gzip:
                        file_report["compression"] = "gzip"
                    file_report["size_bytes"] = len(decoded_content or b"")
                    if file_report["size_bytes"] > max_file_size:
                        file_report["warnings"].append("Размер файла превышает 50 МиБ.")
                    root = ET.fromstring(decoded_content)
                except (ET.ParseError, ValueError) as parse_error:
                    file_report["errors"].append(f"Ошибка парсинга XML: {parse_error}")
                    sitemap_files.append(file_report)
                    continue

                root_tag = local_name(root.tag).lower()
                file_report["type"] = root_tag

                if root_tag == "sitemapindex":
                    child_count = 0
                    for sm_node in root.iter():
                        if local_name(sm_node.tag).lower() != "sitemap":
                            continue
                        loc = find_child_text(sm_node, "loc")
                        if not loc:
                            file_report["warnings"].append("В sitemap-индексе найден элемент без <loc>.")
                            continue
                        if not is_http_url(loc):
                            file_report["warnings"].append(f"Некорректный URL дочернего sitemap: {loc}")
                            continue
                        if loc == sitemap_url:
                            self_child_refs += 1
                            file_report["warnings"].append(f"Самоссылка в sitemap-индексе: {loc}")
                            continue
                        child_error = _get_public_target_error(loc)
                        if child_error:
                            file_report["warnings"].append(f"Небезопасный URL дочернего sitemap пропущен: {loc}")
                            continue
                        child_count += 1
                        if loc in visited or loc in queue:
                            repeated_child_refs += 1
                            file_report["warnings"].append(f"Дочерний sitemap указан несколько раз: {loc}")
                            continue
                        if (len(visited) + len(queue) < max_sitemaps):
                            queue.append(loc)
                    if child_count == 0:
                        file_report["warnings"].append("Sitemap-индекс не содержит дочерних sitemap.")
                    file_report["ok"] = len(file_report["errors"]) == 0

                elif root_tag == "urlset":
                    file_urls: List[str] = []
                    file_duplicate_urls: List[str] = []
                    file_duplicate_occurrences = 0
                    file_lastmods: List[str] = []
                    file_invalid_lastmod_count = 0
                    file_invalid_changefreq_count = 0
                    file_invalid_priority_count = 0
                    file_future_lastmod_count = 0
                    file_stale_lastmod_count = 0
                    file_invalid_lastmod_examples: List[str] = []
                    file_future_lastmod_examples: List[str] = []
                    file_stale_lastmod_examples: List[str] = []
                    file_lastmod_url_samples: Dict[str, List[str]] = {}
                    for url_node in root.iter():
                        if local_name(url_node.tag).lower() != "url":
                            continue
                        loc = find_child_text(url_node, "loc")
                        if not loc:
                            file_report["warnings"].append("В urlset найден элемент без <loc>.")
                            continue
                        if not is_http_url(loc):
                            invalid_urls_count += 1
                            file_report["warnings"].append(f"Некорректный URL в <loc>: {loc}")
                            continue

                        lastmod = find_child_text(url_node, "lastmod")
                        if lastmod:
                            parsed_lastmod = parse_lastmod_dt(lastmod)
                            if not is_valid_lastmod(lastmod) or parsed_lastmod is None:
                                invalid_lastmod_count += 1
                                file_invalid_lastmod_count += 1
                                if len(file_invalid_lastmod_examples) < 5:
                                    file_invalid_lastmod_examples.append(loc)
                            else:
                                lastmod_present_count += 1
                                lastmod_iso_date = parsed_lastmod.date().isoformat()
                                file_lastmods.append(lastmod_iso_date)
                                bucket = file_lastmod_url_samples.setdefault(lastmod_iso_date, [])
                                if len(bucket) < 3:
                                    bucket.append(loc)
                                if parsed_lastmod > now_utc:
                                    lastmod_future_count += 1
                                    file_future_lastmod_count += 1
                                    if len(file_future_lastmod_examples) < 5:
                                        file_future_lastmod_examples.append(loc)
                                if (now_utc - parsed_lastmod).days > stale_days:
                                    stale_lastmod_count += 1
                                    file_stale_lastmod_count += 1
                                    if len(file_stale_lastmod_examples) < 5:
                                        file_stale_lastmod_examples.append(loc)
                        else:
                            lastmod_missing_count += 1

                        changefreq = find_child_text(url_node, "changefreq").lower()
                        if changefreq and changefreq not in allowed_changefreq:
                            invalid_changefreq_count += 1
                            file_invalid_changefreq_count += 1

                        priority_raw = find_child_text(url_node, "priority")
                        if priority_raw:
                            try:
                                priority_value = float(priority_raw)
                                if priority_value < 0 or priority_value > 1:
                                    invalid_priority_count += 1
                                    file_invalid_priority_count += 1
                            except Exception:
                                invalid_priority_count += 1
                                file_invalid_priority_count += 1

                        # Minimal hreflang validation in sitemap (only when present)
                        local_hreflang_seen = set()
                        local_hreflang_count = 0
                        for child in list(url_node):
                            if local_name(child.tag).lower() != "link":
                                continue
                            rel = str(child.attrib.get("rel", "")).strip().lower()
                            href = str(child.attrib.get("href", "")).strip()
                            hreflang = str(child.attrib.get("hreflang", "")).strip().lower()
                            if rel != "alternate" or not (href or hreflang):
                                continue
                            hreflang_links_count += 1
                            local_hreflang_count += 1
                            if hreflang == "x-default":
                                hreflang_has_x_default = True
                            if not is_valid_hreflang_code(hreflang):
                                hreflang_invalid_code_count += 1
                            if not href or not is_http_url(href):
                                hreflang_invalid_href_count += 1
                            if hreflang in local_hreflang_seen:
                                hreflang_duplicate_lang_count += 1
                            local_hreflang_seen.add(hreflang)
                        if local_hreflang_count > 0:
                            hreflang_urls_count += 1

                        # Media extensions (minimal validation)
                        image_nodes = find_children(url_node, "image")
                        image_tags_count += len(image_nodes)
                        for image_node in image_nodes:
                            image_loc = find_child_text(image_node, "loc")
                            if not image_loc or not is_http_url(image_loc):
                                image_missing_loc_count += 1

                        video_nodes = find_children(url_node, "video")
                        video_tags_count += len(video_nodes)
                        for video_node in video_nodes:
                            has_thumb = bool(find_child_text(video_node, "thumbnail_loc"))
                            has_title = bool(find_child_text(video_node, "title"))
                            has_desc = bool(find_child_text(video_node, "description"))
                            has_content = bool(find_child_text(video_node, "content_loc") or find_child_text(video_node, "player_loc"))
                            if not (has_thumb and has_title and has_desc and has_content):
                                video_missing_required_count += 1

                        news_nodes = find_children(url_node, "news")
                        news_tags_count += len(news_nodes)
                        for news_node in news_nodes:
                            if not find_child_text(news_node, "publication_date") or not find_child_text(news_node, "title"):
                                news_missing_required_count += 1

                        file_urls.append(loc)
                        if loc in seen_urls:
                            duplicate_urls_count += 1
                            file_duplicate_occurrences += 1
                            file_duplicate_urls.append(loc)
                            first_sitemap = url_first_seen_in.get(loc, "")
                            if len(duplicate_details) < max_duplicate_details:
                                duplicate_details.append({
                                    "url": loc,
                                    "first_sitemap": first_sitemap,
                                    "duplicate_sitemap": sitemap_url
                                })
                            else:
                                duplicate_details_truncated = True
                        else:
                            seen_urls.add(loc)
                            url_first_seen_in[loc] = sitemap_url
                            if len(all_urls) < max_export_urls:
                                all_urls.append(loc)

                    file_report["urls_count"] = len(file_urls)
                    file_report["urls"] = file_urls[:max_urls_preview_per_sitemap]
                    file_report["urls_omitted"] = max(0, len(file_urls) - max_urls_preview_per_sitemap)
                    file_report["duplicate_count"] = file_duplicate_occurrences
                    file_report["duplicate_urls"] = dedupe_keep_order(file_duplicate_urls)[:200]
                    if len(file_urls) > max_urls_per_sitemap:
                        file_report["warnings"].append("В одном sitemap-файле более 50 000 URL.")
                    if file_report["urls_omitted"] > 0:
                        file_report["tool_notes"].append(
                            f"Превью URL ограничено для UI/API: скрыто {file_report['urls_omitted']} URL; полный подсчет и валидация выполнены."
                        )
                    if file_invalid_lastmod_count > 0:
                        file_report["warnings"].append(
                            f"Некорректные значения <lastmod>: {file_invalid_lastmod_count}. Примеры: {' | '.join(file_invalid_lastmod_examples[:5])}"
                        )
                    if file_invalid_changefreq_count > 0:
                        file_report["warnings"].append(f"Некорректные значения <changefreq>: {file_invalid_changefreq_count}.")
                    if file_invalid_priority_count > 0:
                        file_report["warnings"].append(f"Некорректные значения <priority>: {file_invalid_priority_count}.")
                    if file_future_lastmod_count > 0:
                        file_report["warnings"].append(
                            f"Будущие значения <lastmod>: {file_future_lastmod_count}. Примеры: {' | '.join(file_future_lastmod_examples[:5])}"
                        )
                    if file_stale_lastmod_count > 0:
                        file_report["warnings"].append(
                            f"Устаревшие значения <lastmod> (> {stale_days} дней): {file_stale_lastmod_count}. Примеры: {' | '.join(file_stale_lastmod_examples[:5])}"
                        )
                    if len(file_lastmods) >= 20:
                        histogram: Dict[str, int] = {}
                        for d in file_lastmods:
                            histogram[d] = histogram.get(d, 0) + 1
                        dominant = max(histogram.values()) if histogram else 0
                        if dominant / max(1, len(file_lastmods)) >= 0.9:
                            uniform_lastmod_files += 1
                            dominant_value = max(histogram, key=histogram.get) if histogram else ""
                            dominant_ratio = round((dominant / max(1, len(file_lastmods))) * 100, 2)
                            dominant_examples = file_lastmod_url_samples.get(dominant_value, [])[:3]
                            file_report["lastmod_uniformity"] = {
                                "dominant_date": dominant_value,
                                "dominant_count": dominant,
                                "total_with_lastmod": len(file_lastmods),
                                "dominant_ratio_pct": dominant_ratio,
                                "sample_urls": dominant_examples,
                            }
                            file_report["warnings"].append(
                                f"Подозрительно однотипные lastmod: {dominant}/{len(file_lastmods)} ({dominant_ratio}%) = {dominant_value}. Примеры: {' | '.join(dominant_examples)}"
                            )
                    file_report["ok"] = len(file_report["errors"]) == 0

                else:
                    file_report["errors"].append(f"Неподдерживаемый корневой XML-тег: {root_tag}")

                sitemap_files.append(file_report)

            except Exception as fetch_error:
                file_report["errors"].append(str(fetch_error))
                sitemap_files.append(file_report)

        if queue:
            tool_notes.append(f"Достигнут лимит обхода sitemap: {max_sitemaps} файлов (осталось в очереди: {len(queue)}).")

        errors.extend([f"{item['sitemap_url']}: {err}" for item in sitemap_files for err in item.get("errors", [])])
        warnings.extend([f"{item['sitemap_url']}: {warn}" for item in sitemap_files for warn in item.get("warnings", [])])

        if duplicate_details_truncated:
            tool_notes.append(f"Список дублей сокращен до {max_duplicate_details} записей.")

        valid_files = sum(1 for item in sitemap_files if item.get("ok"))
        total_urls_discovered = sum(item.get("urls_count", 0) for item in sitemap_files if item.get("type") == "urlset")
        urls_export_truncated = total_urls_discovered > len(all_urls)
        export_parts_count = (len(all_urls) + export_chunk_size - 1) // export_chunk_size if all_urls else 0

        # Lightweight live check (sampled 10..20 URLs only)
        live_indexability_checks: List[Dict[str, Any]] = []
        live_non_indexable_count = 0
        live_check_errors_count = 0
        sampled_urls = random.sample(list(seen_urls), min(live_check_sample_size, len(seen_urls))) if seen_urls else []
        canonical_checked_count = 0
        canonical_missing_count = 0
        canonical_invalid_count = 0
        canonical_non_self_count = 0
        if sampled_urls:
            live_session = session
            for sample_url in sampled_urls:
                item = {
                    "url": sample_url,
                    "status_code": None,
                    "indexable": None,
                    "reasons": [],
                    "response_ms": None,
                    "canonical_status": "n/a",
                    "canonical_url": "",
                }
                started = time.time()
                try:
                    live_response = await _safe_fetch_with_redirects_async(
                        live_session,
                        sample_url,
                        timeout=live_check_timeout,
                        read_text=True,
                        text_limit=200000,
                    )
                    item["status_code"] = live_response.status_code
                    item["response_ms"] = int((time.time() - started) * 1000)
                    reasons: List[str] = []
                    if live_response.status_code >= 400:
                        reasons.append(f"HTTP {live_response.status_code}")
                    x_robots = str(live_response.headers.get("X-Robots-Tag", "") or "")
                    if x_robots and re.search(r"\b(noindex|none)\b", x_robots, flags=re.IGNORECASE):
                        reasons.append(f"X-Robots-Tag: {x_robots}")
                    content_type = str(live_response.headers.get("Content-Type", "") or "").lower()
                    if "html" in content_type:
                        try:
                            body = live_response.text[:200000]
                        except Exception:
                            body = ""
                        if body:
                            m = re.search(r'<meta[^>]+name=["\']robots["\'][^>]*content=["\']([^"\']+)["\']', body, flags=re.IGNORECASE)
                            if m and re.search(r"\b(noindex|none)\b", m.group(1), flags=re.IGNORECASE):
                                reasons.append(f"meta robots: {m.group(1)}")
                            canonical_checked_count += 1
                            c = re.search(r'<link[^>]+rel=["\'][^"\']*\bcanonical\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']', body, flags=re.IGNORECASE)
                            if not c:
                                c = re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\'][^"\']*\bcanonical\b[^"\']*["\']', body, flags=re.IGNORECASE)
                            if not c:
                                item["canonical_status"] = "missing"
                                canonical_missing_count += 1
                            else:
                                canonical_raw = str(c.group(1) or "").strip()
                                canonical_abs = urljoin(sample_url, canonical_raw)
                                item["canonical_url"] = canonical_abs
                                if not is_http_url(canonical_abs):
                                    item["canonical_status"] = "invalid"
                                    canonical_invalid_count += 1
                                    reasons.append("canonical: некорректный URL")
                                else:
                                    norm_src = sample_url.rstrip("/")
                                    norm_can = canonical_abs.rstrip("/")
                                    if norm_src == norm_can:
                                        item["canonical_status"] = "self"
                                    else:
                                        item["canonical_status"] = "other"
                                        canonical_non_self_count += 1
                    item["reasons"] = reasons
                    item["indexable"] = (200 <= int(live_response.status_code) < 300) and len(reasons) == 0
                    if item["indexable"] is False:
                        live_non_indexable_count += 1
                except Exception as live_err:
                    item["indexable"] = False
                    item["reasons"] = [str(live_err)]
                    item["response_ms"] = int((time.time() - started) * 1000)
                    live_non_indexable_count += 1
                    live_check_errors_count += 1
                live_indexability_checks.append(item)

        canonical_error_count = canonical_missing_count + canonical_invalid_count

        if canonical_checked_count > 0 and (canonical_error_count + canonical_non_self_count) > 0:
            warnings.append(
                "Проверка canonical на случайной выборке: "
                f"отсутствует={canonical_missing_count}, некорректный={canonical_invalid_count}, не self-canonical={canonical_non_self_count} "
                f"(выборка={canonical_checked_count})."
            )

        recommendations: List[str] = []
        highlights: List[str] = []
        quality_score = 100

        if len(sitemap_files) > 0 and len(errors) == 0 and len(warnings) == 0:
            highlights.append("Структура sitemap валидна, парсинг выполнен без ошибок.")
        if total_urls_discovered > 0:
            highlights.append(f"Обнаружено URL: {total_urls_discovered}. Уникальных URL: {len(seen_urls)}.")
        if duplicate_urls_count == 0 and total_urls_discovered > 0:
            highlights.append("Дубли URL между просканированными sitemap-файлами не обнаружены.")
        if hreflang_links_count > 0:
            highlights.append(f"В sitemap обнаружены hreflang-ссылки: {hreflang_links_count}.")
        if image_tags_count + video_tags_count + news_tags_count > 0:
            highlights.append(f"Обнаружены media-расширения (изображения/видео/новости): {image_tags_count}/{video_tags_count}/{news_tags_count}.")
        if live_indexability_checks:
            highlights.append(f"Проверена live-выборка индексируемости: {len(live_indexability_checks)} URL, неиндексируемых: {live_non_indexable_count}.")

        if invalid_urls_count > 0:
            recommendations.append("Исправьте некорректные <loc> и оставьте только абсолютные HTTP/HTTPS URL.")
            quality_score -= min(25, invalid_urls_count)
        if invalid_lastmod_count > 0:
            recommendations.append("Приведите <lastmod> к формату W3C (YYYY-MM-DD или полный ISO-8601).")
            quality_score -= min(20, invalid_lastmod_count)
        if stale_lastmod_count > 0:
            recommendations.append(
                f"Обновите устаревшие URL (старше {stale_days} дней по <lastmod>) и поддерживайте корректные сигналы обновления."
            )
            quality_score -= min(10, stale_lastmod_count)
        if lastmod_future_count > 0:
            recommendations.append("Исправьте будущие даты в <lastmod>; поисковые системы могут считать такой сигнал недостоверным.")
            quality_score -= min(10, lastmod_future_count)
        if invalid_changefreq_count > 0:
            recommendations.append("Используйте только допустимые значения <changefreq> (always/hourly/daily/weekly/monthly/yearly/never).")
            quality_score -= min(10, invalid_changefreq_count)
        if invalid_priority_count > 0:
            recommendations.append("Используйте значения <priority> только в диапазоне 0.0..1.0.")
            quality_score -= min(10, invalid_priority_count)
        if duplicate_urls_count > 0:
            recommendations.append("Удалите дубли URL между sitemap-файлами.")
            quality_score -= min(20, duplicate_urls_count)
        if self_child_refs > 0 or repeated_child_refs > 0:
            recommendations.append("Исправьте структуру sitemap-индекса (уберите самоссылки и повторяющиеся ссылки на дочерние sitemap).")
            quality_score -= min(10, self_child_refs + repeated_child_refs)
        if queue:
            quality_score -= 10
        if urls_export_truncated:
            tool_notes.append(f"Превью экспорта ограничено до {max_export_urls} URL; для полного списка используйте экспорт частями.")
        if total_urls_discovered > max_urls_per_sitemap:
            recommendations.append("Как минимум один sitemap превышает 50 000 URL; разделите его на несколько файлов.")
            quality_score -= 10
        if any((item.get("size_bytes", 0) or 0) > max_file_size for item in sitemap_files):
            recommendations.append("Как минимум один sitemap-файл превышает 50 МиБ; разделите или сожмите sitemap-файлы.")
            quality_score -= 10
        if hreflang_links_count > 0 and (hreflang_invalid_code_count + hreflang_invalid_href_count + hreflang_duplicate_lang_count) > 0:
            recommendations.append("Исправьте hreflang в sitemap (валидный код, абсолютный href, без дублирования языков в рамках URL).")
            quality_score -= min(10, hreflang_invalid_code_count + hreflang_invalid_href_count + hreflang_duplicate_lang_count)
        if image_tags_count > 0 and image_missing_loc_count > 0:
            recommendations.append("Убедитесь, что каждый <image:image> содержит валидный <image:loc> URL.")
            quality_score -= min(8, image_missing_loc_count)
        if video_tags_count > 0 and video_missing_required_count > 0:
            recommendations.append("Заполните обязательные поля video sitemap (thumbnail_loc, title, description, content_loc/player_loc).")
            quality_score -= min(8, video_missing_required_count)
        if news_tags_count > 0 and news_missing_required_count > 0:
            recommendations.append("Заполните обязательные поля news sitemap (publication_date и title).")
            quality_score -= min(8, news_missing_required_count)
        if live_non_indexable_count > 0:
            recommendations.append("Проверьте неиндексируемые URL из live-выборки (HTTP-ошибки или noindex).")
            quality_score -= min(15, live_non_indexable_count)
        if canonical_error_count > 0:
            recommendations.append("Исправьте отсутствующие и некорректные canonical в выборке URL.")
            quality_score -= min(8, canonical_error_count)
        elif canonical_non_self_count > 0:
            recommendations.append("Проверьте non-self canonical в выборке URL и подтвердите, что он задан намеренно.")

        if not recommendations:
            recommendations.append("Критических проблем sitemap не обнаружено. Поддерживайте текущую структуру и отслеживайте состояние в инструментах вебмастеров.")

        issues: List[Dict[str, Any]] = []
        if invalid_urls_count > 0:
            issues.append(build_issue(
                "critical",
                "invalid_loc_urls",
                "Некорректные URL в <loc>",
                f"Найдено некорректных sitemap URL: {invalid_urls_count}.",
                "Исправьте некорректные <loc> и оставьте только абсолютные HTTP/HTTPS URL.",
                "SEO/Dev",
            ))
        if duplicate_urls_count > 0:
            issues.append(build_issue(
                "warning",
                "duplicate_urls",
                "Дубли URL между sitemap-файлами",
                f"Найдено повторов URL: {duplicate_urls_count}.",
                "Уберите дубли URL во всех sitemap-файлах.",
                "SEO",
            ))
        if self_child_refs > 0 or repeated_child_refs > 0:
            issues.append(build_issue(
                "warning",
                "sitemap_index_structure",
                "Проблемы структуры sitemap-индекса",
                f"Самоссылки: {self_child_refs}, повторные ссылки: {repeated_child_refs}.",
                "Уберите самоссылки и повторные ссылки на дочерние sitemap.",
                "Dev",
            ))
        if invalid_lastmod_count + stale_lastmod_count + lastmod_future_count > 0:
            issues.append(build_issue(
                "warning",
                "lastmod_quality",
                "Проблемы актуальности lastmod",
                f"Некорректных: {invalid_lastmod_count}, устаревших: {stale_lastmod_count}, будущих: {lastmod_future_count}.",
                "Нормализуйте формат lastmod и поддерживайте реалистичные и актуальные даты.",
                "SEO/Content",
            ))
        if hreflang_links_count > 0 and (hreflang_invalid_code_count + hreflang_invalid_href_count + hreflang_duplicate_lang_count) > 0:
            issues.append(build_issue(
                "warning",
                "hreflang_sitemap_issues",
                "Проблемы hreflang в sitemap",
                f"Некорректные коды: {hreflang_invalid_code_count}, некорректные href: {hreflang_invalid_href_count}, дубли языков: {hreflang_duplicate_lang_count}.",
                "Исправьте hreflang и href в alternate-ссылках sitemap.",
                "SEO",
            ))
        if (image_tags_count > 0 and image_missing_loc_count > 0) or (video_tags_count > 0 and video_missing_required_count > 0) or (news_tags_count > 0 and news_missing_required_count > 0):
            issues.append(build_issue(
                "warning",
                "media_extension_issues",
                "Проблемы расширений media/news sitemap",
                f"image: без loc={image_missing_loc_count}, video: без обязательных полей={video_missing_required_count}, news: без обязательных полей={news_missing_required_count}.",
                "Заполните обязательные поля в расширениях image/video/news sitemap.",
                "SEO/Dev",
            ))
        if live_non_indexable_count > 0:
            issues.append(build_issue(
                "critical",
                "live_non_indexable_sample",
                "В live-выборке есть неиндексируемые URL",
                f"{live_non_indexable_count} из {len(live_indexability_checks)} URL в выборке выглядят неиндексируемыми.",
                "Исправьте noindex/HTTP-проблемы на страницах из выборки и запустите проверку повторно.",
                "SEO/Dev",
            ))
        if canonical_error_count > 0:
            issues.append(build_issue(
                "warning",
                "canonical_sample_issues",
                "Проблемы canonical в случайной выборке",
                f"Выборка={canonical_checked_count}, отсутствует={canonical_missing_count}, некорректный={canonical_invalid_count}, non-self={canonical_non_self_count}.",
                "Исправьте отсутствующие и некорректные canonical; non-self canonical проверьте отдельно на предмет осознанной настройки.",
                "SEO/Dev",
            ))

        severity_counts = {
            "critical": sum(1 for it in issues if it.get("severity") == "critical"),
            "warning": sum(1 for it in issues if it.get("severity") == "warning"),
            "info": sum(1 for it in issues if it.get("severity") == "info"),
        }
        severity_weight = {"critical": 0, "warning": 1, "info": 2}
        issues_sorted = sorted(issues, key=lambda x: severity_weight.get(str(x.get("severity")), 9))
        top_fixes = dedupe_keep_order([it.get("action", "") for it in issues_sorted if it.get("action")])[:10]
        action_plan: List[Dict[str, Any]] = []
        for issue in issues_sorted[:12]:
            sev = str(issue.get("severity", "warning"))
            action_plan.append({
                "priority": "P0" if sev == "critical" else ("P1" if sev == "warning" else "P2"),
                "owner": issue.get("owner", "SEO"),
                "issue": issue.get("title", ""),
                "action": issue.get("action", ""),
                "sla": "24h" if sev == "critical" else ("3d" if sev == "warning" else "7d"),
            })
        if not action_plan and recommendations:
            action_plan.append({
                "priority": "P2",
                "owner": "SEO",
                "issue": "Критические блокеры отсутствуют",
                "action": recommendations[0],
                "sla": "7d",
            })

        quality_score = max(0, min(100, quality_score))
        if quality_score >= 90:
            quality_grade = "A"
        elif quality_score >= 80:
            quality_grade = "B"
        elif quality_score >= 70:
            quality_grade = "C"
        elif quality_score >= 60:
            quality_grade = "D"
        else:
            quality_grade = "F"

        return {
            "task_type": "sitemap_validate",
            "url": primary_root_url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "root_sitemaps": root_urls,
                "valid": len(errors) == 0 and len(sitemap_files) > 0,
                "status_code": root_status_code,
                "urls_count": total_urls_discovered,
                "unique_urls_count": len(seen_urls),
                "duplicate_urls_count": duplicate_urls_count,
                "duplicate_details": duplicate_details,
                "duplicate_details_truncated": duplicate_details_truncated,
                "sitemaps_scanned": len(sitemap_files),
                "sitemaps_valid": valid_files,
                "errors": dedupe_keep_order(errors),
                "warnings": dedupe_keep_order(warnings),
                "tool_notes": dedupe_keep_order(tool_notes),
                "recommendations": recommendations,
                "highlights": dedupe_keep_order(highlights),
                "quality_score": quality_score,
                "quality_grade": quality_grade,
                "sitemap_files": sitemap_files,
                "export_urls": all_urls,
                "urls_export_truncated": urls_export_truncated,
                "max_export_urls": max_export_urls,
                "export_chunk_size": export_chunk_size,
                "export_parts_count": export_parts_count,
                "scan_limit_files": max_sitemaps,
                "scan_limit_reached": bool(queue),
                "scan_queue_remaining": len(queue),
                "invalid_lastmod_count": invalid_lastmod_count,
                "invalid_changefreq_count": invalid_changefreq_count,
                "invalid_priority_count": invalid_priority_count,
                "invalid_urls_count": invalid_urls_count,
                "size": sum(item.get("size_bytes", 0) for item in sitemap_files),
                "max_depth_seen": max_depth_seen,
                "self_child_refs": self_child_refs,
                "repeated_child_refs": repeated_child_refs,
                "freshness": {
                    "lastmod_present_count": lastmod_present_count,
                    "lastmod_missing_count": lastmod_missing_count,
                    "lastmod_future_count": lastmod_future_count,
                    "stale_lastmod_count": stale_lastmod_count,
                    "uniform_lastmod_files": uniform_lastmod_files,
                    "stale_threshold_days": stale_days,
                },
                "hreflang": {
                    "detected": hreflang_links_count > 0,
                    "links_count": hreflang_links_count,
                    "urls_count": hreflang_urls_count,
                    "invalid_code_count": hreflang_invalid_code_count,
                    "invalid_href_count": hreflang_invalid_href_count,
                    "duplicate_lang_count": hreflang_duplicate_lang_count,
                    "has_x_default": hreflang_has_x_default,
                },
                "media_extensions": {
                    "image_tags_count": image_tags_count,
                    "image_missing_loc_count": image_missing_loc_count,
                    "video_tags_count": video_tags_count,
                    "video_missing_required_count": video_missing_required_count,
                    "news_tags_count": news_tags_count,
                    "news_missing_required_count": news_missing_required_count,
                },
                "live_indexability_checks": live_indexability_checks,
                "live_check_sample_size": len(live_indexability_checks),
                "live_non_indexable_count": live_non_indexable_count,
                "live_check_errors_count": live_check_errors_count,
                "canonical_sample": {
                    "sample_size": canonical_checked_count,
                    "missing_count": canonical_missing_count,
                    "invalid_count": canonical_invalid_count,
                    "non_self_count": canonical_non_self_count,
                },
                "issues": issues_sorted,
                "severity_counts": severity_counts,
                "top_fixes": top_fixes,
                "action_plan": action_plan,
            }
        }
    except Exception as e:
        return {
            "task_type": "sitemap_validate",
            "url": primary_root_url,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "results": {
                "valid": False,
                "error": str(e),
                "urls_count": 0,
                "export_urls": [],
                "sitemap_files": [],
            }
        }
    finally:
        await session.close()



def check_bots_full(
    url: str,
    selected_bots: Optional[List[str]] = None,
    bot_groups: Optional[List[str]] = None,
    retry_profile: str = "standard",
    criticality_profile: str = "balanced",
    sla_profile: str = "standard",
    baseline_enabled: bool = True,
    ai_block_expected: bool = False,
    batch_mode: bool = False,
    batch_urls: Optional[List[str]] = None,
    use_proxy: bool = False,
    custom_bot_name: Optional[str] = None,
    custom_bot_ua: Optional[str] = None,
) -> Dict[str, Any]:
    """Bot accessibility check with feature-flagged v2 engine."""
    from app.config import settings

    engine = (getattr(settings, "BOT_CHECK_ENGINE", "legacy") or "legacy").lower()
    has_custom_selection = bool(selected_bots or bot_groups)
    if has_custom_selection:
        engine = "v2"
    if engine == "v2":
        try:
            from app.tools.bots.service_v2 import BotAccessibilityServiceV2

            checker = BotAccessibilityServiceV2(
                timeout=getattr(settings, "BOT_CHECK_TIMEOUT", 15),
                max_workers=getattr(settings, "BOT_CHECK_MAX_WORKERS", 10),
                retry_profile=retry_profile,
                criticality_profile=criticality_profile,
                sla_profile=sla_profile,
                baseline_enabled=baseline_enabled,
                ai_block_expected=ai_block_expected,
                use_proxy=use_proxy,
            )
            if batch_mode:
                return checker.run_batch(batch_urls or [], selected_bots=selected_bots, bot_groups=bot_groups, custom_bot_name=custom_bot_name, custom_bot_ua=custom_bot_ua)
            return checker.run(url, selected_bots=selected_bots, bot_groups=bot_groups, custom_bot_name=custom_bot_name, custom_bot_ua=custom_bot_ua)
        except Exception as e:
            print(f"[API] bot v2 failed, fallback to legacy: {e}")
            legacy = _check_bots_legacy(url)
            legacy_results = legacy.get("results", {})
            legacy_results["engine"] = "legacy-fallback"
            legacy_results["engine_error"] = str(e)
            legacy_results["selected_bots_ignored"] = selected_bots or []
            legacy_results["selected_groups_ignored"] = bot_groups or []
            return legacy

    return _check_bots_legacy(url)


def _check_bots_legacy(url: str) -> Dict[str, Any]:
    import requests

    bots = [
        ("Googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
        ("YandexBot", "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"),
        ("Bingbot", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"),
        ("DuckDuckBot", "DuckDuckBot/1.0; (+https://duckduckgo.com/duckbot)"),
        ("GPTBot", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.0; +https://openai.com/gptbot)"),
    ]
    
    results = {}
    for bot_name, user_agent in bots:
        try:
            resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=10)
            results[bot_name] = {
                "status": resp.status_code,
                "accessible": resp.status_code == 200,
                "response_time": resp.elapsed.total_seconds() if hasattr(resp, 'elapsed') else None
            }
        except Exception as e:
            results[bot_name] = {"error": str(e), "accessible": False}
    
    return {
        "task_type": "bot_check",
        "url": url,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "results": {
            "engine": "legacy",
            "bots_checked": [b[0] for b in bots],
            "bot_results": results,
            "summary": {
                "total": len(bots),
                "accessible": sum(1 for r in results.values() if r.get("accessible")),
            }
        }
    }


# ============ REQUEST MODELS ============
class RobotsCheckRequest(URLModel):
    url: str
    use_proxy: bool = False

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_domain_input(cls, value):
        normalized = _normalize_http_input(str(value or ""))
        return normalized or value

class SitemapValidateRequest(URLModel):
    url: str
    use_proxy: bool = False

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_domain_input(cls, value):
        normalized = _normalize_http_input(str(value or ""))
        return normalized or value

class BotCheckRequest(URLModel):
    url: str
    selected_bots: Optional[List[str]] = None
    bot_groups: Optional[List[str]] = None
    retry_profile: Optional[str] = "standard"
    criticality_profile: Optional[str] = "balanced"
    sla_profile: Optional[str] = "standard"
    baseline_enabled: bool = True
    ai_block_expected: bool = False
    scan_mode: Optional[str] = "single"
    batch_urls: Optional[List[str]] = None
    use_proxy: bool = False
    custom_bot_name: Optional[str] = None
    custom_bot_ua: Optional[str] = None

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_domain_input(cls, value):
        normalized = _normalize_http_input(str(value or ""))
        return normalized or value

    @field_validator("selected_bots", "bot_groups", mode="before")
    @classmethod
    def _normalize_list_fields(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            v = value.strip()
            return [v] if v else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return value

    @field_validator("batch_urls", mode="before")
    @classmethod
    def _normalize_batch_urls(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            return [x.strip() for x in re.split(r"[\r\n,;]+", value) if x.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return value



def _is_likely_sitemap_url(value: str) -> bool:
    parsed = urlparse(value)
    path = (parsed.path or "").lower()
    if not path:
        return False
    if path.endswith(".xml") or "sitemap" in path:
        return True
    return False


def _looks_like_sitemap_xml(payload: str) -> bool:
    text = str(payload or "").lstrip("\ufeff \n\r\t").lower()
    return text.startswith("<?xml") or "<urlset" in text or "<sitemapindex" in text


def _candidate_sitemap_score(sitemap_url: str) -> int:
    path = (urlparse(sitemap_url).path or "").lower().strip("/")
    filename = path.split("/")[-1] if path else ""
    score = 0
    if filename in ("sitemap.xml", "sitemap_index.xml", "sitemap-index.xml", "wp-sitemap.xml"):
        score += 120
    if "index" in filename:
        score += 40
    if filename.startswith("sitemap"):
        score += 20
    if re.search(r"(news|image|video|blog|post|tag|category|product|forum|help|article|media)", filename):
        score -= 60
    score -= path.count("/")
    return score


def _discover_sitemap_urls(site_url: str, timeout: int = 12) -> tuple[List[str], Optional[str]]:
    """Discover sitemap URLs for a site. Returns (sitemap_urls, source)."""
    candidate_root = _normalize_http_input(site_url)
    if not candidate_root:
        return [], None

    parsed_root = urlparse(candidate_root)
    root = f"{parsed_root.scheme}://{parsed_root.netloc}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEO-Tools/1.0)"}

    with requests.Session() as session:
        # 1) robots.txt sitemap declarations (priority)
        try:
            robots_resp = _safe_fetch_with_redirects_sync(
                session,
                urljoin(root, "/robots.txt"),
                timeout=timeout,
                headers=headers,
            )
            if robots_resp.status_code == 200:
                robots_candidates: List[str] = []
                for line in (robots_resp.text or "").splitlines():
                    if not re.match(r"^\s*sitemap\s*:", line, flags=re.IGNORECASE):
                        continue
                    raw_loc = line.split(":", 1)[1].strip() if ":" in line else ""
                    if not raw_loc:
                        continue
                    loc = urljoin(root + "/", raw_loc)
                    normalized_loc = _normalize_http_input(loc)
                    if not normalized_loc:
                        continue
                    if _get_public_target_error(normalized_loc):
                        continue
                    try:
                        sm_resp = _safe_fetch_with_redirects_sync(
                            session,
                            normalized_loc,
                            timeout=timeout,
                            headers=headers,
                        )
                        decoded_content, _ = _decode_sitemap_payload(
                            sm_resp.content,
                            getattr(sm_resp, "url", normalized_loc),
                            getattr(sm_resp, "headers", {}),
                        )
                        if sm_resp.status_code == 200 and _looks_like_sitemap_bytes(decoded_content):
                            robots_candidates.append(normalized_loc)
                    except Exception:
                        continue
                if robots_candidates:
                    unique_candidates = list(dict.fromkeys(robots_candidates))
                    unique_candidates.sort(key=lambda u: (_candidate_sitemap_score(u), -len(u)), reverse=True)
                    return unique_candidates, "robots.txt"
        except Exception:
            pass

        # 2) Common fallback sitemap paths
        common_paths = (
            "/sitemap.xml",
            "/sitemap.xml.gz",
            "/sitemap_index.xml",
            "/sitemap_index.xml.gz",
            "/sitemap-index.xml",
            "/sitemap-index.xml.gz",
            "/sitemaps.xml",
            "/sitemaps.xml.gz",
            "/sitemaps/sitemap.xml",
            "/sitemaps/sitemap.xml.gz",
            "/wp-sitemap.xml",
            "/wp-sitemap.xml.gz",
        )
        for path in common_paths:
            loc = urljoin(root, path)
            if _get_public_target_error(loc):
                continue
            try:
                sm_resp = _safe_fetch_with_redirects_sync(
                    session,
                    loc,
                    timeout=timeout,
                    headers=headers,
                )
                decoded_content, _ = _decode_sitemap_payload(
                    sm_resp.content,
                    getattr(sm_resp, "url", loc),
                    getattr(sm_resp, "headers", {}),
                )
                if sm_resp.status_code == 200 and _looks_like_sitemap_bytes(decoded_content):
                    return [loc], "common_path"
            except Exception:
                continue

    return [], None


async def _discover_sitemap_urls_async(site_url: str, timeout: int = 12) -> tuple[List[str], Optional[str]]:
    """Async discovery for sitemap URLs. Returns (sitemap_urls, source)."""
    candidate_root = _normalize_http_input(site_url)
    if not candidate_root:
        return [], None

    parsed_root = urlparse(candidate_root)
    root = f"{parsed_root.scheme}://{parsed_root.netloc}"

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 (compatible; SEO-Tools/1.0)"}) as session:
        try:
            robots_resp = await _safe_fetch_with_redirects_async(
                session,
                urljoin(root, "/robots.txt"),
                timeout=timeout,
            )
            if robots_resp.status_code == 200:
                robots_candidates: List[str] = []
                for line in (robots_resp.text or "").splitlines():
                    if not re.match(r"^\s*sitemap\s*:", line, flags=re.IGNORECASE):
                        continue
                    raw_loc = line.split(":", 1)[1].strip() if ":" in line else ""
                    if not raw_loc:
                        continue
                    loc = urljoin(root + "/", raw_loc)
                    normalized_loc = _normalize_http_input(loc)
                    if not normalized_loc:
                        continue
                    if _get_public_target_error(normalized_loc):
                        continue
                    try:
                        sm_resp = await _safe_fetch_with_redirects_async(
                            session,
                            normalized_loc,
                            timeout=timeout,
                            read_text=False,
                        )
                        decoded_content, _ = _decode_sitemap_payload(
                            sm_resp.content,
                            sm_resp.url,
                            sm_resp.headers,
                        )
                        if sm_resp.status_code == 200 and _looks_like_sitemap_bytes(decoded_content):
                            robots_candidates.append(normalized_loc)
                    except Exception:
                        continue
                if robots_candidates:
                    unique_candidates = list(dict.fromkeys(robots_candidates))
                    unique_candidates.sort(key=lambda u: (_candidate_sitemap_score(u), -len(u)), reverse=True)
                    return unique_candidates, "robots.txt"
        except Exception:
            pass

        common_paths = (
            "/sitemap.xml",
            "/sitemap.xml.gz",
            "/sitemap_index.xml",
            "/sitemap_index.xml.gz",
            "/sitemap-index.xml",
            "/sitemap-index.xml.gz",
            "/sitemaps.xml",
            "/sitemaps.xml.gz",
            "/sitemaps/sitemap.xml",
            "/sitemaps/sitemap.xml.gz",
            "/wp-sitemap.xml",
            "/wp-sitemap.xml.gz",
        )
        for path in common_paths:
            loc = urljoin(root, path)
            if _get_public_target_error(loc):
                continue
            try:
                sm_resp = await _safe_fetch_with_redirects_async(
                    session,
                    loc,
                    timeout=timeout,
                    read_text=False,
                )
                decoded_content, _ = _decode_sitemap_payload(
                    sm_resp.content,
                    sm_resp.url,
                    sm_resp.headers,
                )
                if sm_resp.status_code == 200 and _looks_like_sitemap_bytes(decoded_content):
                    return [loc], "common_path"
            except Exception:
                continue

    return [], None


# ============ API ENDPOINTS ============

@router.post("/tasks/robots-check")
async def create_robots_check(data: RobotsCheckRequest, request: Request):
    """Full robots.txt analysis"""
    url = _normalize_http_input(str(data.url or ""))
    if not url:
        raise HTTPException(status_code=422, detail="Введите корректный домен или URL сайта.")
    
    print(f"[API] Full robots.txt analysis for: {url}")
    
    from app.core.scan_token import capture_scan_token_from_request, scan_token_context

    scan_token = capture_scan_token_from_request(request)
    with scan_token_context(scan_token):
        if data.use_proxy:
            result = await check_robots_full_async(url, use_proxy=True)
        else:
            result = await check_robots_full_async(url)
    task_id = f"robots-{datetime.now().timestamp()}"
    create_task_result(task_id, "robots_check", url, result)
    
    return {
        "task_id": task_id,
        "status": "SUCCESS",
        "message": "Robots.txt analysis completed"
    }



@router.post("/tasks/sitemap-validate")
async def create_sitemap_validate(data: SitemapValidateRequest, request: Request):
    """Full sitemap validation"""
    raw_input = str(data.url or "").strip()
    normalized_input = _normalize_http_input(raw_input)
    if not normalized_input:
        raise HTTPException(status_code=422, detail="Введите корректный домен или URL sitemap.")
    target_error = _get_public_target_error(_root_site_url(normalized_input))
    if target_error:
        raise HTTPException(status_code=422, detail=target_error)

    if _is_likely_sitemap_url(normalized_input):
        target_sitemap_urls = [normalized_input]
        discovery_source = "direct_input"
    else:
        discovered_urls, source = await _discover_sitemap_urls_async(normalized_input)
        if not discovered_urls:
            raise HTTPException(
                status_code=422,
                detail="Мы не нашли sitemap автоматически. Введите полный URL sitemap (например, https://example.com/sitemap.xml)."
            )
        target_sitemap_urls = discovered_urls
        discovery_source = source or "auto_discovery"

    print(
        f"[API] Полная валидация sitemap для input={normalized_input}, "
        f"sitemaps={len(target_sitemap_urls)}, source={discovery_source}"
    )

    from app.core.scan_token import capture_scan_token_from_request, scan_token_context

    scan_token = capture_scan_token_from_request(request)
    with scan_token_context(scan_token):
        if data.use_proxy:
            result = await check_sitemap_full_async(target_sitemap_urls, use_proxy=True)
        else:
            result = await check_sitemap_full_async(target_sitemap_urls)
    if isinstance(result, dict):
        resolved_sitemap_url = target_sitemap_urls[0] if target_sitemap_urls else ""
        result["input_url"] = normalized_input
        result["resolved_sitemap_url"] = resolved_sitemap_url
        result["resolved_sitemap_urls"] = target_sitemap_urls
        result["sitemap_discovery_source"] = discovery_source
        results_payload = result.setdefault("results", {})
        if isinstance(results_payload, dict):
            results_payload["input_url"] = normalized_input
            results_payload["resolved_sitemap_url"] = resolved_sitemap_url
            results_payload["resolved_sitemap_urls"] = target_sitemap_urls
            results_payload["sitemap_discovery_source"] = discovery_source
    task_id = f"sitemap-{datetime.now().timestamp()}"
    create_task_result(
        task_id,
        "sitemap_validate",
        target_sitemap_urls[0] if target_sitemap_urls else normalized_input,
        result
    )
    
    return {
        "task_id": task_id,
        "status": "SUCCESS",
        "message": "Валидация sitemap завершена"
    }


@router.post("/tasks/bot-check")
async def create_bot_check(data: BotCheckRequest, request: Request):
    """Full bot accessibility check"""
    url = data.url
    
    print(f"[API] Full bot check for: {url}")
    
    from app.core.scan_token import capture_scan_token_from_request, scan_token_context

    scan_token = capture_scan_token_from_request(request)
    with scan_token_context(scan_token):
        result = check_bots_full(
            url,
            selected_bots=data.selected_bots,
            bot_groups=data.bot_groups,
            retry_profile=(data.retry_profile or "standard"),
            criticality_profile=(data.criticality_profile or "balanced"),
            sla_profile=(data.sla_profile or "standard"),
            baseline_enabled=bool(data.baseline_enabled),
            ai_block_expected=bool(data.ai_block_expected),
            batch_mode=(str(data.scan_mode or "single").lower() == "batch"),
            batch_urls=(data.batch_urls or []),
            use_proxy=bool(data.use_proxy),
            custom_bot_name=data.custom_bot_name,
            custom_bot_ua=data.custom_bot_ua,
        )
    task_id = f"bots-{datetime.now().timestamp()}"
    create_task_result(task_id, "bot_check", url, result)
    
    return {
        "task_id": task_id,
        "status": "SUCCESS",
        "message": "Bot check completed"
    }


# ============ ROBOTS.TXT VISUAL CONSTRUCTOR ============


@router.post("/api/tools/robots-validator")
async def validate_url_accessibility(data: dict):
    """Check if a URL is accessible based on given robots.txt rules."""
    url = data.get("url", "")
    robots_rules = data.get("rules", [])
    test_path = urlparse(url).path or "/"

    results = []
    for rule in robots_rules:
        ua = rule.get("user_agent", "*")
        directive = rule.get("directive", "disallow")
        path = rule.get("path", "/")
        matches = test_path.startswith(path)
        results.append({
            "user_agent": ua,
            "directive": directive,
            "path": path,
            "matches": matches,
            "result": "blocked" if matches and directive == "disallow" else "allowed",
        })

    return {"url": url, "test_path": test_path, "rules_checked": len(results), "results": results}


@router.post("/api/tools/robots-generator")
async def generate_robots_txt(data: dict):
    """Generate robots.txt content from structured rules."""
    groups = data.get("groups", [])
    sitemaps = data.get("sitemaps", [])

    lines: List[str] = []
    for group in groups:
        ua = group.get("user_agent", "*")
        lines.append(f"User-agent: {ua}")
        for rule in group.get("rules", []):
            directive = rule.get("directive", "Disallow")
            path = rule.get("path", "/")
            lines.append(f"{directive}: {path}")
        lines.append("")

    for sm in sitemaps:
        if sm.strip():
            lines.append(f"Sitemap: {sm.strip()}")

    content = "\n".join(lines)
    return {"content": content, "lines": len(lines), "groups": len(groups), "sitemaps": len(sitemaps)}

