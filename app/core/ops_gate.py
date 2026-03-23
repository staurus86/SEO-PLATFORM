"""Access control helpers for ops dashboard and maintenance endpoints."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import settings


def _normalize_token(value: str) -> str:
    token = str(value or "").strip()
    if len(token) >= 2 and ((token[0] == token[-1] == '"') or (token[0] == token[-1] == "'")):
        token = token[1:-1].strip()
    return token


def _configured_token() -> str:
    return _normalize_token(getattr(settings, "OPS_ACCESS_TOKEN", "") or "")


def is_ops_allowed(request: Request) -> bool:
    configured = _configured_token()
    if not configured:
        return True

    if bool(getattr(settings, "OPS_ALLOW_ADMIN", True)):
        role = str(request.headers.get("x-role", "") or "").strip().lower()
        if role == "admin":
            return True

    presented = _normalize_token(
        request.headers.get("x-ops-token")
        or request.query_params.get("ops_token")
        or request.cookies.get("ops_token")
        or ""
    )
    return bool(presented and presented == configured)


def require_ops_access(request: Request) -> None:
    if is_ops_allowed(request):
        return
    raise HTTPException(status_code=403, detail="Ops access denied")
