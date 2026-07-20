"""Order + inventory + payment services.

The order creation flow is the most critical in the platform:

    1. Validate address (inline or by address_id).
    2. Collect order items (from selected cart OR explicit list).
    3. For each item: lock SKU row (SELECT ... FOR UPDATE), check stock,
       reserve stock (increment `reserved`).
    4. Compute totals (items_subtotal + shipping_fee - discount_amount).
    5. Apply coupon if provided.
    6. Insert Order + OrderItem rows (with price/title snapshots).
    7. Remove items from cart (if sourced from cart).
    8. Commit transaction.

Stock reservation holds until payment succeeds (then deducts) or order
cancelled (then releases). This prevents overselling under concurrency.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ecommerce.config import settings
from ecommerce.db.models import (
    CartItem, Coupon, InventoryLog, Order, OrderItem, Payment, Product,
    ProductSKU, UserAddress,
)
from ecommerce.schemas.order import (
    OrderCreateIn, OrderItemOut, OrderOut, OrderStatusUpdate, PaymentCreateIn,
    PaymentOut, PaymentResult,
)
from ecommerce.services.cart_service import clear_cart

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _gen_order_no() -> str:
    """20-char order number: 'EC' + YYYYMMDD + 12-hex-uuid4."""
    now = datetime.now(timezone.utc)
    return f"EC{now.strftime('%Y%m%d')}{uuid.uuid4().hex[:12].upper()}"


def _gen_txn_id() -> str:
    return f"TX{uuid.uuid4().hex[:16].upper()}"


def _resolve_address(db: Session, user_id: str, req: OrderCreateIn) -> tuple[str, str, str]:
    """Return (recipient, phone, address_line). Raises ValueError if missing."""
    if req.address_id:
        addr = db.get(UserAddress, req.address_id)
        if addr is None or addr.user_id != user_id:
            raise ValueError(f"Address {req.address_id} not found for user")
        line = f"{addr.province}{addr.city}{addr.district}{addr.detail}"
        return addr.recipient, addr.phone, line
    if req.recipient and req.phone and req.address_line:
        return req.recipient, req.phone, req.address_line
    # Fall back to user's default address.
    stmt = (
        select(UserAddress)
        .where(UserAddress.user_id == user_id)
        .order_by(UserAddress.is_default.desc(), UserAddress.id.desc())
    )
    addr = db.scalars(stmt).first()
    if addr is None:
        raise ValueError("No address provided and no default address on file")
    line = f"{addr.province}{addr.city}{addr.district}{addr.detail}"
    return addr.recipient, addr.phone, line


def _collect_items(db: Session, user_id: str, req: OrderCreateIn) -> list[tuple[ProductSKU, Product, int]]:
    """Resolve order line items to (sku, product, quantity) tuples."""
    result: list[tuple[ProductSKU, Product, int]] = []

    if req.items:
        for it in req.items:
            sku = db.get(ProductSKU, it.sku_id)
            if sku is None or not sku.is_active:
                raise ValueError(f"SKU {it.sku_id} not found or inactive")
            prod = db.get(Product, sku.product_id)
            if prod is None or not prod.is_published:
                raise ValueError(f"Product for SKU {it.sku_id} not available")
            if it.quantity < 1 or it.quantity > 99:
                raise ValueError(f"Invalid quantity {it.quantity} for SKU {it.sku_id}")
            result.append((sku, prod, it.quantity))
    else:
        # Use all selected cart items.
        stmt = (
            select(CartItem, ProductSKU, Product)
            .join(ProductSKU, CartItem.sku_id == ProductSKU.id)
            .join(Product, ProductSKU.product_id == Product.id)
            .where(CartItem.user_id == user_id, CartItem.selected.is_(True))
        )
        rows = list(db.execute(stmt))
        if not rows:
            raise ValueError("No selected cart items to order")
        for ci, sku, prod in rows:
            result.append((sku, prod, ci.quantity))

    if len(result) > settings.order_max_items:
        raise ValueError(f"Order exceeds max items ({settings.order_max_items})")
    return result


def _compute_shipping(items_subtotal: Decimal) -> Decimal:
    if items_subtotal >= Decimal(str(settings.free_shipping_threshold)):
        return Decimal("0")
    return Decimal(str(settings.default_shipping_fee))


def _apply_coupon(db: Session, code: Optional[str], items_subtotal: Decimal) -> Decimal:
    """Return discount amount. 0 if no coupon or invalid."""
    if not code:
        return Decimal("0")
    stmt = select(Coupon).where(Coupon.code == code, Coupon.is_active.is_(True))
    coupon = db.scalars(stmt).first()
    if coupon is None:
        raise ValueError(f"Coupon {code} not found or inactive")
    if items_subtotal < coupon.min_order_amount:
        raise ValueError(
            f"Coupon requires min order amount {coupon.min_order_amount}, "
            f"current subtotal {items_subtotal}"
        )
    if coupon.discount_type == "fixed":
        return min(coupon.discount_value, items_subtotal)
    elif coupon.discount_type == "percent":
        return (items_subtotal * coupon.discount_value / Decimal("100")).quantize(Decimal("0.01"))
    raise ValueError(f"Unknown coupon discount_type {coupon.discount_type}")


# --------------------------------------------------------------------------- #
# Order creation
# --------------------------------------------------------------------------- #
def create_order(db: Session, user_id: str, req: OrderCreateIn) -> Order:
    """Create an order with stock reservation. Atomic — rolls back on any error."""
    recipient, phone, address_line = _resolve_address(db, user_id, req)
    items = _collect_items(db, user_id, req)

    # Reserve stock for each SKU under row-level lock (SELECT ... FOR UPDATE).
    # This blocks concurrent create_order on the same SKU until the current
    # transaction commits or rolls back, preventing overselling. See
    # `optimization_logs/2026-07-21/second-review.md` P0-5.
    items_subtotal = Decimal("0")
    order_items: list[tuple[ProductSKU, Product, int, Decimal]] = []
    for sku, prod, qty in items:
        # Re-fetch the SKU with FOR UPDATE to acquire a row lock.
        locked_sku = db.execute(
            select(ProductSKU).where(ProductSKU.id == sku.id).with_for_update()
        ).scalar_one_or_none()
        if locked_sku is None:
            raise ValueError(f"SKU {sku.sku_code} disappeared during checkout")
        sku = locked_sku
        available = sku.stock - sku.reserved
        if qty > available:
            raise ValueError(
                f"Insufficient stock for SKU {sku.sku_code}: requested {qty}, available {available}"
            )
        sku.reserved += qty  # reserve (not deduct yet — wait for payment)
        line_subtotal = (sku.price * qty).quantize(Decimal("0.01"))
        items_subtotal += line_subtotal
        order_items.append((sku, prod, qty, line_subtotal))

    shipping_fee = _compute_shipping(items_subtotal)
    discount = _apply_coupon(db, req.coupon_code, items_subtotal)
    total = (items_subtotal + shipping_fee - discount).quantize(Decimal("0.01"))
    if total < 0:
        total = Decimal("0")

    order = Order(
        order_no=_gen_order_no(),
        user_id=user_id,
        status=Order.STATUS_PENDING_PAYMENT,
        recipient=recipient, phone=phone, address_line=address_line,
        items_subtotal=items_subtotal,
        shipping_fee=shipping_fee,
        discount_amount=discount,
        total_amount=total,
        remark=req.remark,
    )
    db.add(order)
    db.flush()  # populate order.id

    for sku, prod, qty, subtotal in order_items:
        db.add(OrderItem(
            order_id=order.id,
            product_id=prod.id, sku_id=sku.id,
            product_title=prod.title, sku_spec=sku.spec,
            product_image=prod.main_image,
            unit_price=sku.price, quantity=qty, subtotal=subtotal,
        ))
        # Inventory log for audit.
        db.add(InventoryLog(
            sku_id=sku.id, delta=-qty, reason="reserve", ref_order_id=order.id,
        ))
        # Increment product sales_count (will revert if cancelled).
        prod.sales_count += qty

    # If items came from cart, clear selected items.
    if not req.items:
        # Clear only selected cart items (the ones we just ordered).
        stmt = select(CartItem).where(CartItem.user_id == user_id, CartItem.selected.is_(True))
        for ci in db.scalars(stmt):
            db.delete(ci)

    db.flush()
    return order


# --------------------------------------------------------------------------- #
# Order queries
# --------------------------------------------------------------------------- #
def get_order(db: Session, order_id: int, user_id: Optional[str] = None) -> Optional[Order]:
    order = db.get(Order, order_id)
    if order is None:
        return None
    if user_id is not None and order.user_id != user_id:
        return None
    return order


def list_orders(db: Session, user_id: str, status: Optional[str] = None,
                page: int = 1, page_size: int = 20) -> tuple[list[Order], int]:
    stmt = select(Order).where(Order.user_id == user_id)
    count_stmt = select(Order.id).where(Order.user_id == user_id)
    if status:
        stmt = stmt.where(Order.status == status)
        count_stmt = count_stmt.where(Order.status == status)
    total = len(list(db.scalars(count_stmt)))
    offset = (page - 1) * page_size
    stmt = stmt.order_by(Order.created_at.desc()).offset(offset).limit(page_size)
    return list(db.scalars(stmt)), total


def cancel_order(db: Session, order_id: int, user_id: str,
                 reason: Optional[str] = None) -> Order:
    """Cancel a pending order and release reserved stock."""
    order = get_order(db, order_id, user_id)
    if order is None:
        raise LookupError(f"Order {order_id} not found")
    if order.status not in (Order.STATUS_PENDING_PAYMENT, Order.STATUS_PAID):
        raise ValueError(f"Cannot cancel order in status {order.status}")

    was_paid = order.status == Order.STATUS_PAID

    # Release reserved stock for each item.
    for it in order.items:
        sku = db.get(ProductSKU, it.sku_id)
        if sku is None:
            continue
        if was_paid:
            # Already deducted at payment time — restock.
            sku.stock += it.quantity
            db.add(InventoryLog(
                sku_id=sku.id, delta=it.quantity, reason="cancel",
                ref_order_id=order.id,
            ))
        else:
            # Was only reserved — release.
            sku.reserved = max(0, sku.reserved - it.quantity)
            db.add(InventoryLog(
                sku_id=sku.id, delta=it.quantity, reason="release",
                ref_order_id=order.id,
            ))
        # Revert sales_count.
        prod = db.get(Product, it.product_id)
        if prod:
            prod.sales_count = max(0, prod.sales_count - it.quantity)

    order.status = Order.STATUS_CANCELLED
    order.remark = (order.remark or "") + (f" | Cancelled: {reason}" if reason else "")
    db.flush()
    return order


# --------------------------------------------------------------------------- #
# Payment (mock provider)
# --------------------------------------------------------------------------- #
def create_payment(db: Session, user_id: str, req: PaymentCreateIn) -> PaymentResult:
    """Simulate a payment provider authorising a transaction.

    Always succeeds for demo purposes (the brief explicitly asks for
    "payment flow simulation"). The provider payload is stored for debugging.
    """
    order = get_order(db, req.order_id, user_id=user_id)
    if order is None:
        raise LookupError(f"Order {req.order_id} not found for user {user_id}")
    if order.status != Order.STATUS_PENDING_PAYMENT:
        raise ValueError(f"Order {req.order_id} is not pending payment (status={order.status})")

    txn_id = _gen_txn_id()
    payment = Payment(
        order_id=order.id, txn_id=txn_id,
        amount=order.total_amount, currency=settings.payment_currency,
        method=req.method, status=Payment.STATUS_PENDING,
    )
    db.add(payment)
    db.flush()

    # Simulate async provider callback — in a real system this would be a
    # webhook from Alipay/WeChat. We just inline-success it.
    payment.status = Payment.STATUS_SUCCESS
    payment.paid_at = datetime.now(timezone.utc)
    payment.provider_payload = (
        f'{{"txn_id":"{txn_id}","amount":"{payment.amount}",'
        f'"method":"{req.method}","result":"success"}}'
    )

    # Transition order to PAID and deduct reserved stock.
    order.status = Order.STATUS_PAID
    order.paid_at = payment.paid_at
    order.payment_method = req.method
    order.payment_txn_id = txn_id

    for it in order.items:
        sku = db.get(ProductSKU, it.sku_id)
        if sku is None:
            continue
        # Deduct from stock AND release the reservation.
        sku.stock = max(0, sku.stock - it.quantity)
        sku.reserved = max(0, sku.reserved - it.quantity)
        db.add(InventoryLog(
            sku_id=sku.id, delta=-it.quantity, reason="order",
            ref_order_id=order.id,
        ))

    db.flush()
    return PaymentResult(
        payment=PaymentOut.model_validate(payment),
        order_status=order.status,
        success=True,
        message="Payment succeeded (simulated)",
    )


# --------------------------------------------------------------------------- #
# Auto-ship paid orders (for demo — would be a separate worker in prod)
# --------------------------------------------------------------------------- #
def ship_paid_orders(db: Session) -> int:
    """Mark all PAID orders as SHIPPED and assign a mock tracking number.
    Returns count shipped. Called by a periodic background task."""
    stmt = select(Order).where(Order.status == Order.STATUS_PAID)
    count = 0
    for order in db.scalars(stmt):
        order.status = Order.STATUS_SHIPPED
        order.tracking_no = f"SF{uuid.uuid4().hex[:14].upper()}"
        order.shipped_at = datetime.now(timezone.utc)
        count += 1
    if count:
        db.flush()
    return count


def deliver_shipped_orders(db: Session) -> int:
    """Auto-deliver shipped orders older than 60s (simulates logistics)."""
    stmt = select(Order).where(Order.status == Order.STATUS_SHIPPED)
    now = datetime.now(timezone.utc)
    count = 0
    for order in db.scalars(stmt):
        if order.shipped_at and (now - order.shipped_at).total_seconds() > 60:
            order.status = Order.STATUS_DELIVERED
            order.delivered_at = now
            count += 1
    if count:
        db.flush()
    return count


def complete_delivered_orders(db: Session) -> int:
    """Auto-complete delivered orders older than 5 minutes."""
    stmt = select(Order).where(Order.status == Order.STATUS_DELIVERED)
    now = datetime.now(timezone.utc)
    count = 0
    for order in db.scalars(stmt):
        if order.delivered_at and (now - order.delivered_at).total_seconds() > 300:
            order.status = Order.STATUS_COMPLETED
            count += 1
    if count:
        db.flush()
    return count
