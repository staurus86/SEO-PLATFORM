"""
SEO Tools Platform - FastAPI Application (Simplified for Railway)
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

# Setup logging first
_log_fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_log_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_stream_handler])

# Add rotating file handler (errors only, max 5 MB × 3 files)
_log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_log_dir, exist_ok=True)
_file_handler = RotatingFileHandler(
    os.path.join(_log_dir, "app.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setLevel(logging.ERROR)
_file_handler.setFormatter(_log_fmt)
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)

logger.info("=" * 50)
logger.info("STARTING SEO TOOLS PLATFORM")
logger.info("=" * 50)

# Log environment
logger.info(f"Python executable: {sys.executable}")
logger.info(f"Python version: {sys.version}")
logger.info(f"Current directory: {os.getcwd()}")
logger.info(f"Files in current dir: {os.listdir('.')}")
logger.info(f"PORT env var: {os.environ.get('PORT', 'NOT SET')}")
logger.info(f"PYTHONPATH: {os.environ.get('PYTHONPATH', 'NOT SET')}")

ASSET_VERSION = str(os.environ.get("RAILWAY_DEPLOYMENT_ID", "") or "").strip()
if not ASSET_VERSION:
    ASSET_VERSION = str(int(os.path.getmtime(__file__)))

# Try importing FastAPI
try:
    logger.info("Importing FastAPI...")
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, RedirectResponse
    from app.middleware import RateLimitMiddleware
    from app.core.memory_guard import start_memory_guard, stop_memory_guard, get_process_memory_snapshot
    logger.info("[OK] FastAPI imported successfully")
except Exception as e:
    logger.error(f"[ERROR] FastAPI import failed: {e}")
    import traceback
    traceback.print_exc()
    raise

# Try importing app config
try:
    logger.info("Importing app.config...")
    from app.config import settings
    logger.info(f"[OK] Config loaded: PORT={settings.PORT}, HOST={settings.HOST}")
except Exception as e:
    logger.error(f"[ERROR] Config import failed: {e}")
    import traceback
    traceback.print_exc()
    raise

# Try importing routes
try:
    logger.info("Importing app.api.routes...")
    from app.api.routes import router as api_router
    logger.info("[OK] Routes imported successfully")
except Exception as e:
    logger.error(f"[ERROR] Routes import failed: {e}")
    import traceback
    traceback.print_exc()
    raise

# Create FastAPI app
logger.info("Creating FastAPI app...")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_memory_guard()
    try:
        yield
    finally:
        stop_memory_guard()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Asynchronous SEO analysis platform with 10 tools",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# WebSocket connection manager — pushes real-time task updates to browsers
# ---------------------------------------------------------------------------
from typing import Dict, Set
import asyncio
import json as _json


class ConnectionManager:
    """Manages per-task WebSocket subscribers."""

    def __init__(self):
        self.active: Dict[str, Set[WebSocket]] = {}  # task_id -> set of websockets

    async def connect(self, websocket: WebSocket, task_id: str):
        await websocket.accept()
        if task_id not in self.active:
            self.active[task_id] = set()
        self.active[task_id].add(websocket)

    def disconnect(self, websocket: WebSocket, task_id: str):
        if task_id in self.active:
            self.active[task_id].discard(websocket)
            if not self.active[task_id]:
                del self.active[task_id]

    async def broadcast(self, task_id: str, data: dict):
        if task_id not in self.active:
            return
        dead: Set[WebSocket] = set()
        for ws in self.active[task_id]:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active[task_id].discard(ws)
        if task_id in self.active and not self.active[task_id]:
            del self.active[task_id]


ws_manager = ConnectionManager()


# CORS middleware
# In production set ALLOWED_ORIGINS env var (comma-separated domains).
# Browsers block credentialed requests to wildcard origins, so credentials
# are only enabled when specific origins are configured.
_origins_raw = settings.ALLOWED_ORIGINS.strip()
if _origins_raw:
    _allowed_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]
    _allow_credentials = True
else:
    _allowed_origins = ["*"]
    _allow_credentials = False  # wildcard + credentials is rejected by browsers

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

# Static files - try multiple paths
static_mounted = False
static_paths = ["app/static", "/app/app/static", "./app/static"]

for path in static_paths:
    try:
        logger.info(f"Trying static path: {path}")
        if os.path.exists(path):
            app.mount("/static", StaticFiles(directory=path), name="static")
            logger.info(f"[OK] Static files mounted from: {path}")
            static_mounted = True
            break
        else:
            logger.warning(f"Static path does not exist: {path}")
    except Exception as e:
        logger.warning(f"Failed to mount static from {path}: {e}")

if not static_mounted:
    logger.error("[ERROR] Could not mount static files from any path")

# Templates - try multiple paths for different environments
templates = None
template_paths = ["app/templates", "/app/app/templates", "./app/templates"]

for path in template_paths:
    try:
        logger.info(f"Trying templates path: {path}")
        if os.path.exists(path):
            templates = Jinja2Templates(directory=path)
            logger.info(f"[OK] Templates configured from: {path}")
            break
        else:
            logger.warning(f"Path does not exist: {path}")
    except Exception as e:
        logger.warning(f"Failed to load templates from {path}: {e}")

if templates is None:
    logger.error("[ERROR] Could not load templates from any path")
else:
    templates.env.globals["asset_version"] = ASSET_VERSION

# API routes
app.include_router(api_router)

# LLM crawler router is always included.
# Feature availability is enforced inside router via feature gate (403),
# so route registration must not be optional to avoid hidden 404 regressions.
from app.tools.llmCrawler.router import router as llm_crawler_router

app.include_router(llm_crawler_router)
logger.info("[OK] LLM crawler routes included")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page with tools."""
    if templates:
        llm_enabled = False
        try:
            from app.tools.llmCrawler.feature_gate import is_llm_crawler_enabled_for_request

            llm_enabled = bool(is_llm_crawler_enabled_for_request(request))
        except Exception:
            llm_enabled = False
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "feature_llm_crawler_enabled": llm_enabled,
            },
        )
    return HTMLResponse("<h1>SEO Tools Platform</h1><p>Templates not loaded</p>")


@app.get("/results/{task_id}", response_class=HTMLResponse)
async def results_page(request: Request, task_id: str):
    """Results page."""
    if templates:
        return templates.TemplateResponse(
            "task_progress.html", 
            {"request": request, "task_id": task_id, "app_version": settings.APP_VERSION}
        )
    return HTMLResponse(f"<h1>Results</h1><p>Task: {task_id}</p>")


@app.get("/llm-crawler/results/{job_id}", response_class=HTMLResponse)
async def llm_crawler_results_page(request: Request, job_id: str):
    """LLM crawler dedicated results page (single design)."""
    if templates:
        ui_wow_enabled = bool(getattr(settings, "LLM_UI_WOW_ENABLED", False))
        return templates.TemplateResponse(
            "llm_crawler_result_v2.html",
            {
                "request": request,
                "job_id": job_id,
                "app_version": settings.APP_VERSION,
                "llm_ui_wow_enabled": ui_wow_enabled,
            },
        )
    return HTMLResponse(f"<h1>LLM Crawler Result</h1><p>Job: {job_id}</p>")


@app.get("/llm-crawler/results/v2/{job_id}", response_class=HTMLResponse)
async def llm_crawler_results_page_v2(request: Request, job_id: str):
    """Backward-compatible alias to the single LLM crawler result page."""
    return RedirectResponse(url=f"/llm-crawler/results/{job_id}", status_code=307)


@app.websocket("/ws/tasks/{task_id}")
async def websocket_task(websocket: WebSocket, task_id: str):
    """Push real-time task progress to the browser via WebSocket."""
    await ws_manager.connect(websocket, task_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, task_id)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "memory": get_process_memory_snapshot(),
    }

@app.get("/robots.txt")
async def robots_txt():
    """Serve robots.txt"""
    return """User-agent: *
Allow: /
Sitemap: https://seo-tools-platform.up.railway.app/sitemap.xml
"""


@app.get("/favicon.ico")
async def favicon():
    """Serve favicon"""
    from fastapi.responses import Response
    # Return empty 204 No Content
    return Response(status_code=204)

@app.post("/export/robots")
async def export_robots_redirect(request: Request):
    """Handle export requests and redirect to API"""
    from fastapi import Request
    from app.api.routers.exports import export_robots_word, ExportRequest
    import json
    
    try:
        # Read JSON body
        body = await request.body()
        data = json.loads(body.decode('utf-8'))
        export_request = ExportRequest(task_id=data.get('task_id'))
        
        # Call the export function
        return await export_robots_word(export_request)
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": str(e)}, status_code=400)


logger.info("=" * 50)
logger.info("APP INITIALIZED SUCCESSFULLY")
logger.info("=" * 50)


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting uvicorn on {settings.HOST}:{settings.PORT}")
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
