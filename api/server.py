"""FastAPI server entry point.

Wires all routers, serves the static front-end at /, and exposes
`/docs` (Swagger) for quick manual testing.

Run:
    python -m api.server
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.deps import get_agent_for_tenant
from api.routes import chat, kb, metrics, ops
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up the default tenant's agent at startup.

    Without this, the first `/api/chat/stream` request triggers a 30+ second
    agent build (loading BGE embedding model + FAISS collection + compiling
    the multi-agent graph). The browser's fetch() aborts long before the
    first SSE byte arrives, producing ``net::ERR_ABORTED`` and a broken UX.

    Pre-building at startup shifts that latency to service boot time —
    scripts/run_all.py's health check waits for `/api/health`, which only
    returns 200 after this warmup completes, so the user never sees a
    half-ready service.
    """
    default_tenant = settings.default_tenant_id
    logger.info("warming up agent for default tenant %r ...", default_tenant)
    try:
        get_agent_for_tenant(default_tenant)
        logger.info("agent warmup complete for tenant %r", default_tenant)
    except Exception as exc:  # noqa: BLE001
        # Don't crash the service — agent build will be retried on first
        # request. We just log so the operator knows warmup failed.
        logger.exception("agent warmup failed (will lazy-build on first request): %s", exc)
    yield


app = FastAPI(
    title="0719agent Commerce API",
    description=(
        "Cross-border e-commerce customer-service agent SaaS.\n\n"
        "Closed loop: user input -> agent (ReAct + RAG + commerce skills) "
        "-> trace -> flywheel -> post-training -> better model."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #
app.include_router(ops.health_router)
app.include_router(chat.router)
app.include_router(kb.router)
app.include_router(ops.feedback_router)
app.include_router(ops.traces_router)
app.include_router(ops.flywheel_router)
app.include_router(ops.dashboard_router)
app.include_router(metrics.metrics_router)


# --------------------------------------------------------------------------- #
# Static front-end (single-page demo UI)
# --------------------------------------------------------------------------- #
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Avatars are served at /avatars/... if the directory exists.
avatars_dir = STATIC_DIR / "avatars"
if avatars_dir.exists():
    app.mount("/avatars", StaticFiles(directory=avatars_dir), name="avatars")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


def main() -> None:
    logger.info("starting 0719agent Commerce API on port %s", settings.api_port)
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port, log_level="info")


if __name__ == "__main__":
    main()
