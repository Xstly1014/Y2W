"""Shared dependencies for routes.

`get_db` provides a per-request SQLAlchemy session. `get_user_id` extracts
the client-generated user id from the `X-User-Id` header (no auth — the
client just sends a stable uuid stored in localStorage).
"""
from __future__ import annotations

import uuid
from typing import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ecommerce.db.base import SessionLocal


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_user_id(x_user_id: str | None = Header(default=None, alias="X-User-Id")) -> str:
    """Extract or generate a user id.

    No auth — if the client didn't send a header we mint a fresh guest id.
    The frontend stores it in localStorage so subsequent requests reuse it.
    """
    if x_user_id:
        # Validate: 1-64 chars, alphanumeric + . _ -
        if not (1 <= len(x_user_id) <= 64) or not all(
            c.isalnum() or c in "._-" for c in x_user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-User-Id must be 1-64 chars of [A-Za-z0-9._-]",
            )
        return x_user_id
    # Mint a guest id. The frontend should persist this for the user.
    return f"guest-{uuid.uuid4().hex[:12]}"


# Convenience type aliases for route signatures.
DBSession = Depends(get_db)
UserId = Depends(get_user_id)
