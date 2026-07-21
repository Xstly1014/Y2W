"""FastAPI business layer.

Wires the 0719agent (ReAct + RAG + Skills + flywheel + tracing) into a
multi-tenant HTTP API that powers the cross-border e-commerce customer
service web app.

Run standalone:
    python -m api.server

The static front-end lives in `static/index.html` and is served at `/`
automatically.
"""
