"""Network helpers for Site Audit Pro."""
from __future__ import annotations

import requests


def build_session(use_proxy: bool) -> requests.Session:
    """Prepare requests session with default UA and optional proxy settings."""
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})
    if use_proxy:
        from app.proxy import get_requests_proxies

        proxies = get_requests_proxies()
        if proxies:
            session.proxies.update(proxies)
    return session
