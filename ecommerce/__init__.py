"""E-commerce platform backend.

Standalone FastAPI service exposing product catalog, cart, orders, payments
and recommendation APIs. Listens on settings.ecommerce_port (default 8002).

Architecture:
    ecommerce/
      server.py            — FastAPI app + lifespan + router registration
      config.py            — Pydantic settings (PG DSN, ports, limits)
      db/
        base.py            — SQLAlchemy engine + session factory + Base
        models.py          — ORM models (categories, products, skus, ...)
        seed.py            — demo data seeder
      schemas/             — Pydantic request/response schemas
      services/            — business logic layer (DB transactions)
      routes/              — FastAPI routers (one per resource)
      alembic/             — DB migrations
      static/shop/         — Vue 3 SPA frontend
"""
