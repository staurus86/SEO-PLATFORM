#!/bin/bash
set -e

echo "============================================"
echo "SEO Tools Platform - Railway Deployment"
echo "============================================"
echo "MODE: ${SERVICE_MODE:-web}"
echo "PORT: ${PORT:-8000}"
echo "Current directory: $(pwd)"
echo ""

# Export PYTHONPATH
export PYTHONPATH=/app:$PYTHONPATH

# Show all env vars
echo "=== All Environment Variables ==="
env | grep -E '(REDIS|CELERY|SERVICE|PORT)' | sed 's/=.*@/=***@/' || echo "No matching vars found"
echo ""

if [ "$SERVICE_MODE" = "worker" ]; then
    echo "Starting in WORKER mode..."
    echo ""
    
    # Test environment first
    echo "=== Testing Environment ==="
    python3 test_env.py
    echo ""
    
    # Check REDIS_URL
    if [ -z "$REDIS_URL" ]; then
        echo "ERROR: REDIS_URL is empty!"
        exit 1
    fi
    
    echo "REDIS_URL is set to: ${REDIS_URL//:*@/:***@}"
    echo ""
    
    # Unset CELERY_* vars to prevent override
    unset CELERY_BROKER_URL
    unset CELERY_RESULT_BACKEND
    
    echo "Unset CELERY_BROKER_URL and CELERY_RESULT_BACKEND to prevent override"
    echo ""
    
    echo "Starting Celery worker..."
    echo "Queue: seo"
    echo "Concurrency: 2"
    echo ""
    
    exec celery -A app.core.celery_app worker --loglevel=info --concurrency=2 -Q seo
elif [ "$SERVICE_MODE" = "llm-worker" ]; then
    echo "Starting in LLM WORKER mode..."
    echo ""
    if [ -z "$REDIS_URL" ]; then
        echo "ERROR: REDIS_URL is empty for llm-worker. Configure Redis service variables first."
        exit 1
    fi
    export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"
    if ! compgen -G "${PLAYWRIGHT_BROWSERS_PATH}/chromium_headless_shell-*/chrome-linux/headless_shell" > /dev/null 2>&1; then
        echo "ERROR: Playwright headless_shell binary not found at expected path. Listing available browsers:"
        ls "${PLAYWRIGHT_BROWSERS_PATH}/" 2>/dev/null || echo "(directory empty or missing)"
        echo "Build image with Playwright browsers preinstalled. Runtime install is disabled for stability."
        exit 1
    fi
    echo "REDIS_URL is set to: ${REDIS_URL//:*@/:***@}"
    echo "JOB_CONCURRENCY: ${JOB_CONCURRENCY:-2}"
    echo "FETCH_TIMEOUT_MS: ${FETCH_TIMEOUT_MS:-20000}"
    echo "MAX_HTML_BYTES: ${MAX_HTML_BYTES:-2000000}"
    echo "PLAYWRIGHT_BROWSERS_PATH: ${PLAYWRIGHT_BROWSERS_PATH}"
    echo ""
    exec python -m app.tools.llmCrawler.worker
else
    echo "Starting in WEB mode..."
    echo ""

    export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/ms-playwright}"
    echo "PLAYWRIGHT_BROWSERS_PATH: ${PLAYWRIGHT_BROWSERS_PATH}"

    exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --log-level info
fi
