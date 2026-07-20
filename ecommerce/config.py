"""E-commerce service configuration.

Reads from .env via pydantic-settings. All DB connection params live here so
`config/settings.py` doesn't need to know about the e-commerce module.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class EcommerceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="ECOMMERCE_",
    )

    # ----- PostgreSQL connection -----
    # E.g. postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/ecommerce
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/ecommerce",
        alias="ECOMMERCE_DATABASE_URL",
    )
    # Async variant used by SSE endpoints / future async work.
    database_url_async: str = Field(
        default="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ecommerce",
        alias="ECOMMERCE_DATABASE_URL_ASYNC",
    )
    db_pool_size: int = Field(default=10, alias="ECOMMERCE_DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="ECOMMERCE_DB_MAX_OVERFLOW")
    db_echo: bool = Field(default=False, alias="ECOMMERCE_DB_ECHO")

    # ----- Service ports -----
    port: int = Field(default=8002, alias="ECOMMERCE_PORT")
    host: str = Field(default="127.0.0.1", alias="ECOMMERCE_HOST")

    # ----- Cross-service URLs -----
    # AI customer service (existing agent on api 8000).
    agent_api_url: str = Field(
        default="http://127.0.0.1:8000", alias="ECOMMERCE_AGENT_API_URL"
    )

    # ----- Business rules -----
    cart_max_items: int = Field(default=50, alias="ECOMMERCE_CART_MAX_ITEMS")
    order_max_items: int = Field(default=100, alias="ECOMMERCE_ORDER_MAX_ITEMS")
    payment_currency: str = Field(default="CNY", alias="ECOMMERCE_PAYMENT_CURRENCY")
    # Free shipping threshold in CNY.
    free_shipping_threshold: float = Field(default=99.0, alias="ECOMMERCE_FREE_SHIPPING")
    default_shipping_fee: float = Field(default=12.0, alias="ECOMMERCE_SHIPPING_FEE")

    # ----- Seeding -----
    seed_products_count: int = Field(default=120, alias="ECOMMERCE_SEED_PRODUCTS")


settings = EcommerceSettings()
