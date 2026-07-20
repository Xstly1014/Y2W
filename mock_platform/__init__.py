"""Mock e-commerce platform (Shopify/Shopee stand-in).

A tiny FastAPI service that emulates just enough of an e-commerce platform
for the agent to demonstrate real business value:
  - GET  /orders/{order_id}        -> fetch order details
  - GET  /orders?customer=...      -> list orders by customer email
  - GET  /logistics/{tracking_no}  -> get logistics status
  - POST /refunds                  -> create a refund for an order
  - GET  /health                   -> liveness probe

Data lives in `mock_platform.data` (in-memory, reset on restart).
Multi-tenant by `X-Tenant-Id` header; each tenant gets its own orders.

Run standalone:
    python -m mock_platform.server
"""
