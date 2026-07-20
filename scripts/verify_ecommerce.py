"""End-to-end API verification for the e-commerce platform.

Runs against a live ecommerce.server on http://127.0.0.1:8002.
Verifies: catalog -> user ensure -> cart -> checkout -> pay -> order list.
"""
from __future__ import annotations

import json
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8002"
USER_ID = f"e2e-{uuid.uuid4().hex[:12]}"
HEADERS = {"X-User-Id": USER_ID, "X-Tenant-Id": "demo-tenant"}


def step(n: int, name: str) -> None:
    print(f"\n--- Step {n}: {name} ---")


def main() -> int:
    client = httpx.Client(base_url=BASE, headers=HEADERS, timeout=15.0)

    # 1. health
    step(1, "health")
    r = client.get("/api/health")
    print(f"GET /api/health -> {r.status_code}: {r.text[:200]}")
    assert r.status_code == 200

    # 2. categories
    step(2, "categories tree")
    r = client.get("/api/catalog/categories")
    print(f"GET /api/catalog/categories -> {r.status_code}, items={len(r.json())}")
    assert r.status_code == 200 and len(r.json()) > 0

    # 3. product list
    step(3, "product list")
    r = client.get("/api/catalog/products", params={"page_size": 5})
    data = r.json()
    print(f"GET /api/catalog/products -> {r.status_code}, total={data['total']}, returned={len(data['items'])}")
    assert r.status_code == 200 and data["total"] > 0
    product_id = data["items"][0]["id"]

    # 4. product detail
    step(4, f"product detail (id={product_id})")
    r = client.get(f"/api/catalog/products/{product_id}")
    p = r.json()
    print(f"GET /api/catalog/products/{product_id} -> {r.status_code}")
    print(f"   title={p['title'][:40]}, skus={len(p['skus'])}, images={len(p['images'])}")
    assert r.status_code == 200 and len(p["skus"]) > 0
    sku_id = p["skus"][0]["id"]

    # 5. ensure user (GET /users/me auto-creates on first request)
    step(5, "ensure user via GET /users/me")
    r = client.get("/api/users/me")
    print(f"GET /api/users/me -> {r.status_code}: {r.text[:200]}")
    assert r.status_code == 200

    # 6. add address
    step(6, "add address")
    r = client.post("/api/users/addresses", json={
        "recipient": "E2E User",
        "phone": "13800000000",
        "province": "北京市",
        "city": "北京市",
        "district": "海淀区",
        "detail": "中关村大街1号",
        "is_default": True,
    })
    addr = r.json()
    print(f"POST /api/users/addresses -> {r.status_code}, addr_id={addr.get('id')}")
    assert r.status_code in (200, 201) and addr.get("id")
    address_id = addr["id"]

    # 7. add to cart
    step(7, f"add to cart (sku_id={sku_id}, qty=2)")
    r = client.post("/api/cart/items", json={"sku_id": sku_id, "quantity": 2})
    print(f"POST /api/cart/items -> {r.status_code}: {r.text[:200]}")
    assert r.is_success

    # 8. list cart
    step(8, "list cart")
    r = client.get("/api/cart")
    cart = r.json()
    print(f"GET /api/cart -> {r.status_code}, items={len(cart['items'])}, subtotal={cart.get('selected_subtotal')}")
    assert r.status_code == 200 and len(cart["items"]) > 0

    # 9. create order
    step(9, "create order")
    r = client.post("/api/orders", json={
        "address_id": address_id,
        "items": [{"sku_id": sku_id, "quantity": 1}],
        "remark": "E2E test order",
    })
    order = r.json()
    print(f"POST /api/orders -> {r.status_code}, order_no={order.get('order_no')}, total={order.get('total_amount')}")
    assert r.is_success and order.get("order_no")
    order_id = order["id"]

    # 10. pay
    step(10, f"pay order (id={order_id})")
    r = client.post(f"/api/orders/{order_id}/payment", json={
        "method": "alipay",
        "provider": "mock",
    })
    pay = r.json()
    print(f"POST /api/orders/{order_id}/payment -> {r.status_code}, success={pay.get('success')}, order_status={pay.get('order_status')}")
    assert r.is_success and pay.get("success") is True

    # 11. order detail (verify paid)
    step(11, "order detail after payment")
    r = client.get(f"/api/orders/{order_id}")
    od = r.json()
    print(f"GET /api/orders/{order_id} -> {r.status_code}, status={od.get('status')}, paid_at={od.get('paid_at')}")
    assert r.status_code == 200 and od.get("status") == "paid"

    # 12. order list
    step(12, "order list")
    r = client.get("/api/orders")
    ol = r.json()
    print(f"GET /api/orders -> {r.status_code}, total={ol.get('total', len(ol.get('items', [])))}")
    assert r.status_code == 200

    # 13. recommendations
    step(13, "recommendations")
    r = client.get("/api/recommendations/for-user")
    rec = r.json()
    print(f"GET /api/recommendations/for-user -> {r.status_code}, items={len(rec.get('items', rec if isinstance(rec, list) else []))}")

    # 14. product related
    step(14, "related products")
    r = client.get(f"/api/catalog/products/{product_id}/related")
    print(f"GET /api/catalog/products/{product_id}/related -> {r.status_code}, items={len(r.json())}")

    print("\n" + "=" * 60)
    print(f"ALL 14 STEPS PASSED. user_id={USER_ID}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
