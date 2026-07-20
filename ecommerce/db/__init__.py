"""Database package — engine, session factory, ORM models, seeder."""
from __future__ import annotations

from ecommerce.db.base import Base, engine, get_session, session_scope  # noqa: F401

# Import models so SQLAlchemy registers them on `Base.metadata` at import time.
# Without this, `Base.metadata.create_all(engine)` would create empty schema.
from ecommerce.db import models  # noqa: F401
