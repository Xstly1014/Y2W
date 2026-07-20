"""ORM models for the e-commerce platform.

Design notes:
  * All tables use surrogate BIGINT PKs (`id`) for stable joins and sharding.
  * `created_at` / `updated_at` on every mutable table for audit / debug.
  * Money stored as `Numeric(12, 2)` (NOT float) to avoid rounding errors.
  * Inventory tracked at SKU level — products have one or more SKUs
    (e.g. size/color variants) and stock is reserved per SKU.
  * Orders snapshot the product name / price / image at purchase time so
    historical orders remain correct even if the product later changes.
  * No auth tables (per requirements). `users` is a thin profile row keyed
    by a client-generated user_id (stored in localStorage on the frontend).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String,
    Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ecommerce.db.base import Base


# --------------------------------------------------------------------------- #
# Catalog: categories, products, skus, images
# --------------------------------------------------------------------------- #
class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    icon: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    parent: Mapped[Optional["Category"]] = relationship(
        "Category", remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship("Category", back_populates="parent")
    products: Mapped[list["Product"]] = relationship("Product", back_populates="category")

    __table_args__ = (Index("ix_categories_parent_active", "parent_id", "is_active"),)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    spu_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="external product code")
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    subtitle: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Main image shown in list view. Detail view pulls ProductImage rows.
    main_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Price range across SKUs — denormalised for list-view performance.
    price_min: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    price_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    # Original price for showing discount badge (optional).
    original_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    tags: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, comment="comma-separated tags")
    # Sales / rating denormalised for sort performance.
    sales_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rating_avg: Mapped[Decimal] = mapped_column(Numeric(2, 1), default=Decimal("4.5"), nullable=False)
    rating_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    category: Mapped[Optional["Category"]] = relationship("Category", back_populates="products")
    skus: Mapped[list["ProductSKU"]] = relationship("ProductSKU", back_populates="product", cascade="all, delete-orphan")
    images: Mapped[list["ProductImage"]] = relationship(
        "ProductImage", back_populates="product", cascade="all, delete-orphan", order_by="ProductImage.sort_order"
    )
    reviews: Mapped[list["ProductReview"]] = relationship("ProductReview", back_populates="product")

    __table_args__ = (
        Index("ix_products_category_published", "category_id", "is_published"),
        Index("ix_products_sales", "sales_count"),
        Index("ix_products_rating", "rating_avg"),
    )


class ProductSKU(Base):
    """Stock-keeping unit — a specific variant (size/color combo) of a product."""

    __tablename__ = "product_skus"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # JSON-ish spec string, e.g. "color:red;size:L". Kept simple to avoid
    # bringing in JSONB column dependencies for the demo.
    spec: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Soft-reserved by carts/orders not yet paid. Available = stock - reserved.
    reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="skus")
    inventory_logs: Mapped[list["InventoryLog"]] = relationship("InventoryLog", back_populates="sku")

    __table_args__ = (
        UniqueConstraint("product_id", "spec", name="uq_skus_product_spec"),
    )


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="images")


class ProductReview(Base):
    __tablename__ = "product_reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False, comment="1-5")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    images: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True, comment="comma-separated urls")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="reviews")

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", "order_id", name="uq_review_user_product_order"),
    )


class InventoryLog(Base):
    """Audit trail of every stock change (purchase / restock / cancel)."""

    __tablename__ = "inventory_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("product_skus.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False, comment="positive=restock, negative=sold")
    reason: Mapped[str] = mapped_column(String(32), nullable=False, comment="restock|order|cancel|reserve|release")
    ref_order_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sku: Mapped["ProductSKU"] = relationship("ProductSKU", back_populates="inventory_logs")


# --------------------------------------------------------------------------- #
# User (no auth — just a profile row keyed by client-generated id)
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="client-generated uuid")
    nickname: Mapped[str] = mapped_column(String(64), default="Guest", nullable=False)
    avatar: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    addresses: Mapped[list["UserAddress"]] = relationship(
        "UserAddress", back_populates="user", cascade="all, delete-orphan"
    )
    cart_items: Mapped[list["CartItem"]] = relationship(
        "CartItem", back_populates="user", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user")


class UserAddress(Base):
    __tablename__ = "user_addresses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    province: Mapped[str] = mapped_column(String(32), nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    district: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(String(256), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", back_populates="addresses")


# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #
class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("product_skus.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    selected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", back_populates="cart_items")
    sku: Mapped["ProductSKU"] = relationship("ProductSKU")

    __table_args__ = (
        UniqueConstraint("user_id", "sku_id", name="uq_cart_user_sku"),
    )


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
class Order(Base):
    __tablename__ = "orders"

    # Status values — kept as string for readability (vs enum migration pain).
    STATUS_PENDING_PAYMENT = "pending_payment"
    STATUS_PAID = "paid"
    STATUS_SHIPPED = "shipped"
    STATUS_DELIVERED = "delivered"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUNDED = "refunded"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_PENDING_PAYMENT, index=True)

    # Snapshot of address at order time (so address edits don't break history).
    recipient: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    address_line: Mapped[str] = mapped_column(String(512), nullable=False)

    items_subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    # Payment-related
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    payment_txn_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Shipping
    tracking_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    remark: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship("User", back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    payment: Mapped[Optional["Payment"]] = relationship("Payment", back_populates="order", uselist=False)

    __table_args__ = (
        Index("ix_orders_user_status", "user_id", "status"),
        Index("ix_orders_created", "created_at"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # Snapshots — DO NOT change when product/sku changes.
    product_title: Mapped[str] = mapped_column(String(256), nullable=False)
    sku_spec: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    product_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped["Order"] = relationship("Order", back_populates="items")


# --------------------------------------------------------------------------- #
# Payment
# --------------------------------------------------------------------------- #
class Payment(Base):
    __tablename__ = "payments"

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_REFUNDED = "refunded"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    txn_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False, comment="alipay|wechat|card|balance")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Mock payment provider's raw response (for debugging).
    provider_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    order: Mapped["Order"] = relationship("Order", back_populates="payment")


# --------------------------------------------------------------------------- #
# Recommendations support — browsing history & coupons
# --------------------------------------------------------------------------- #
class BrowsingHistory(Base):
    """Tracks user product views for "看了又看" / "猜你喜欢" recommendations."""

    __tablename__ = "browsing_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_browsing_user_product", "user_id", "product_id"),
        Index("ix_browsing_viewed", "viewed_at"),
    )


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="fixed|percent")
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_order_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
