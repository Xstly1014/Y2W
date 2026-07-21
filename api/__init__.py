"""FastAPI business layer.

Wires the 0719agent (ReAct + RAG + Skills + flywheel + tracing) into a
multi-tenant HTTP API that powers the cross-border e-commerce customer
service web app.

Run standalone:
    python -m api.server

This service is now **API-only** — the user-facing UI lives exclusively
in the standalone e-commerce service (port 8002 → ``/shop``) and
proxies chat traffic here via ``/api/chat/stream``. There is no HTML
page on port 8000; ``GET /`` returns 404.
"""
