"""FastAPI app for the e-commerce service.

Listens on settings.port (default 8002). Mounts all routers, serves the
Vue 3 SPA from /shop, and runs a background task that auto-advances paid
orders through shipped → delivered → completed for demo purposes.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ecommerce.config import settings
from ecommerce.db.base import session_scope
from ecommerce.services.order_service import (
    complete_delivered_orders, deliver_shipped_orders, ship_paid_orders,
)

logger = logging.getLogger(__name__)

SHOP_STATIC_DIR = Path(__file__).resolve().parent / "static" / "shop"


async def _order_lifecycle_worker(app: FastAPI) -> None:
    """Background task that auto-advances order status for the demo.

    Runs every 15 seconds. A real system would have separate workers for
    shipping / delivery / completion driven by external events.
    """
    while True:
        try:
            await asyncio.sleep(15)
            with session_scope() as db:
                shipped = ship_paid_orders(db)
                delivered = deliver_shipped_orders(db)
                completed = complete_delivered_orders(db)
            if shipped or delivered or completed:
                logger.info(
                    "order lifecycle: shipped=%d delivered=%d completed=%d",
                    shipped, delivered, completed,
                )
        except asyncio.CancelledError:
            logger.info("order lifecycle worker cancelled")
            break
        except Exception:  # pragma: no cover — log and keep going
            logger.exception("order lifecycle worker error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB schema (dev only — prod uses Alembic migrations)."""
    # Import here so module-level imports don't trigger DB connection at
    # collection time (helps tests).
    from ecommerce.db.base import init_db
    try:
        init_db()
        logger.info("Database schema ensured.")
    except Exception as e:
        logger.error("init_db failed (is PostgreSQL running?): %s", e)
        logger.error("Dsn: %s", settings.database_url.replace(
            settings.database_url.split("://")[1].split("@")[0] if "@" in settings.database_url else "",
            "***:***"
        ))

    # Start background worker.
    task = asyncio.create_task(_order_lifecycle_worker(app))
    logger.info("E-commerce service up on http://%s:%d", settings.host, settings.port)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="E-commerce Platform",
        version="1.0.0",
        description="Cross-border e-commerce SaaS — products, cart, orders, payments, recommendations",
        lifespan=lifespan,
    )

    # CORS — the SPA is served from the same origin but the agent service
    # (api 8000) may be called directly from the browser too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # Register API routers.
    from ecommerce.routes import (
        cart, catalog, customer_service, orders, recommend, users,
    )
    app.include_router(catalog.router, prefix="/api")
    app.include_router(cart.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(orders.router, prefix="/api")
    app.include_router(recommend.router, prefix="/api")
    app.include_router(customer_service.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "service": "ecommerce",
            "version": "1.0.0",
            "agent_api_url": settings.agent_api_url,
        }

    # Mount the Vue 3 SPA at /shop (static assets).
    if SHOP_STATIC_DIR.exists():
        app.mount("/shop/static", StaticFiles(directory=SHOP_STATIC_DIR), name="shop-static")

    @app.get("/shop")
    @app.get("/shop/{path:path}")
    def shop_spa(path: str = ""):
        """Serve the SPA's index.html for any /shop/* route (history mode)."""
        # If a real file under shop/ was requested, serve it directly.
        candidate = SHOP_STATIC_DIR / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        # Force no-cache on the HTML shell so new vendor <script> tags
        # (e.g. adding vue-demi) are picked up immediately by the browser.
        return FileResponse(
            SHOP_STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # Root redirect to /shop for convenience.
    @app.get("/", include_in_schema=False)
    def root_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/shop")

    # SPA catch-all: Vue Router uses history mode, so /cart, /product/123,
    # /orders etc. must all serve the SPA shell. Only catch paths that don't
    # look like API/static requests.
    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        from fastapi.responses import RedirectResponse
        # Never intercept API, shop static, or docs.
        if path.startswith(("api/", "shop/", "docs", "openapi", "redoc")):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not Found")
        # Serve the SPA shell so Vue Router can take over.
        return FileResponse(
            SHOP_STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    uvicorn.run(
        "ecommerce.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
