"""Customer-service routes — proxy to the existing AI agent on api 8000.

The frontend has a global floating "客服" button. Clicking it opens a chat
panel that streams messages from the existing agent service. This router
exposes a thin proxy so the frontend only needs to talk to ONE backend
(the e-commerce service on 8002) rather than cross-origin to 8000.

Two endpoints:
  * POST /customer-service/chat  — non-streaming, returns full answer + trace_id
  * GET  /customer-service/health — ping the agent service

We pass through the X-Tenant-Id and X-User-Id headers so the agent can
track conversations per-tenant.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ecommerce.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customer-service", tags=["customer-service"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None
    # Context the e-commerce frontend injects so the agent can answer
    # questions about the current page (e.g. "I'm viewing product 42").
    context: dict | None = None


class ChatResponse(BaseModel):
    answer: str
    trace_id: str | None = None
    thread_id: str | None = None
    ok: bool = True


class FeedbackRequest(BaseModel):
    trace_id: str
    feedback: str = Field(..., description="'up' (positive) or 'down' (negative)")


@router.get("/health")
async def agent_health():
    """Check if the agent backend (api 8000) is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.agent_api_url}/api/health")
            return {"up": r.status_code == 200, "status_code": r.status_code}
    except Exception as e:
        return {"up": False, "error": str(e)}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Proxy to the agent's /api/chat endpoint (non-streaming).

    If the frontend sent a `context` dict, we prepend it to the message
    so the agent has page context (e.g. "用户正在浏览商品ID 42，标题: ...").
    """
    # Compose final message — context prepended as a system hint.
    final_msg = body.message
    if body.context:
        ctx_lines = [f"{k}: {v}" for k, v in body.context.items() if v is not None]
        if ctx_lines:
            final_msg = f"[用户当前页面上下文] {'; '.join(ctx_lines)}\n\n用户问题: {body.message}"

    payload = {"message": final_msg}
    if body.thread_id:
        payload["thread_id"] = body.thread_id

    headers = {"Content-Type": "application/json"}
    if x_user_id:
        headers["X-User-Id"] = x_user_id
    if x_tenant_id:
        headers["X-Tenant-Id"] = x_tenant_id
    else:
        # Default tenant — match the agent's default.
        headers["X-Tenant-Id"] = "demo-tenant"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{settings.agent_api_url}/api/chat",
                json=payload,
                headers=headers,
            )
        if r.status_code != 200:
            raise HTTPException(
                status_code=r.status_code,
                detail=f"Agent error: {r.text[:500]}",
            )
        data = r.json()
        return ChatResponse(
            answer=data.get("answer", ""),
            trace_id=data.get("trace_id"),
            thread_id=data.get("thread_id"),
            ok=data.get("ok", True),
        )
    except httpx.RequestError as e:
        logger.error("customer-service chat: %s", e)
        raise HTTPException(status_code=502, detail=f"Agent unreachable: {e}")


@router.post("/feedback")
async def feedback(
    body: FeedbackRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Proxy thumbs-up/down feedback to the agent's flywheel collector.

    The agent's /api/feedback endpoint requires user_input + prediction, but
    the e-commerce widget only has trace_id. We pass placeholders and rely on
    trace_id to link back to the full conversation trace.
    """
    passed = body.feedback.lower() in ("up", "good", "positive", "1", "true")
    payload = {
        "user_input": "(from e-commerce widget)",
        "prediction": "(from e-commerce widget)",
        "passed": passed,
        "trace_id": body.trace_id,
        "tenant_id": x_tenant_id or "demo-tenant",
    }
    headers = {"Content-Type": "application/json"}
    if x_user_id:
        headers["X-User-Id"] = x_user_id
    if x_tenant_id:
        headers["X-Tenant-Id"] = x_tenant_id
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{settings.agent_api_url}/api/feedback",
                json=payload,
                headers=headers,
            )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=f"Agent feedback error: {r.text[:300]}")
        return {"ok": True, "feedback": body.feedback, "trace_id": body.trace_id}
    except httpx.RequestError as e:
        logger.error("customer-service feedback: %s", e)
        raise HTTPException(status_code=502, detail=f"Agent unreachable: {e}")


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """SSE proxy to the agent's /api/chat/stream endpoint.

    Transparently forwards the upstream SSE byte-for-byte so the frontend can
    consume the exact same event stream (meta / route / step_start / step_end
    / token / final / summary / error) that the agent service emits. This is
    what lets the e-commerce widget render the real-time "thinking" card with
    tool-call / agent-think steps appearing one-by-one.

    We use httpx.stream() so chunks are forwarded as soon as they arrive —
    NOT buffered until the upstream request finishes. StreamingResponse with
    media_type='text/event-stream' keeps the connection alive.
    """
    # Compose final message — context prepended as a system hint (mirrors /chat).
    final_msg = body.message
    if body.context:
        ctx_lines = [f"{k}: {v}" for k, v in body.context.items() if v is not None]
        if ctx_lines:
            final_msg = f"[用户当前页面上下文] {'; '.join(ctx_lines)}\n\n用户问题: {body.message}"

    payload = {"message": final_msg}
    if body.thread_id:
        payload["thread_id"] = body.thread_id

    headers = {"Content-Type": "application/json"}
    if x_user_id:
        headers["X-User-Id"] = x_user_id
    if x_tenant_id:
        headers["X-Tenant-Id"] = x_tenant_id
    else:
        headers["X-Tenant-Id"] = "demo-tenant"

    upstream_url = f"{settings.agent_api_url}/api/chat/stream"

    async def upstream_gen():
        # Use a long read timeout — agent turns can take 30s+ when multiple
        # tools fire. No write timeout (we only POST once). connect timeout
        # stays short so we fail fast if the agent is down.
        timeout = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", upstream_url, json=payload, headers=headers
                ) as r:
                    if r.status_code != 200:
                        text = await r.aread()
                        yield (
                            f"event: error\ndata: "
                            f'{{"message":"agent status {r.status_code}","trace_id":null}}\n\n'
                        ).encode()
                        return
                    # Forward upstream SSE bytes verbatim. The agent emits
                    # `event: <type>\ndata: <json>\n\n` blocks; we just pipe
                    # them through unchanged so the frontend parser sees the
                    # same wire format as if it talked to 8000 directly.
                    async for chunk in r.aiter_bytes():
                        if chunk:
                            yield chunk
        except httpx.RequestError as e:
            logger.error("customer-service chat/stream: %s", e)
            err = f'{{"message":"agent unreachable: {e}","trace_id":null}}'
            yield f"event: error\ndata: {err}\n\n".encode()
        except Exception as e:  # noqa: BLE001
            logger.exception("customer-service chat/stream unexpected error")
            err = f'{{"message":"{type(e).__name__}: {e}","trace_id":null}}'
            yield f"event: error\ndata: {err}\n\n".encode()

    return StreamingResponse(
        upstream_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
            "Connection": "keep-alive",
        },
    )
