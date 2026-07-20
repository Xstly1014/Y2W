"""Cart routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ecommerce.routes.deps import get_db, get_user_id
from ecommerce.schemas.order import CartItemIn, CartItemOut, CartItemUpdate, CartSummary
from ecommerce.services import cart_service

router = APIRouter(prefix="/cart", tags=["cart"])


@router.get("", response_model=CartSummary)
def get_cart(db: Session = Depends(get_db), user_id: str = Depends(get_user_id)):
    return cart_service.list_cart(db, user_id)


@router.post("/items", response_model=CartItemOut, status_code=status.HTTP_201_CREATED)
def add_item(
    item: CartItemIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    try:
        ci = cart_service.add_to_cart(db, user_id, item)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    # Re-fetch with joins for the response.
    summary = cart_service.list_cart(db, user_id)
    for out in summary.items:
        if out.id == ci.id:
            return out
    raise HTTPException(status_code=500, detail="Cart item disappeared after insert")


@router.patch("/items/{cart_item_id}", response_model=CartItemOut)
def update_item(
    cart_item_id: int,
    update: CartItemUpdate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    try:
        cart_service.update_cart_item(db, user_id, cart_item_id, update)
        db.commit()
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    summary = cart_service.list_cart(db, user_id)
    for out in summary.items:
        if out.id == cart_item_id:
            return out
    raise HTTPException(status_code=404, detail="Cart item not found after update")


@router.delete("/items/{cart_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(
    cart_item_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    try:
        cart_service.remove_cart_item(db, user_id, cart_item_id)
        db.commit()
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def clear_cart(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    cart_service.clear_cart(db, user_id)
    db.commit()
