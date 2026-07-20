"""Order & payment routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ecommerce.routes.deps import get_db, get_user_id
from ecommerce.schemas.order import (
    OrderCreateIn, OrderListResponse, OrderOut, OrderStatusUpdate,
    PaymentCreateIn, PaymentResult, ReviewCreateIn,
)
from ecommerce.services import order_service, product_service

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    body: OrderCreateIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    try:
        order = order_service.create_order(db, user_id, body)
        db.commit()
        db.refresh(order)
        return OrderOut.model_validate(order)
    except (ValueError, LookupError) as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("", response_model=OrderListResponse)
def list_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    items, total = order_service.list_orders(db, user_id, status=status_filter, page=page, page_size=page_size)
    return OrderListResponse(
        items=[OrderOut.model_validate(o) for o in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    order = order_service.get_order(db, order_id, user_id=user_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return OrderOut.model_validate(order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: int,
    body: OrderStatusUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    try:
        order = order_service.cancel_order(db, order_id, user_id, reason=body.reason)
        db.commit()
        db.refresh(order)
        return OrderOut.model_validate(order)
    except LookupError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# --------------------------------------------------------------------------- #
# Payment
# --------------------------------------------------------------------------- #
@router.post("/{order_id}/payment", response_model=PaymentResult)
def pay_order(
    order_id: int,
    body: PaymentCreateIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    """Simulate paying for an order. Body method overrides if provided."""
    req = PaymentCreateIn(order_id=order_id, method=body.method or "alipay")
    try:
        result = order_service.create_payment(db, user_id, req)
        db.commit()
        return result
    except LookupError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #
@router.post("/{order_id}/reviews", status_code=status.HTTP_201_CREATED)
def add_review(
    order_id: int,
    body: ReviewCreateIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    if body.product_id <= 0 or body.rating < 1 or body.rating > 5:
        raise HTTPException(status_code=400, detail="Invalid product_id or rating")
    try:
        review = product_service.create_review(
            db, user_id=user_id, product_id=body.product_id,
            rating=body.rating, content=body.content, order_id=order_id,
        )
        db.commit()
        return {"id": review.id, "ok": True}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
