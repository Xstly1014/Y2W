"""Recommendation service.

Implements three lightweight strategies suitable for a demo:

  1. **history** — based on user's browsing history, recommend products from
     the same categories the user has viewed most.
  2. **hot** — top sellers across the whole catalog (cold-start fallback).
  3. **related** — same-category products for a given product detail page.

A production system would use a trained model (matrix factorisation / two-tower
neural net / graph embedding). The strategies here are simple but follow the
same shape — given (user_id, context), return a ranked list of product ids
with explanations.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ecommerce.db.models import BrowsingHistory, Product

logger = logging.getLogger(__name__)


def recommend_for_user(db: Session, user_id: str, limit: int = 10) -> tuple[list[Product], str]:
    """Personalised recommendations. Returns (items, basis)."""
    # 1. Collect the user's most-viewed categories.
    stmt = (
        select(Product.category_id, func.count().label("views"))
        .join(BrowsingHistory, BrowsingHistory.product_id == Product.id)
        .where(BrowsingHistory.user_id == user_id, Product.category_id.is_not(None))
        .group_by(Product.category_id)
        .order_by(desc("views"))
        .limit(3)
    )
    top_cats = [row[0] for row in db.execute(stmt) if row[0] is not None]

    if not top_cats:
        # Cold start — fall back to hot products.
        stmt = (
            select(Product)
            .where(Product.is_published.is_(True))
            .order_by(desc(Product.sales_count), desc(Product.rating_avg))
            .limit(limit)
        )
        return list(db.scalars(stmt)), "hot"

    # 2. Recommend products from those categories, excluding already-viewed.
    viewed_stmt = select(BrowsingHistory.product_id).where(BrowsingHistory.user_id == user_id)
    viewed_ids = set(db.scalars(viewed_stmt))

    stmt = (
        select(Product)
        .where(
            Product.category_id.in_(top_cats),
            Product.is_published.is_(True),
            Product.id.notin_(viewed_stmt) if viewed_ids else True,
        )
        .order_by(desc(Product.sales_count), desc(Product.rating_avg))
        .limit(limit)
    )
    items = list(db.scalars(stmt))
    return items, "history"


def recommend_hot(db: Session, limit: int = 10) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.is_published.is_(True))
        .order_by(desc(Product.sales_count), desc(Product.rating_avg))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def recommend_related(db: Session, product_id: int, limit: int = 6) -> list[Product]:
    """Same-category products, excluding the given one."""
    product = db.get(Product, product_id)
    if product is None or product.category_id is None:
        return []
    stmt = (
        select(Product)
        .where(
            Product.category_id == product.category_id,
            Product.id != product.id,
            Product.is_published.is_(True),
        )
        .order_by(desc(Product.sales_count))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def recommend_new(db: Session, limit: int = 10) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.is_published.is_(True))
        .order_by(desc(Product.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt))
