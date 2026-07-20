"""SQLAlchemy engine, session factory, and declarative Base.

Uses psycopg2 (sync) for the engine so transactions block predictably and we
can share the engine between FastAPI sync routes and Alembic migrations. For
SSE endpoints that need async streaming we still rely on sync calls pushed
to a threadpool via FastAPI's `run_in_threadpool` — simpler than maintaining
two engines and two session families.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ecommerce.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def _build_engine() -> Engine:
    """Create the SQLAlchemy engine with sane pool defaults.

    `pool_pre_ping=True` ensures stale connections (e.g. after PG restart)
    are detected before use rather than causing a 500 mid-request.
    """
    eng = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.db_echo,
        future=True,
    )

    # Enable SQLite-style foreign key enforcement on every connection. Postgres
    # enforces FK by default, but being explicit guards against migrations
    # that disable constraints.
    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn, _):  # type: ignore[no-untyped-def]
        # Use the default public schema; kept here as a hook for future
        # multi-tenant schema isolation.
        with dbapi_conn.cursor() as cur:
            cur.execute("SET timezone TO 'Asia/Shanghai';")

    return eng


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a per-request Session.

    Commits on success, rolls back on exception, always closes. Routes that
    only read can use `session_scope()` directly without going through this.
    """
    db = SessionLocal()
    try:
        yield db
        # Don't auto-commit reads — only commit if the route called db.commit()
        # explicitly. This avoids pointless write transactions on GETs.
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed session for service-layer code outside FastAPI.

    Commits on clean exit, rolls back on exception. Use this in services/
    rather than reaching into SessionLocal directly so transaction boundaries
    are consistent.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create all tables (dev only). Production uses Alembic migrations."""
    # Import models so metadata is populated before create_all runs.
    from ecommerce.db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    logger.info("init_db: all tables ensured on %s", settings.database_url.split("@")[-1])
