"""
Rate limiting middleware for SEO Tools Platform.

Uses Redis fixed-window counters keyed by client IP.
Applies to POST requests on /api/ paths (tool endpoints).
Degrades gracefully — passes requests through when Redis is unavailable.

Config (env vars, see app/config.py):
    RATE_LIMIT_PER_HOUR  — max POST requests per IP in window (default 999)
    RATE_LIMIT_WINDOW    — window size in seconds (default 10)
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Paths that are excluded from rate limiting even if they are POSTs
_EXCLUDED_PREFIXES = (
    "/api/docs",
    "/api/redoc",
    "/api/openapi",
)

_redis_client: Optional[object] = None
_redis_retry_after_ts: float = 0.0


def _get_redis():
    global _redis_client, _redis_retry_after_ts
    now = time.time()
    if _redis_client is not None:
        try:
            _redis_client.ping()  # type: ignore[attr-defined]
            return _redis_client
        except Exception:
            _redis_client = None
            _redis_retry_after_ts = now + 10.0
            return None
    if now < _redis_retry_after_ts:
        return None
    try:
        import redis as _redis_lib  # noqa: PLC0415

        from app.config import settings

        _redis_client = _redis_lib.from_url(settings.RATE_LIMIT_REDIS_URL, decode_responses=True)
        _redis_client.ping()  # type: ignore[attr-defined]
        _redis_retry_after_ts = 0.0
    except Exception:
        _redis_client = None
        _redis_retry_after_ts = now + 10.0
    return _redis_client


def _get_client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for", "") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return str(getattr(request.client, "host", "unknown") or "unknown")


def _check_rate_limit(ip: str, limit: int, window_sec: int, bucket: str = "api") -> tuple[bool, int, int]:
    """
    Increment the request counter for ``ip`` in the current window.

    Returns (allowed, remaining, reset_in_seconds).
    Falls back to (True, limit, window_sec) when Redis is unavailable.
    """
    client = _get_redis()
    if not client:
        return True, limit, window_sec

    window_id = math.floor(time.time() / window_sec)
    from app.config import settings

    prefix = str(getattr(settings, "RATE_LIMIT_REDIS_PREFIX", "ratelimit") or "ratelimit").strip(": ")
    key = f"{prefix}:{bucket}:{ip}:{window_id}"
    try:
        count = int(client.incr(key))  # type: ignore[attr-defined]
        if count == 1:
            client.expire(key, window_sec)  # type: ignore[attr-defined]
        ttl = int(client.ttl(key) or window_sec)  # type: ignore[attr-defined]
        allowed = count <= limit
        remaining = max(0, limit - count)
        return allowed, remaining, max(0, ttl)
    except Exception as exc:
        logger.debug("Rate limit Redis error (ignored): %s", exc)
        return True, limit, window_sec


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    IP-based rate limiter applied to all POST /api/ requests.

    Reads limits from app.config.settings at dispatch time so that
    env-var overrides are picked up without restarting.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only rate-limit POST tool endpoints
        if request.method != "POST" or not path.startswith("/api/"):
            return await call_next(request)

        for excluded in _EXCLUDED_PREFIXES:
            if path.startswith(excluded):
                return await call_next(request)

        from app.config import settings  # late import to avoid circular deps

        ip = _get_client_ip(request)

        # Stricter limit for export endpoints (file generation is expensive)
        if path.startswith("/api/export/"):
            limit = max(1, int(getattr(settings, "RATE_LIMIT_EXPORT", 10)))
            window = max(1, int(getattr(settings, "RATE_LIMIT_EXPORT_WINDOW", 60)))
            allowed, remaining, reset_in = _check_rate_limit(ip, limit, window, bucket="export")
        else:
            limit = max(1, int(settings.RATE_LIMIT_PER_HOUR))
            window = max(1, int(settings.RATE_LIMIT_WINDOW))
            allowed, remaining, reset_in = _check_rate_limit(ip, limit, window)

        if not allowed:
            logger.warning("Rate limit exceeded for IP %s on %s", ip, path)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Too many requests.",
                    "remaining": remaining,
                    "reset_in": reset_in,
                },
                headers={
                    "Retry-After": str(reset_in),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(int(time.time()) + reset_in),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + reset_in)
        return response
