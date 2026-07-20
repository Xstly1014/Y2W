"""Schemas for cart, orders, payments, user."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# User & Address
# --------------------------------------------------------------------------- #
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    nickname: str
    avatar: Optional[str] = None
    phone: Optional[str] = None


class UserEnsureIn(BaseModel):
    """Body of POST /users/ensure — create-if-not-exists."""

    user_id: str = Field(min_length=1, max_length=64)
    nickname: Optional[str] = Field(default="Guest", max_length=64)
    avatar: Optional[str] = None


class AddressIn(BaseModel):
    recipient: str = Field(min_length=1, max_length=64)
    phone: str = Field(min_length=4, max_length=32)
    province: str = Field(min_length=1, max_length=32)
    city: str = Field(min_length=1, max_length=32)
    district: str = Field(min_length=1, max_length=32)
    detail: str = Field(min_length=1, max_length=256)
    is_default: bool = False


class AddressOut(AddressIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: str


# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #
class CartItemIn(BaseModel):
    sku_id: int
    quantity: int = Field(default=1, ge=1, le=99)
    selected: bool = True


class CartItemUpdate(BaseModel):
    quantity: Optional[int] = Field(default=None, ge=1, le=99)
    selected: Optional[bool] = None


class CartItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: str
    sku_id: int
    quantity: int
    selected: bool
    # Joined fields (filled by service layer):
    product_id: Optional[int] = None
    product_title: Optional[str] = None
    product_image: Optional[str] = None
    sku_spec: Optional[str] = None
    sku_price: Optional[Decimal] = None
    available_stock: Optional[int] = None


class CartSummary(BaseModel):
    items: list[CartItemOut]
    selected_count: int
    selected_quantity: int
    selected_subtotal: Decimal
    total_quantity: int


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
class OrderCreateItem(BaseModel):
    sku_id: int
    quantity: int = Field(ge=1, le=99)


class OrderCreateIn(BaseModel):
    """Create order from selected cart items OR from explicit items list."""

    address_id: Optional[int] = None
    items: Optional[list[OrderCreateItem]] = Field(
        default=None, description="If omitted, uses all selected cart items."
    )
    coupon_code: Optional[str] = None
    remark: Optional[str] = Field(default=None, max_length=256)
    # If address_id omitted, these inline fields are used:
    recipient: Optional[str] = None
    phone: Optional[str] = None
    address_line: Optional[str] = None


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    sku_id: int
    product_title: str
    sku_spec: Optional[str] = None
    product_image: Optional[str] = None
    unit_price: Decimal
    quantity: int
    subtotal: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    user_id: str
    status: str
    recipient: str
    phone: str
    address_line: str
    items_subtotal: Decimal
    shipping_fee: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    paid_at: Optional[datetime] = None
    payment_method: Optional[str] = None
    payment_txn_id: Optional[str] = None
    tracking_no: Optional[str] = None
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    remark: Optional[str] = None
    created_at: datetime
    items: list[OrderItemOut] = Field(default_factory=list)


class OrderListResponse(BaseModel):
    items: list[OrderOut]
    total: int
    page: int
    page_size: int


class OrderStatusUpdate(BaseModel):
    """Used by POST /orders/{id}/cancel and similar status transitions."""

    reason: Optional[str] = Field(default=None, max_length=256)


# --------------------------------------------------------------------------- #
# Payment
# --------------------------------------------------------------------------- #
class PaymentCreateIn(BaseModel):
    # order_id is taken from the URL path; kept here for service-layer reuse.
    order_id: Optional[int] = None
    method: str = Field(default="alipay", description="alipay|wechat|card|balance")


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_id: int
    txn_id: str
    amount: Decimal
    currency: str
    method: str
    status: str
    paid_at: Optional[datetime] = None
    created_at: datetime


class PaymentResult(BaseModel):
    """Returned after POST /payments — simulates a payment provider response."""

    payment: PaymentOut
    order_status: str
    success: bool
    message: str


# --------------------------------------------------------------------------- #
# Review
# --------------------------------------------------------------------------- #
class ReviewCreateIn(BaseModel):
    product_id: int
    order_id: Optional[int] = None
    rating: int = Field(ge=1, le=5)
    content: Optional[str] = Field(default=None, max_length=1024)


# --------------------------------------------------------------------------- #
# Recommendation
# --------------------------------------------------------------------------- #
class RecommendationResponse(BaseModel):
    basis: str = Field(description="history|category|hot|related")
    items: list  # list[ProductListItem] but kept dynamic to avoid circular import
