"""
Production Celery Configuration - Fixed for Railway
"""
import os
import sys
from urllib.parse import urlsplit, urlunsplit

# CRITICAL: Read broker/backend URLs before anything else
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL", "")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND") or os.environ.get("REDIS_URL", "")
if not CELERY_BROKER_URL or not CELERY_RESULT_BACKEND:
    print("[CELERY FATAL] CELERY_BROKER_URL/CELERY_RESULT_BACKEND are not set!", file=sys.stderr)
    sys.exit(1)


def _mask_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        user = parsed.username or ""
        auth = f"{user}:***@" if user else ""
        return urlunsplit((parsed.scheme, f"{auth}{host}{port}", parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "***"


print(f"[CELERY] broker = {_mask_url(CELERY_BROKER_URL)}", file=sys.stderr)
print(f"[CELERY] backend = {_mask_url(CELERY_RESULT_BACKEND)}", file=sys.stderr)

from celery import Celery

# Create Celery app with explicit broker/backend
celery_app = Celery(
    "seo_tools",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["app.core.tasks"]
)

print(f"[CELERY] Created app with broker: {celery_app.conf.broker_url}", file=sys.stderr)

# Print all config
print(f"[CELERY] Final config broker_url: {celery_app.conf.get('broker_url')}", file=sys.stderr)
print(f"[CELERY] Final config result_backend: {celery_app.conf.get('result_backend')}", file=sys.stderr)

# Export for other modules
CELERY_APP = celery_app
