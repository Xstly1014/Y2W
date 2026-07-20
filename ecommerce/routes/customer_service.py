"""Customer-service routes — proxy to the existing AI agent on api 8000.

The frontend has a global floating "客服" button. Clicking it opens a chat
panel that streams messages from the existing agent service. This router
exposes a thin proxy so the frontend only needs to talk to ONE backend
(the e-commerce service on 8002) rather than cross-origin to 8000.

Endpoints:
  * POST /customer-service/chat          — non-streaming JSON-only chat
  * POST /customer-service/chat/stream   — SSE; accepts JSON or multipart
                                            (multipart => with file uploads)
  * POST /customer-service/feedback      — thumbs up/down
  * GET  /customer-service/health        — agent reachability check

The /chat/stream endpoint transparently forwards upstream SSE events
(meta / route / step_start / step_end / token / final / summary / error)
so the e-commerce widget renders the same real-time "thinking" card UX
that the standalone agent on port 8000 produces.

We pass through the X-Tenant-Id and X-User-Id headers so the agent can
track conversations per-tenant.
"""
from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ecommerce.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customer-service", tags=["customer-service"])


# Files smaller than this get inlined as text inside the message so the
# agent can actually read them. Larger files are summarized as metadata
# (name + size + type) only — embedding tens of KB in every turn would
# blow up the LLM context window and slow responses.
_TEXT_INLINE_LIMIT = 2 * 1024  # 2 KB
# Extensions we will try to read as plain text. The frontend's `ACCEPT`
# already filters to image/* + these — but we re-check here so a
# hand-crafted multipart request can't smuggle a 50MB file as text.
_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts",
             ".html", ".css", ".xml", ".yaml", ".yml", ".ini", ".conf",
             ".sql", ".sh", ".env"}


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
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """SSE proxy to the agent's /api/chat/stream endpoint.

    Accepts EITHER:
      * Content-Type: application/json  -> body: ChatRequest (text-only chat)
      * Content-Type: multipart/form-data -> fields: message, thread_id, context
        (JSON-encoded) + files[] (one or more uploaded files)

    Multipart requests are folded into a single text message before being
    forwarded to the agent. The agent itself only consumes text — it has
    no native file-upload endpoint — so we:
      * read text-like files (.txt, .md, .json, .csv, .log, code, etc.)
        and inline their content under a "附件: <name>\n<content>" header
      * describe non-text files (image/*, application/pdf) as metadata
        only ("已收到图片 attachment.png (12.3KB), 暂不支持直接查看")

    Transparently forwards the upstream SSE byte-for-byte so the frontend
    can consume the exact same event stream (meta / route / step_start /
    step_end / token / final / summary / error).
    """
    content_type = (request.headers.get("content-type") or "").lower()
    is_multipart = "multipart/form-data" in content_type

    if is_multipart:
        # --- multipart branch: extract text + files from FormData ---
        form = await request.form()
        message = form.get("message") or ""
        thread_id = form.get("thread_id") or None
        ctx_raw = form.get("context") or "{}"
        try:
            ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else (ctx_raw or {})
        except json.JSONDecodeError:
            ctx = {}
        # `files` may appear as one or more keys depending on the client
        # (FormData.append each call -> single key; FormData.append array ->
        # multiple keys). Iterate over the whole form looking for UploadFile
        # values.
        uploads: list[UploadFile] = []
        for key in form.keys():
            val = form.get(key)
            if isinstance(val, UploadFile):
                uploads.append(val)
        final_msg = _compose_message_with_attachments(message, ctx, uploads)
    else:
        # --- JSON branch: existing text-only path ---
        try:
            body_json = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {e}")
        try:
            body = ChatRequest(**body_json)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        final_msg = body.message
        thread_id = body.thread_id
        if body.context:
            ctx_lines = [f"{k}: {v}" for k, v in body.context.items() if v is not None]
            if ctx_lines:
                final_msg = f"[用户当前页面上下文] {'; '.join(ctx_lines)}\n\n用户问题: {body.message}"
        ctx = None

    # ---- build the upstream JSON payload ----
    payload = {"message": final_msg}
    if thread_id:
        payload["thread_id"] = thread_id

    headers = {"Content-Type": "application/json"}
    if x_user_id:
        headers["X-User-Id"] = x_user_id
    if x_tenant_id:
        headers["X-Tenant-Id"] = x_tenant_id
    else:
        # Default tenant — match the agent's default.
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


def _compose_message_with_attachments(
    message: str,
    context: dict | None,
    uploads: list[UploadFile],
) -> str:
    """Fold (message, context, uploaded files) into a single text message.

    The agent's chat endpoint only takes a string. To let the user "attach
    a file" we read small text files inline and summarize anything else
    (image, pdf, large file) as metadata. The resulting string is
    forwarded to the agent in place of the user's bare text.

    Layout of the final message:
        [page context if any]
        用户问题: <message>
        ---
        附件 (N 个):
          1. <name> (<human size>, <mime>)  — 全文如下:
             <file content>
          2. <name> (<size>, <mime>)  — 已收到，暂不支持直接查看（仅记录元数据）
        ...
    """
    parts: list[str] = []

    if context:
        ctx_lines = [f"{k}: {v}" for k, v in context.items() if v is not None]
        if ctx_lines:
            parts.append(f"[用户当前页面上下文] {'; '.join(ctx_lines)}")

    parts.append(f"用户问题: {message or '(空，仅附件)'}")

    if uploads:
        blocks: list[str] = []
        for idx, f in enumerate(uploads, 1):
            name = f.filename or f"file{idx}"
            mime = f.content_type or "application/octet-stream"
            size = _safe_size(f)
            ext = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
            is_text = (mime.startswith("text/") or ext in _TEXT_EXT
                       or mime in {"application/json"})
            if is_text and size <= _TEXT_INLINE_LIMIT:
                # Read and inline the actual text so the LLM can reason
                # about it. Capped at _TEXT_INLINE_LIMIT so a 50MB log
                # file doesn't blow up the context window.
                try:
                    raw = f.file.read(_TEXT_INLINE_LIMIT + 1)
                except Exception as e:  # noqa: BLE001
                    blocks.append(
                        f"  {idx}. {name} ({_fmt_size(size)}, {mime}) — 读取失败: {e}"
                    )
                    continue
                if len(raw) > _TEXT_INLINE_LIMIT:
                    truncated = raw[:_TEXT_INLINE_LIMIT].decode("utf-8", errors="replace")
                    blocks.append(
                        f"  {idx}. {name} ({_fmt_size(size)}, {mime}) — 文本过长，仅前"
                        f" {_TEXT_INLINE_LIMIT} 字节:\n```\n{truncated}\n```"
                    )
                else:
                    text = raw.decode("utf-8", errors="replace")
                    blocks.append(
                        f"  {idx}. {name} ({_fmt_size(size)}, {mime}) — 全文如下:\n"
                        f"```\n{text}\n```"
                    )
            else:
                # Non-text or oversized: just record the metadata. The agent
                # at least knows the user attached something and what it is.
                kind = "图片" if mime.startswith("image/") else (
                    "PDF" if mime == "application/pdf" else "二进制文件"
                )
                blocks.append(
                    f"  {idx}. {name} ({_fmt_size(size)}, {kind}) — 已收到，"
                    f"当前模型不支持直接查看，仅记录元数据"
                )
        parts.append("---\n附件 (" + str(len(uploads)) + " 个):\n" + "\n".join(blocks))

    return "\n\n".join(parts)


def _safe_size(f: UploadFile) -> int:
    """Best-effort: return file size from headers without consuming the stream."""
    try:
        return int(getattr(f, "size", None) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 / 1024:.2f}MB"
