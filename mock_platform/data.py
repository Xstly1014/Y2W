"""In-memory mock data for the e-commerce platform.

Seeded per-tenant on first access. Reset on process restart.
"""
from __future__ import annotations

from threading import Lock
from typing import Any

# Seed orders. Each tenant gets a deep copy so the demo can mutate state
# (e.g. mark as refunded) without affecting other tenants.
_SEED_ORDERS: list[dict[str, Any]] = [
    {
        "order_id": "1001",
        "customer_email": "alice@example.com",
        "customer_name": "Alice Wang",
        "items": [
            {"sku": "BGE-TSHIRT-001", "name": "Organic Cotton T-Shirt", "qty": 2, "price_usd": 19.99},
            {"sku": "BGE-SOCKS-002", "name": "Bamboo Fiber Socks", "qty": 1, "price_usd": 7.50},
        ],
        "total_usd": 47.48,
        "currency": "USD",
        "status": "shipped",
        "tracking_no": "TRACK-1001-US",
        "placed_at": "2026-07-10T08:23:00Z",
        "shipped_at": "2026-07-11T14:00:00Z",
        "refundable": True,
        "refund_reason_allowed": ["defective", "not_received", "wrong_item"],
    },
    {
        "order_id": "1002",
        "customer_email": "bob@example.com",
        "customer_name": "Bob Chen",
        "items": [
            {"sku": "BGE-MUG-003", "name": "Ceramic Coffee Mug", "qty": 1, "price_usd": 12.00},
        ],
        "total_usd": 12.00,
        "currency": "USD",
        "status": "delivered",
        "tracking_no": "TRACK-1002-CN",
        "placed_at": "2026-07-05T10:00:00Z",
        "shipped_at": "2026-07-06T09:00:00Z",
        "delivered_at": "2026-07-12T16:30:00Z",
        "refundable": True,
        "refund_reason_allowed": ["defective", "wrong_item"],
    },
    {
        "order_id": "1003",
        "customer_email": "alice@example.com",
        "customer_name": "Alice Wang",
        "items": [
            {"sku": "BGE-TSHIRT-001", "name": "Organic Cotton T-Shirt", "qty": 3, "price_usd": 19.99},
        ],
        "total_usd": 59.97,
        "currency": "USD",
        "status": "pending",
        "tracking_no": None,
        "placed_at": "2026-07-18T20:11:00Z",
        "refundable": True,
        "refund_reason_allowed": ["cancelled_by_customer"],
    },
]

# Logistics status timeline per tracking number.
_SEED_LOGISTICS: dict[str, list[dict[str, str]]] = {
    "TRACK-1001-US": [
        {"ts": "2026-07-11T14:00:00Z", "status": "picked_up", "location": "Shenzhen, CN"},
        {"ts": "2026-07-13T03:00:00Z", "status": "in_transit", "location": "Anchorage, US"},
        {"ts": "2026-07-15T09:00:00Z", "status": "customs_clearance", "location": "Los Angeles, US"},
        {"ts": "2026-07-17T08:00:00Z", "status": "out_for_delivery", "location": "Local Hub, US"},
    ],
    "TRACK-1002-CN": [
        {"ts": "2026-07-06T09:00:00Z", "status": "picked_up", "location": "Yiwu, CN"},
        {"ts": "2026-07-08T10:00:00Z", "status": "in_transit", "location": "Hangzhou, CN"},
        {"ts": "2026-07-11T15:00:00Z", "status": "delivered", "location": "Shanghai, CN"},
    ],
}

_LOCK = Lock()
_TENANT_STATE: dict[str, dict[str, Any]] = {}


def _deep_copy_seed() -> dict[str, Any]:
    import copy
    return {
        "orders": copy.deepcopy(_SEED_ORDERS),
        "logistics": copy.deepcopy(_SEED_LOGISTICS),
        "refunds": [],
    }


def get_tenant_state(tenant_id: str) -> dict[str, Any]:
    """Return the mutable state for a tenant, lazily seeded."""
    with _LOCK:
        if tenant_id not in _TENANT_STATE:
            _TENANT_STATE[tenant_id] = _deep_copy_seed()
        return _TENANT_STATE[tenant_id]


def list_all_tenants() -> list[str]:
    with _LOCK:
        return list(_TENANT_STATE.keys())
