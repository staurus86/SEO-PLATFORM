"""
Конфигурация SEO Tools Platform
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


def env_bool(name: str, default: str = "false") -> bool:
    raw = str(os.getenv(name, default) or "").strip()
    if len(raw) >= 2 and ((raw[0] == raw[-1] == '"') or (raw[0] == raw[-1] == "'")):
        raw = raw[1:-1].strip()
    return raw.lower() in {"1", "true", "yes", "on"}


class Settings(BaseSettings):
    """Настройки приложения"""
    model_config = SettingsConfigDict(env_file=".env")
    
    # App
    APP_NAME: str = "SEO Tools Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = env_bool("DEBUG", "false")
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_RETRY_COOLDOWN_SEC: int = int(os.getenv("REDIS_RETRY_COOLDOWN_SEC", "30"))
    TASK_STORE_REDIS_URL: str = os.getenv("TASK_STORE_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    PROGRESS_REDIS_URL: str = os.getenv("PROGRESS_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    RATE_LIMIT_REDIS_URL: str = os.getenv("RATE_LIMIT_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    LLM_CRAWLER_REDIS_URL: str = os.getenv("LLM_CRAWLER_REDIS_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    TASK_STORE_REDIS_PREFIX: str = os.getenv("TASK_STORE_REDIS_PREFIX", "task")
    PROGRESS_REDIS_PREFIX: str = os.getenv("PROGRESS_REDIS_PREFIX", "task_progress")
    RATE_LIMIT_REDIS_PREFIX: str = os.getenv("RATE_LIMIT_REDIS_PREFIX", "ratelimit")
    LLM_CRAWLER_REDIS_PREFIX: str = os.getenv("LLM_CRAWLER_REDIS_PREFIX", "llmCrawler")
    
    # Celery - use REDIS_URL from environment
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Timeouts (seconds)
    HTTP_TIMEOUT_NORMAL: int = 10
    HTTP_TIMEOUT_RENDER: int = 15
    MAX_TASK_DURATION: int = 20 * 60  # 20 minutes
    
    # Page Limits
    MAX_PAGES_DEFAULT: int = 100
    MAX_PAGES_LIMIT: int = 2000
    
    # Concurrent Tasks
    MAX_CONCURRENT_TASKS: int = 10
    
    # Rate Limiting (test-friendly defaults: effectively disabled)
    RATE_LIMIT_PER_HOUR: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "999"))
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "10"))
    RATE_LIMIT_EXPORT: int = int(os.getenv("RATE_LIMIT_EXPORT", "10"))
    RATE_LIMIT_EXPORT_WINDOW: int = int(os.getenv("RATE_LIMIT_EXPORT_WINDOW", "60"))
    
    # Reports
    REPORTS_DIR: str = "reports_output"
    MAX_REPORT_AGE_DAYS: int = 7

    # Sitemap validator
    SITEMAP_MAX_FILES: int = int(os.getenv("SITEMAP_MAX_FILES", "500"))
    SITEMAP_MAX_EXPORT_URLS: int = int(os.getenv("SITEMAP_MAX_EXPORT_URLS", "100000"))

    # Link Profile upload limits
    LINK_PROFILE_MAX_BACKLINK_FILES: int = int(os.getenv("LINK_PROFILE_MAX_BACKLINK_FILES", "20"))
    LINK_PROFILE_MAX_FILE_SIZE_BYTES: int = int(os.getenv("LINK_PROFILE_MAX_FILE_SIZE_BYTES", str(35 * 1024 * 1024)))
    LINK_PROFILE_MAX_BATCH_FILE_SIZE_BYTES: int = int(os.getenv("LINK_PROFILE_MAX_BATCH_FILE_SIZE_BYTES", str(5 * 1024 * 1024)))
    LINK_PROFILE_MAX_TOTAL_UPLOAD_BYTES: int = int(os.getenv("LINK_PROFILE_MAX_TOTAL_UPLOAD_BYTES", str(150 * 1024 * 1024)))

    # Clusterizer
    CLUSTERIZER_MAX_KEYWORDS: int = int(os.getenv("CLUSTERIZER_MAX_KEYWORDS", "25000"))
    CLUSTERIZER_MAX_FILE_SIZE_BYTES: int = int(os.getenv("CLUSTERIZER_MAX_FILE_SIZE_BYTES", str(50 * 1024 * 1024)))

    # Core Web Vitals (Google PageSpeed Insights API)
    PAGESPEED_API_KEY: str = os.getenv("PAGESPEED_API_KEY", "")
    PAGESPEED_TIMEOUT_SEC: int = int(os.getenv("PAGESPEED_TIMEOUT_SEC", "60"))
    PAGESPEED_MAX_RETRIES: int = int(os.getenv("PAGESPEED_MAX_RETRIES", "3"))

    # LLM Crawler Simulation rollout and limits
    FEATURE_LLM_CRAWLER: bool = env_bool("FEATURE_LLM_CRAWLER", "false")
    LLM_CRAWLER_ALLOWLIST: str = os.getenv("LLM_CRAWLER_ALLOWLIST", "")
    LLM_CRAWLER_ALLOW_ADMIN: bool = env_bool("LLM_CRAWLER_ALLOW_ADMIN", "true")
    JOB_CONCURRENCY: int = int(os.getenv("JOB_CONCURRENCY", "2"))
    FETCH_TIMEOUT_MS: int = int(os.getenv("FETCH_TIMEOUT_MS", "20000"))
    MAX_HTML_BYTES: int = int(os.getenv("MAX_HTML_BYTES", "2000000"))
    LLM_CRAWLER_MAX_REDIRECT_HOPS: int = int(os.getenv("LLM_CRAWLER_MAX_REDIRECT_HOPS", "8"))
    LLM_CRAWLER_JOB_TTL_SEC: int = int(os.getenv("LLM_CRAWLER_JOB_TTL_SEC", str(72 * 3600)))
    LLM_CRAWLER_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("LLM_CRAWLER_RATE_LIMIT_PER_MINUTE", "999"))
    LLM_CRAWLER_RENDER_RATE_LIMIT_PER_DAY: int = int(os.getenv("LLM_CRAWLER_RENDER_RATE_LIMIT_PER_DAY", "999"))
    LLM_CRAWLER_QUEUE_KEY: str = os.getenv("LLM_CRAWLER_QUEUE_KEY", "")
    LLM_CRAWLER_WORKER_HEARTBEAT_KEY: str = os.getenv("LLM_CRAWLER_WORKER_HEARTBEAT_KEY", "")
    LLM_CRAWLER_WORKER_HEARTBEAT_TTL_SEC: int = int(os.getenv("LLM_CRAWLER_WORKER_HEARTBEAT_TTL_SEC", "120"))
    LLM_CRAWLER_REQUIRE_HEALTHY_WORKER: bool = env_bool("LLM_CRAWLER_REQUIRE_HEALTHY_WORKER", "true")
    LLM_CRAWLER_STUCK_JOB_TIMEOUT_SEC: int = int(os.getenv("LLM_CRAWLER_STUCK_JOB_TIMEOUT_SEC", "300"))
    LLM_CRAWLER_INLINE_FALLBACK: bool = env_bool("LLM_CRAWLER_INLINE_FALLBACK", "false")
    LLM_CRAWLER_JOB_TTL_SECONDS: int = int(os.getenv("LLM_CRAWLER_JOB_TTL_SECONDS", str(86400)))
    LLM_CRAWLER_MAX_JOB_BYTES: int = int(os.getenv("LLM_CRAWLER_MAX_JOB_BYTES", str(1_500_000)))
    LLM_CRAWLER_COMPRESS_RESULTS: bool = env_bool("LLM_CRAWLER_COMPRESS_RESULTS", "true")
    LLM_CRAWLER_CLOAKING_ENABLED: bool = env_bool("LLM_CRAWLER_CLOAKING_ENABLED", "false")
    LLM_CRAWLER_EEAT_ENABLED: bool = env_bool("LLM_CRAWLER_EEAT_ENABLED", "false")
    LLM_CRAWLER_ENTITY_GRAPH_ENABLED: bool = env_bool("LLM_CRAWLER_ENTITY_GRAPH_ENABLED", "false")
    LLM_CRAWLER_VECTOR_SCORE_ENABLED: bool = env_bool("LLM_CRAWLER_VECTOR_SCORE_ENABLED", "false")
    LLM_CRAWLER_FUSION_ENGINE_ENABLED: bool = env_bool("LLM_CRAWLER_FUSION_ENGINE_ENABLED", "true")
    LLM_CRAWLER_BOT_VISIBILITY_ENABLED: bool = env_bool("LLM_CRAWLER_BOT_VISIBILITY_ENABLED", "true")
    LLM_CRAWLER_CHALLENGE_V2_ENABLED: bool = env_bool("LLM_CRAWLER_CHALLENGE_V2_ENABLED", "true")
    LLM_CRAWLER_CALIBRATION_V2_ENABLED: bool = env_bool("LLM_CRAWLER_CALIBRATION_V2_ENABLED", "true")
    LLM_REPORT_V2_ENABLED: bool = env_bool("LLM_REPORT_V2_ENABLED", "false")
    LLM_REPORT_V3_ENABLED: bool = env_bool("LLM_REPORT_V3_ENABLED", "false")
    LLM_UI_WOW_ENABLED: bool = env_bool("LLM_UI_WOW_ENABLED", "false")
    MAX_JOBS_PER_MINUTE: int = int(os.getenv("MAX_JOBS_PER_MINUTE", "10"))
    MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
    LLM_CRAWLER_LIMITS_ENABLED: bool = env_bool("LLM_CRAWLER_LIMITS_ENABLED", "false")
    LLM_SIMULATION_ENABLED: bool = env_bool("LLM_SIMULATION_ENABLED", "false")
    LLM_REPORT_HTML_ENABLED: bool = env_bool("LLM_REPORT_HTML_ENABLED", "false")

    # Bot check v2
    BOT_CHECK_ENGINE: str = os.getenv("BOT_CHECK_ENGINE", "legacy")
    BOT_CHECK_TIMEOUT: int = int(os.getenv("BOT_CHECK_TIMEOUT", "15"))
    BOT_CHECK_MAX_WORKERS: int = int(os.getenv("BOT_CHECK_MAX_WORKERS", "10"))

    # Mobile check v2
    MOBILE_CHECK_ENGINE: str = os.getenv("MOBILE_CHECK_ENGINE", "v2")
    MOBILE_CHECK_TIMEOUT: int = int(os.getenv("MOBILE_CHECK_TIMEOUT", "35"))
    MOBILE_CHECK_MODE: str = os.getenv("MOBILE_CHECK_MODE", "quick")

    # Render audit v2
    RENDER_AUDIT_ENGINE: str = os.getenv("RENDER_AUDIT_ENGINE", "v2")
    RENDER_AUDIT_TIMEOUT: int = int(os.getenv("RENDER_AUDIT_TIMEOUT", "35"))
    RENDER_AUDIT_DEBUG: bool = env_bool("RENDER_AUDIT_DEBUG", "false")

    # Site Audit Pro rollout
    SITE_AUDIT_PRO_ENABLED: bool = env_bool("SITE_AUDIT_PRO_ENABLED", "true")
    SITE_AUDIT_PRO_DEFAULT_MODE: str = os.getenv("SITE_AUDIT_PRO_DEFAULT_MODE", "quick")
    SITE_AUDIT_PRO_MAX_PAGES_LIMIT: int = int(os.getenv("SITE_AUDIT_PRO_MAX_PAGES_LIMIT", "1500"))
    SITE_AUDIT_PRO_MAX_PAGES_LIMIT_QUICK: int = int(os.getenv("SITE_AUDIT_PRO_MAX_PAGES_LIMIT_QUICK", "200"))
    SITE_AUDIT_PRO_MAX_PAGES_LIMIT_FULL: int = int(os.getenv("SITE_AUDIT_PRO_MAX_PAGES_LIMIT_FULL", "1500"))
    SITE_AUDIT_PRO_INLINE_ISSUES_LIMIT: int = int(os.getenv("SITE_AUDIT_PRO_INLINE_ISSUES_LIMIT", "200"))
    SITE_AUDIT_PRO_INLINE_SEMANTIC_LIMIT: int = int(os.getenv("SITE_AUDIT_PRO_INLINE_SEMANTIC_LIMIT", "200"))
    SITE_AUDIT_PRO_INLINE_PAGES_LIMIT: int = int(os.getenv("SITE_AUDIT_PRO_INLINE_PAGES_LIMIT", "500"))
    
    # CORS — comma-separated list of allowed origins.
    # Empty string means allow all ("*"). Set to your domain(s) in production.
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "")

    # History
    HISTORY_SIZE: int = 10
    
    # Geo-proxy (Webshare format: ip:port:user:pass, comma or newline separated)
    GEO_PROXY_LIST: str = os.getenv("GEO_PROXY_LIST", "")

    # Playwright
    PLAYWRIGHT_BROWSERS_PATH: str = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

    # Memory guard / in-memory fallback controls
    MEMORY_SWEEP_INTERVAL_SEC: int = int(os.getenv("MEMORY_SWEEP_INTERVAL_SEC", "60"))
    MEMORY_IDLE_CLEANUP_SEC: int = int(os.getenv("MEMORY_IDLE_CLEANUP_SEC", "300"))
    MEMORY_GC_COOLDOWN_SEC: int = int(os.getenv("MEMORY_GC_COOLDOWN_SEC", "120"))
    TASK_STORE_MEMORY_TTL_SEC: int = int(os.getenv("TASK_STORE_MEMORY_TTL_SEC", "7200"))
    TASK_STORE_MEMORY_MAX_ITEMS: int = int(os.getenv("TASK_STORE_MEMORY_MAX_ITEMS", "200"))
    TASK_STORE_MEMORY_MAX_BYTES: int = int(os.getenv("TASK_STORE_MEMORY_MAX_BYTES", str(64 * 1024 * 1024)))
    TASK_STORE_IDLE_KEEP_SEC: int = int(os.getenv("TASK_STORE_IDLE_KEEP_SEC", "900"))
    PROGRESS_MEMORY_TTL_SEC: int = int(os.getenv("PROGRESS_MEMORY_TTL_SEC", "7200"))
    PROGRESS_MEMORY_MAX_ITEMS: int = int(os.getenv("PROGRESS_MEMORY_MAX_ITEMS", "2000"))
    PROGRESS_MEMORY_MAX_BYTES: int = int(os.getenv("PROGRESS_MEMORY_MAX_BYTES", str(8 * 1024 * 1024)))
    PROGRESS_IDLE_KEEP_SEC: int = int(os.getenv("PROGRESS_IDLE_KEEP_SEC", "900"))
    
settings = Settings()
