"""FastAPI server entry point.

Wires all routers and exposes the JSON API. The user-facing UI lives
exclusively in the standalone e-commerce service on port 8002
(`ecommerce/static/shop/index.html`); the agent backend on this
port (8000) is now API-only — there is no static page to delete,
so a GET / returns 404. The web mall's customer-service widget
calls this server's `/api/chat/stream` (proxied through the
e-commerce service's `/customer-service/chat/stream`).

Run:
    python -m api.server
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

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

    Also enables LangSmith tracing if configured — LangChain reads these
    env vars at import time, so we set them before any LLM call. See
    `optimization_logs/2026-07-20/issues-and-fixes.md` P1-3.
    """
    # Enable LangSmith auto-tracing if configured. LangChain's tracer reads
    # these env vars on first LLM call, so setting them here (before
    # get_agent_for_tenant builds the LLM) is sufficient.
    if settings.langchain_tracing_v2 and settings.langchain_api_key:
        import os

        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
        os.environ["LANGCHAIN_ENDPOINT"] = settings.langchain_endpoint
        logger.info(
            "LangSmith tracing enabled (project=%s, endpoint=%s)",
            settings.langchain_project, settings.langchain_endpoint,
        )
        # Verify the langsmith package is importable so we fail fast with a
        # helpful message instead of a cryptic ImportError on first chat.
        try:
            import langsmith  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            logger.warning(
                "LANGCHAIN_TRACING_V2=true but `langsmith` package is not "
                "installed. Run `pip install langsmith` to enable LangSmith export. "
                "Clearing LANGCHAIN_* env vars so LangChain doesn't ImportError "
                "on the first LLM call."
            )
            # Clear env vars so LangChain doesn't try to load the tracer
            # (which would ImportError on the first LLM call). See
            # `optimization_logs/2026-07-21/second-review.md` P1-12.
            for _k in (
                "LANGCHAIN_TRACING_V2",
                "LANGCHAIN_API_KEY",
                "LANGCHAIN_PROJECT",
                "LANGCHAIN_ENDPOINT",
            ):
                os.environ.pop(_k, None)

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


# Note: this server used to mount `static/index.html` at `/` and
# `static/admin.html` at `/admin`. The user-facing UI is now served
# exclusively by the e-commerce service (port 8002 → /shop), so the
# standalone agent page on port 8000 was removed. The API endpoints
# under `/api/*` still work — the web mall's customer-service widget
# calls `/api/chat/stream` via the proxy at
# `ecommerce/routes/customer_service.py`. See AGENT-WORKFLOW.md
# §14.7 "第九轮" for the rationale.


def main() -> None:
    logger.info("starting 0719agent Commerce API on port %s", settings.api_port)
    uvicorn.run(app, host="0.0.0.0", port=settings.api_port, log_level="info")


if __name__ == "__main__":
    main()
