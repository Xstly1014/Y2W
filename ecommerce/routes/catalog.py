"""Catalog routes — categories, products, search."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ecommerce.routes.deps import get_db, get_user_id
from ecommerce.schemas.product import (
    ProductDetail, ProductListItem, ProductListResponse, SearchFilters,
)
from ecommerce.services import product_service

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    """Return nested category tree for the navigation."""
    return product_service.get_category_tree(db)


@router.get("/products", response_model=ProductListResponse)
def list_products(
    keyword: str | None = Query(default=None),
    category_id: int | None = Query(default=None),
    brand: str | None = Query(default=None),
    price_min: float | None = Query(default=None),
    price_max: float | None = Query(default=None),
    sort_by: str = Query(default="default"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    filters = SearchFilters(
        keyword=keyword, category_id=category_id, brand=brand,
        price_min=price_min, price_max=price_max, sort_by=sort_by,
        page=page, page_size=page_size,
    )
    items, total = product_service.search_products(db, filters)
    return ProductListResponse(
        items=[ProductListItem.model_validate(p) for p in items],
        total=total, page=page, page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get("/products/hot", response_model=list[ProductListItem])
def hot_products(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    items = product_service.get_hot_products(db, limit=limit)
    return [ProductListItem.model_validate(p) for p in items]


@router.get("/products/new", response_model=list[ProductListItem])
def new_products(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    items = product_service.get_new_products(db, limit=limit)
    return [ProductListItem.model_validate(p) for p in items]


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    product = product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    # Record browsing history (best-effort, don't fail the request).
    try:
        product_service.record_browsing(db, user_id, product_id)
        db.commit()
    except Exception:  # pragma: no cover — best-effort
        db.rollback()
    return ProductDetail.model_validate(product)


@router.get("/products/{product_id}/related", response_model=list[ProductListItem])
def related_products(
    product_id: int,
    limit: int = Query(default=6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    product = db.get(product_service.Product, product_id) if hasattr(product_service, "Product") else None
    # Simpler: re-fetch
    from ecommerce.db.models import Product
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    items = product_service.get_related_products(db, product, limit=limit)
    return [ProductListItem.model_validate(p) for p in items]
