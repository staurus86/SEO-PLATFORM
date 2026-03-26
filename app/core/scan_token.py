"""Temporary outbound scan token propagation for single-site scans."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Mapping, Optional

from fastapi import Request

TOKEN_HEADER_NAME = "X-SP-Token"
ENABLE_HEADER_NAME = "X-SP-Token-Enabled"

_scan_token_var: ContextVar[str] = ContextVar("scan_token", default="")


def capture_scan_token_from_request(request: Request) -> str:
    """Read the temporary scan token from request headers."""
    enabled = str(request.headers.get(ENABLE_HEADER_NAME, "") or "").strip().lower() == "true"
    if not enabled:
        return ""
    return str(request.headers.get(TOKEN_HEADER_NAME, "") or "").strip()


@contextmanager
def scan_token_context(token: str):
    """Temporarily enable outbound scan token propagation for current context."""
    normalized = str(token or "").strip()
    token_ref = _scan_token_var.set(normalized)
    try:
        yield
    finally:
        _scan_token_var.reset(token_ref)


def get_scan_token() -> str:
    """Return current outbound scan token or empty string."""
    return str(_scan_token_var.get("") or "").strip()


def get_scan_token_headers() -> Dict[str, str]:
    """Return outbound headers that should be attached to scan requests."""
    token = get_scan_token()
    if not token:
        return {}
    return {TOKEN_HEADER_NAME: token}


def merge_scan_token_headers(headers: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
    """Merge current scan token into outgoing headers without overwriting explicit values."""
    merged: Dict[str, object] = dict(headers or {})
    for key, value in get_scan_token_headers().items():
        merged.setdefault(key, value)
    return merged


def install_http_header_patches() -> None:
    """Patch common HTTP clients so outbound scan token is propagated automatically."""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    import requests

    original_request = requests.sessions.Session.request

    def patched_request(self, method, url, **kwargs):
        kwargs["headers"] = merge_scan_token_headers(kwargs.get("headers"))
        return original_request(self, method, url, **kwargs)

    requests.sessions.Session.request = patched_request

    try:
        import aiohttp

        original_aiohttp_init = aiohttp.ClientSession.__init__

        def patched_aiohttp_init(self, *args, **kwargs):
            kwargs["headers"] = merge_scan_token_headers(kwargs.get("headers"))
            return original_aiohttp_init(self, *args, **kwargs)

        aiohttp.ClientSession.__init__ = patched_aiohttp_init
    except Exception:
        pass

    _PATCH_INSTALLED = True


_PATCH_INSTALLED = False
