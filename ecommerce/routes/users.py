"""User profile & address routes (no auth)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ecommerce.db.models import User, UserAddress
from ecommerce.routes.deps import get_db, get_user_id
from ecommerce.schemas.order import AddressIn, AddressOut, UserEnsureIn, UserOut
from ecommerce.services.cart_service import ensure_user

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/ensure", response_model=UserOut)
def ensure_user_endpoint(
    body: UserEnsureIn,
    db: Session = Depends(get_db),
):
    user = ensure_user(db, body.user_id, body.nickname, body.avatar)
    db.commit()
    return UserOut.model_validate(user)


@router.get("/me", response_model=UserOut)
def get_me(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    user = db.get(User, user_id)
    if user is None:
        # Auto-create on first request so the frontend has a row to work with.
        user = ensure_user(db, user_id)
        db.commit()
    return UserOut.model_validate(user)


@router.get("/addresses", response_model=list[AddressOut])
def list_addresses(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    from sqlalchemy import select
    stmt = (
        select(UserAddress)
        .where(UserAddress.user_id == user_id)
        .order_by(UserAddress.is_default.desc(), UserAddress.id.desc())
    )
    return list(db.scalars(stmt))


@router.post("/addresses", response_model=AddressOut, status_code=status.HTTP_201_CREATED)
def add_address(
    body: AddressIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    ensure_user(db, user_id)
    if body.is_default:
        # Unset other defaults.
        from sqlalchemy import select, update
        db.execute(
            update(UserAddress)
            .where(UserAddress.user_id == user_id, UserAddress.is_default.is_(True))
            .values(is_default=False)
        )
    addr = UserAddress(user_id=user_id, **body.model_dump())
    db.add(addr)
    db.commit()
    db.refresh(addr)
    return AddressOut.model_validate(addr)


@router.put("/addresses/{address_id}", response_model=AddressOut)
def update_address(
    address_id: int,
    body: AddressIn,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    addr = db.get(UserAddress, address_id)
    if addr is None or addr.user_id != user_id:
        raise HTTPException(status_code=404, detail="Address not found")
    if body.is_default:
        from sqlalchemy import select, update
        db.execute(
            update(UserAddress)
            .where(
                UserAddress.user_id == user_id,
                UserAddress.is_default.is_(True),
                UserAddress.id != address_id,
            )
            .values(is_default=False)
        )
    for k, v in body.model_dump().items():
        setattr(addr, k, v)
    db.commit()
    db.refresh(addr)
    return AddressOut.model_validate(addr)


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: int,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_user_id),
):
    addr = db.get(UserAddress, address_id)
    if addr is None or addr.user_id != user_id:
        raise HTTPException(status_code=404, detail="Address not found")
    db.delete(addr)
    db.commit()
