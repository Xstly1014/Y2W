"""Cart service — add / update / remove / list with live product join."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ecommerce.config import settings
from ecommerce.db.models import CartItem, Product, ProductSKU, User
from ecommerce.schemas.order import CartItemIn, CartItemOut, CartItemUpdate, CartSummary

logger = logging.getLogger(__name__)


def ensure_user(db: Session, user_id: str, nickname: str = "Guest", avatar: Optional[str] = None) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, nickname=nickname, avatar=avatar)
        db.add(user)
        db.flush()
    return user


def _enrich(item: CartItem, sku: ProductSKU, product: Product) -> CartItemOut:
    return CartItemOut(
        id=item.id, user_id=item.user_id, sku_id=item.sku_id,
        quantity=item.quantity, selected=item.selected,
        product_id=product.id, product_title=product.title,
        product_image=product.main_image, sku_spec=sku.spec,
        sku_price=sku.price,
        available_stock=max(0, sku.stock - sku.reserved),
    )


def list_cart(db: Session, user_id: str) -> CartSummary:
    stmt = (
        select(CartItem, ProductSKU, Product)
        .join(ProductSKU, CartItem.sku_id == ProductSKU.id)
        .join(Product, ProductSKU.product_id == Product.id)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.updated_at.desc())
    )
    items: list[CartItemOut] = []
    for ci, sku, prod in db.execute(stmt):
        items.append(_enrich(ci, sku, prod))

    selected = [i for i in items if i.selected]
    return CartSummary(
        items=items,
        selected_count=len(selected),
        selected_quantity=sum(i.quantity for i in selected),
        selected_subtotal=sum(i.sku_price * i.quantity for i in selected if i.sku_price),  # type: ignore[arg-type]
        total_quantity=sum(i.quantity for i in items),
    )


def add_to_cart(db: Session, user_id: str, item: CartItemIn) -> CartItem:
    ensure_user(db, user_id)

    # Verify SKU exists and has stock.
    sku = db.get(ProductSKU, item.sku_id)
    if sku is None or not sku.is_active:
        raise ValueError(f"SKU {item.sku_id} not found or inactive")
    product = db.get(Product, sku.product_id)
    if product is None or not product.is_published:
        raise ValueError(f"Product for SKU {item.sku_id} not available")

    available = sku.stock - sku.reserved
    if item.quantity > available:
        raise ValueError(f"Insufficient stock for SKU {item.sku_id}: requested {item.quantity}, available {available}")

    # Upsert: if (user_id, sku_id) already in cart, increment quantity.
    stmt = select(CartItem).where(CartItem.user_id == user_id, CartItem.sku_id == item.sku_id)
    existing = db.scalars(stmt).first()
    if existing:
        new_qty = existing.quantity + item.quantity
        if new_qty > available:
            raise ValueError(f"Combined quantity {new_qty} exceeds stock {available}")
        if new_qty > 99:
            raise ValueError("Quantity per cart line cannot exceed 99")
        existing.quantity = new_qty
        existing.selected = item.selected
        db.flush()
        return existing

    # Enforce cart_max_items limit.
    count_stmt = select(CartItem).where(CartItem.user_id == user_id)
    if len(list(db.scalars(count_stmt))) >= settings.cart_max_items:
        raise ValueError(f"Cart cannot exceed {settings.cart_max_items} items")

    ci = CartItem(
        user_id=user_id, sku_id=item.sku_id,
        quantity=item.quantity, selected=item.selected,
    )
    db.add(ci)
    db.flush()
    return ci


def update_cart_item(db: Session, user_id: str, cart_item_id: int,
                     update: CartItemUpdate) -> CartItem:
    ci = db.get(CartItem, cart_item_id)
    if ci is None or ci.user_id != user_id:
        raise LookupError(f"Cart item {cart_item_id} not found for user {user_id}")

    if update.quantity is not None:
        sku = db.get(ProductSKU, ci.sku_id)
        if sku is None:
            raise ValueError("SKU missing")
        available = sku.stock - sku.reserved
        if update.quantity > available:
            raise ValueError(f"Quantity {update.quantity} exceeds available {available}")
        ci.quantity = update.quantity
    if update.selected is not None:
        ci.selected = update.selected
    db.flush()
    return ci


def remove_cart_item(db: Session, user_id: str, cart_item_id: int) -> None:
    ci = db.get(CartItem, cart_item_id)
    if ci is None or ci.user_id != user_id:
        raise LookupError(f"Cart item {cart_item_id} not found for user {user_id}")
    db.delete(ci)
    db.flush()


def clear_cart(db: Session, user_id: str) -> int:
    """Remove all cart items for a user. Returns count deleted."""
    stmt = select(CartItem).where(CartItem.user_id == user_id)
    items = list(db.scalars(stmt))
    for ci in items:
        db.delete(ci)
    db.flush()
    return len(items)
