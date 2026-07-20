"""Recommendation routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ecommerce.routes.deps import get_db, get_user_id
from ecommerce.schemas.product import ProductListItem
from ecommerce.services import recommend_service

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/for-user")
def for_user(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    items, basis = recommend_service.recommend_for_user(db, user_id, limit=limit)
    return {
        "basis": basis,
        "items": [ProductListItem.model_validate(p) for p in items],
    }


@router.get("/hot", response_model=list[ProductListItem])
def hot(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    items = recommend_service.recommend_hot(db, limit=limit)
    return [ProductListItem.model_validate(p) for p in items]


@router.get("/new", response_model=list[ProductListItem])
def new_arrivals(
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    items = recommend_service.recommend_new(db, limit=limit)
    return [ProductListItem.model_validate(p) for p in items]


@router.get("/related/{product_id}", response_model=list[ProductListItem])
def related(
    product_id: int,
    limit: int = Query(default=6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    items = recommend_service.recommend_related(db, product_id, limit=limit)
    return [ProductListItem.model_validate(p) for p in items]
