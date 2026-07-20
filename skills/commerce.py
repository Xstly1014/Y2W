"""Cross-border e-commerce customer-service skills.

These three skills contribute the business-specific tools that turn the
generic 0719agent into a customer-service agent for a Shopify/Shopee-like
store:

  - `query_order`      : look up an order by id or customer email
  - `query_logistics`  : look up the logistics timeline for a tracking number
  - `create_refund`    : initiate a refund on the mock platform

All three call the mock platform over HTTP. The tenant id is read from
a context variable that the API layer sets per-request, so multiple
sellers can share one agent process without leaking data.

Future expansion hooks:
  - Replace HTTP calls with the official `shopify-python-sdk` for real stores
  - Add `cancel_order`, `modify_address`, `apply_coupon` skills
  - Wrap each call in a retry / circuit-breaker policy
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from config import settings
from skills.base import Skill

logger = logging.getLogger(__name__)

# Per-request tenant id. Set by the API layer (see api/deps.py) so that
# skills running inside the agent loop know which tenant's data to touch.
current_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_tenant_id", default=settings.default_tenant_id
)


def _tenant() -> str:
    return current_tenant_id.get()


def _headers() -> dict[str, str]:
    return {"X-Tenant-Id": _tenant()}


# --------------------------------------------------------------------------- #
# Order query
# --------------------------------------------------------------------------- #
@tool
def query_order(order_id: str | None = None, customer_email: str | None = None) -> str:
    """Look up an order on the e-commerce platform.

    Provide EITHER order_id (e.g. "1001") OR customer_email. Returns the
    order(s) as a compact string: id, status, total, items, tracking_no.

    Use this whenever the customer mentions an order number, asks where
    their package is, or wants to start a refund.
    """
    if not order_id and not customer_email:
        return "query_order error: provide order_id or customer_email"
    try:
        with httpx.Client(timeout=10.0) as client:
            if order_id:
                r = client.get(
                    f"{settings.mock_platform_base_url}/orders/{order_id}",
                    headers=_headers(),
                )
                if r.status_code == 404:
                    return f"order {order_id} not found"
                r.raise_for_status()
                orders = [r.json()]
            else:
                r = client.get(
                    f"{settings.mock_platform_base_url}/orders",
                    params={"customer": customer_email},
                    headers=_headers(),
                )
                r.raise_for_status()
                orders = r.json()
        if not orders:
            return "no orders found"
        lines = []
        for o in orders:
            # Defensive: items may be missing or malformed in some mock states.
            items = o.get("items") or []
            try:
                items_str = ", ".join(
                    f"{i.get('qty', '?')}x {i.get('name', '?')}" for i in items
                )
            except (TypeError, AttributeError):
                items_str = "<unable to render items>"
            lines.append(
                f"order#{o.get('order_id', '?')} status={o.get('status', '?')} "
                f"total=${o.get('total_usd', '?')} items=[{items_str}] "
                f"tracking={o.get('tracking_no') or 'N/A'} "
                f"refundable={o.get('refundable')}"
            )
        return "; ".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("query_order failed")
        return f"query_order error: {exc}"


# --------------------------------------------------------------------------- #
# Logistics query
# --------------------------------------------------------------------------- #
@tool
def query_logistics(tracking_no: str) -> str:
    """Look up the logistics timeline for a tracking number.

    Returns the most recent 3 events (timestamp + status + location) so
    the customer can see where their package is. Use this after
    `query_order` returned a tracking_no, or when the customer mentions
    one directly.
    """
    if not tracking_no:
        return "query_logistics error: tracking_no is required"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                f"{settings.mock_platform_base_url}/logistics/{tracking_no}",
                headers=_headers(),
            )
            if r.status_code == 404:
                return f"tracking {tracking_no} not found"
            r.raise_for_status()
            data = r.json()
        timeline = data.get("timeline", [])[-3:]
        if not timeline:
            return f"no events for {tracking_no}"
        lines = [f"{e['ts']} {e['status']} @ {e['location']}" for e in timeline]
        return f"tracking={tracking_no} latest_events: " + " | ".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("query_logistics failed")
        return f"query_logistics error: {exc}"


# --------------------------------------------------------------------------- #
# Refund creation (write operation)
# --------------------------------------------------------------------------- #
@tool
def create_refund(order_id: str, reason: str, amount_usd: float | None = None) -> str:
    """Initiate a refund for an order on the e-commerce platform.

    Args:
        order_id: the order id, e.g. "1001"
        reason: must be one of: defective, not_received, wrong_item,
            cancelled_by_customer. The allowed reasons depend on the order.
        amount_usd: optional; defaults to full refund. Only set for partial refunds.

    Returns the refund_id and processed amount, or an error message
    explaining why the refund was rejected (wrong reason, not refundable...).

    ALWAYS call `query_order` first to verify the order is refundable and
    to discover which reasons are allowed for that order.
    """
    try:
        payload: dict[str, Any] = {"order_id": order_id, "reason": reason}
        if amount_usd is not None:
            payload["amount_usd"] = amount_usd
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{settings.mock_platform_base_url}/refunds",
                json=payload,
                headers=_headers(),
            )
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail", r.text)
                except Exception:  # noqa: BLE001
                    detail = r.text
                return f"refund rejected: {detail}"
            data = r.json()
        return (
            f"refund processed: refund_id={data['refund_id']} "
            f"amount=${data['amount_usd']} reason={data['reason']} "
            f"status={data['status']}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("create_refund failed")
        return f"create_refund error: {exc}"


# --------------------------------------------------------------------------- #
# Skill packaging (inherits from the Skill base class)
# --------------------------------------------------------------------------- #
class CommerceSkills(Skill):
    """Contribute query_order / query_logistics / create_refund tools.

    Inherits the get_tools / as_tools plumbing from Skill — subclasses only
    need to implement build_tools(). main.py wires them into the agent.
    """

    name: str = "commerce"
    description: str = "Cross-border e-commerce order/logistics/refund skills."
    version: str = "0.1.0"
    tags: tuple[str, ...] = ("commerce", "customer-service")
    permissions: tuple[str, ...] = ("network",)
    dependencies: tuple[str, ...] = ("httpx",)
    enabled_by_default: bool = True

    def build_tools(self) -> list[BaseTool]:
        return [query_order, query_logistics, create_refund]
