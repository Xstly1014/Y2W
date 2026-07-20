"""Schemas for catalog (categories, products, skus, reviews)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: Optional[int] = None
    name: str
    slug: str
    icon: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True
    children: list["CategoryOut"] = Field(default_factory=list)


class SKUOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    spec: Optional[str] = None
    price: Decimal
    stock: int
    reserved: int = 0
    is_active: bool = True

    @property
    def available(self) -> int:
        return max(0, self.stock - self.reserved)


class ProductImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    sort_order: int = 0


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    user_id: str
    rating: int
    content: Optional[str] = None
    created_at: datetime


class ProductListItem(BaseModel):
    """Compact view used in list / search / category pages."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: Optional[int] = None
    title: str
    subtitle: Optional[str] = None
    main_image: Optional[str] = None
    price_min: Decimal
    price_max: Decimal
    original_price: Optional[Decimal] = None
    brand: Optional[str] = None
    sales_count: int = 0
    rating_avg: Decimal = Decimal("4.5")
    rating_count: int = 0


class ProductDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: Optional[int] = None
    spu_code: str
    title: str
    subtitle: Optional[str] = None
    description: Optional[str] = None
    main_image: Optional[str] = None
    price_min: Decimal
    price_max: Decimal
    original_price: Optional[Decimal] = None
    brand: Optional[str] = None
    tags: Optional[str] = None
    sales_count: int = 0
    rating_avg: Decimal
    rating_count: int = 0
    images: list[ProductImageOut] = Field(default_factory=list)
    skus: list[SKUOut] = Field(default_factory=list)
    reviews: list[ReviewOut] = Field(default_factory=list, max_length=5)


class ProductListResponse(BaseModel):
    items: list[ProductListItem]
    total: int
    page: int
    page_size: int
    has_more: bool


class SearchFilters(BaseModel):
    """Query parameters accepted by /products and /search."""

    keyword: Optional[str] = None
    category_id: Optional[int] = None
    brand: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    sort_by: str = Field(default="default", description="default|sales|price_asc|price_desc|rating|newest")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# Resolve forward refs
CategoryOut.model_rebuild()
ProductDetail.model_rebuild()
