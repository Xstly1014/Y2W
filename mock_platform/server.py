"""Mock e-commerce platform server.

Standalone FastAPI app emulating Shopify/Shopee for demo purposes.
Listens on settings.mock_platform_port (default 8001).

Endpoints:
  GET  /health
  GET  /orders/{order_id}
  GET  /orders?customer=<email>
  GET  /logistics/{tracking_no}
  POST /refunds            body: {order_id, reason, amount_usd?}
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from config import settings
from mock_platform.data import get_tenant_state

logger = logging.getLogger(__name__)

app = FastAPI(title="Mock E-commerce Platform", version="0.1.0")

# Per-tenant locks so concurrent refund requests for the same tenant are
# serialised. Without this, two simultaneous POST /refunds could both read
# `order["refundable"] == True` and both succeed.
#
# Bounded LRU so a long-running service doesn't leak one Lock per tenant
# forever (10K tenants ≈ 800KB). See P3-6.
_TENANT_LOCKS: OrderedDict[str, Lock] = OrderedDict()
_TENANT_LOCKS_MAX = 1024
_TENANT_LOCKS_GUARD = Lock()


def _tenant_lock(tenant_id: str) -> Lock:
    """Return (or lazily create) the mutex guarding a tenant's mutable state."""
    with _TENANT_LOCKS_GUARD:
        lock = _TENANT_LOCKS.get(tenant_id)
        if lock is None:
            lock = Lock()
            _TENANT_LOCKS[tenant_id] = lock
            # Evict oldest entry if the cache is full.
            while len(_TENANT_LOCKS) > _TENANT_LOCKS_MAX:
                _TENANT_LOCKS.popitem(last=False)
        else:
            # Mark as recently used.
            _TENANT_LOCKS.move_to_end(tenant_id)
        return lock


class RefundRequest(BaseModel):
    order_id: str
    reason: str
    amount_usd: float | None = Field(default=None, description="Defaults to full order total")


def _tenant(x_tenant_id: str | None) -> str:
    return x_tenant_id or settings.default_tenant_id

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock_platform"}


@app.get("/orders/{order_id}")
def get_order(order_id: str, x_tenant_id: str | None = Header(default=None)) -> dict[str, Any]:
    state = get_tenant_state(_tenant(x_tenant_id))
    for o in state["orders"]:
        if o["order_id"] == order_id:
            return dict(o)  # return a shallow copy so callers can't mutate state
    raise HTTPException(status_code=404, detail=f"order {order_id} not found")


@app.get("/orders")
def list_orders(
    customer: str | None = Query(default=None, description="customer email"),
    x_tenant_id: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    state = get_tenant_state(_tenant(x_tenant_id))
    orders = state["orders"]
    if customer:
        orders = [o for o in orders if o["customer_email"] == customer]
    # Return shallow copies so external callers cannot mutate internal state.
    return [dict(o) for o in orders]

@app.get("/logistics/{tracking_no}")
def get_logistics(
    tracking_no: str, x_tenant_id: str | None = Header(default=None)
) -> dict[str, Any]:
    state = get_tenant_state(_tenant(x_tenant_id))
    timeline = state["logistics"].get(tracking_no)
    if not timeline:
        raise HTTPException(status_code=404, detail=f"tracking {tracking_no} not found")
    return {"tracking_no": tracking_no, "timeline": list(timeline)}


@app.post("/refunds")
def create_refund(
    req: RefundRequest, x_tenant_id: str | None = Header(default=None)
) -> dict[str, Any]:
    tenant_id = _tenant(x_tenant_id)
    # Serialise per-tenant writes to prevent double-refund races.
    with _tenant_lock(tenant_id):
        state = get_tenant_state(tenant_id)
        # Find the order.
        order = next((o for o in state["orders"] if o["order_id"] == req.order_id), None)
        if not order:
            raise HTTPException(status_code=404, detail=f"order {req.order_id} not found")
        if not order.get("refundable"):
            raise HTTPException(status_code=400, detail=f"order {req.order_id} is not refundable")
        if req.reason not in order.get("refund_reason_allowed", []):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"reason '{req.reason}' not allowed for order {req.order_id}; "
                    f"allowed: {order['refund_reason_allowed']}"
                ),
            )

        amount = req.amount_usd if req.amount_usd is not None else order["total_usd"]
        # Validate refund amount: must be positive and cannot exceed order total.
        if amount <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"refund amount must be positive, got {amount}",
            )
        if amount > order["total_usd"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"refund amount {amount} exceeds order total "
                    f"{order['total_usd']}"
                ),
            )

        # Use uuid4 hex for globally-unique refund id (len()-based ids collide
        # if refunds are ever removed and re-created).
        refund_id = f"RF-{req.order_id}-{uuid4().hex[:10].upper()}"
        refund_record = {
            "refund_id": refund_id,
            "order_id": req.order_id,
            "reason": req.reason,
            "amount_usd": round(amount, 2),
            "currency": order["currency"],
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "processed",
        }
        state["refunds"].append(refund_record)
        # Mark the order as refunded.
        order["status"] = "refunded"
        order["refundable"] = False
        logger.info("refund created: %s for order %s amount=%.2f", refund_id, req.order_id, amount)
        return dict(refund_record)


@app.get("/refunds")
def list_refunds(x_tenant_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    state = get_tenant_state(_tenant(x_tenant_id))
    # Return shallow copies so external callers cannot mutate internal state.
    return [dict(r) for r in state["refunds"]]


def main() -> None:
    """Run the mock platform server (for `python -m mock_platform.server`)."""
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.mock_platform_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
