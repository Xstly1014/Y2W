"""Product / category / search services."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ecommerce.db.models import BrowsingHistory, Category, Product, ProductImage, ProductReview, ProductSKU
from ecommerce.schemas.product import SearchFilters

logger = logging.getLogger(__name__)


def _apply_sort(query, sort_by: str):
    if sort_by == "sales":
        return query.order_by(desc(Product.sales_count), desc(Product.id))
    if sort_by == "price_asc":
        return query.order_by(Product.price_min.asc(), desc(Product.id))
    if sort_by == "price_desc":
        return query.order_by(Product.price_min.desc(), desc(Product.id))
    if sort_by == "rating":
        return query.order_by(desc(Product.rating_avg), desc(Product.rating_count))
    if sort_by == "newest":
        return query.order_by(desc(Product.created_at))
    # default: blend of sales + rating (popularity)
    return query.order_by(desc(Product.sales_count), desc(Product.rating_avg), desc(Product.id))


def list_categories(db: Session) -> list[Category]:
    """Return top-level categories with children preloaded."""
    stmt = (
        select(Category)
        .where(Category.parent_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.id)
        .options(selectinload(Category.children))
    )
    return list(db.scalars(stmt))


def get_category_tree(db: Session) -> list[dict]:
    """Nested tree for frontend nav. Materialised as dicts to avoid
    recursive Pydantic serialisation issues."""
    cats = list_categories(db)
    tree = []
    for c in cats:
        node = {
            "id": c.id, "parent_id": c.parent_id, "name": c.name, "slug": c.slug,
            "icon": c.icon, "sort_order": c.sort_order, "is_active": c.is_active,
            "children": [
                {
                    "id": ch.id, "parent_id": ch.parent_id, "name": ch.name,
                    "slug": ch.slug, "icon": ch.icon, "sort_order": ch.sort_order,
                    "is_active": ch.is_active, "children": [],
                }
                for ch in sorted(c.children, key=lambda x: (x.sort_order, x.id))
                if ch.is_active
            ],
        }
        tree.append(node)
    return tree


def search_products(db: Session, filters: SearchFilters) -> tuple[list[Product], int]:
    """Paged + filtered product search. Returns (items, total)."""
    stmt = select(Product).where(Product.is_published.is_(True))
    count_stmt = select(Product.id).where(Product.is_published.is_(True))

    if filters.keyword:
        like = f"%{filters.keyword}%"
        cond = or_(
            Product.title.ilike(like),
            Product.subtitle.ilike(like),
            Product.brand.ilike(like),
            Product.tags.ilike(like),
            Product.description.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    if filters.category_id is not None:
        # Match the category OR any of its descendants (one-level deep for demo).
        sub_cats = select(Category.id).where(Category.parent_id == filters.category_id)
        cat_cond = or_(
            Product.category_id == filters.category_id,
            Product.category_id.in_(sub_cats),
        )
        stmt = stmt.where(cat_cond)
        count_stmt = count_stmt.where(cat_cond)

    if filters.brand:
        stmt = stmt.where(Product.brand == filters.brand)
        count_stmt = count_stmt.where(Product.brand == filters.brand)

    if filters.price_min is not None:
        stmt = stmt.where(Product.price_min >= filters.price_min)
        count_stmt = count_stmt.where(Product.price_min >= filters.price_min)
    if filters.price_max is not None:
        stmt = stmt.where(Product.price_max <= filters.price_max)
        count_stmt = count_stmt.where(Product.price_max <= filters.price_max)

    # Sort BEFORE pagination so page boundaries are stable.
    stmt = _apply_sort(stmt, filters.sort_by)

    total = len(list(db.scalars(count_stmt)))
    offset = (filters.page - 1) * filters.page_size
    stmt = stmt.offset(offset).limit(filters.page_size)
    items = list(db.scalars(stmt))
    return items, total


def get_product(db: Session, product_id: int) -> Optional[Product]:
    stmt = (
        select(Product)
        .where(Product.id == product_id, Product.is_published.is_(True))
        .options(
            selectinload(Product.skus),
            selectinload(Product.images),
            selectinload(Product.reviews).joinedload(ProductReview.product),  # avoid N+1
        )
    )
    return db.scalars(stmt).first()


def get_product_by_sku(db: Session, sku_id: int) -> Optional[tuple[Product, ProductSKU]]:
    stmt = (
        select(ProductSKU)
        .where(ProductSKU.id == sku_id, ProductSKU.is_active.is_(True))
        .options(selectinload(ProductSKU.product))
    )
    sku = db.scalars(stmt).first()
    if not sku:
        return None
    return sku.product, sku


def record_browsing(db: Session, user_id: str, product_id: int) -> None:
    """Insert a browsing history row. Failures are non-fatal (best-effort)."""
    try:
        db.add(BrowsingHistory(user_id=user_id, product_id=product_id))
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.warning("record_browsing: product %s not found, skipping", product_id)


def get_related_products(db: Session, product: Product, limit: int = 6) -> list[Product]:
    """Same category, exclude self, sort by sales."""
    if product.category_id is None:
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


def get_hot_products(db: Session, limit: int = 10) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.is_published.is_(True))
        .order_by(desc(Product.sales_count), desc(Product.rating_avg))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def get_new_products(db: Session, limit: int = 10) -> list[Product]:
    stmt = (
        select(Product)
        .where(Product.is_published.is_(True))
        .order_by(desc(Product.created_at))
        .limit(limit)
    )
    return list(db.scalars(stmt))


def create_review(db: Session, user_id: str, product_id: int, rating: int,
                  content: Optional[str], order_id: Optional[int] = None) -> ProductReview:
    """Insert a review and recompute product rating aggregates."""
    review = ProductReview(
        product_id=product_id, user_id=user_id, order_id=order_id,
        rating=rating, content=content,
    )
    db.add(review)
    db.flush()

    # Recompute aggregates — small table so a full scan is fine.
    stmt = select(ProductReview).where(ProductReview.product_id == product_id)
    reviews = list(db.scalars(stmt))
    if reviews:
        product = db.get(Product, product_id)
        if product:
            avg = sum(r.rating for r in reviews) / len(reviews)
            product.rating_avg = round(min(5.0, max(0.0, avg)), 1)
            product.rating_count = len(reviews)
            db.flush()
    return review
