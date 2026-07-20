"""Chat routes: streaming SSE + simple invoke.

POST /api/chat            -> non-streaming invoke, returns final answer + trace_id
POST /api/chat/stream     -> SSE stream of routing + step + final events
GET  /api/chat/conversations/{thread_id}/history  -> retrieve prior messages

SSE event types emitted on /stream:
    meta        : session metadata (thread_id, tenant_id)
    route       : router decision (route, route_reason, subagent_name)
    step_start  : a tool call / llm call / agent-think begins
    step_end    : the matching step ends (preview, latency_ms)
    final       : the final answer (answer, trace_id, num_steps, ok)
    summary     : aggregated stats (total_latency_ms, num_tools_called, ...)
    error       : failure (message, trace_id)

Each event dict is JSON-encoded into the SSE `data:` field. The router node
emits its own agent_think step plus a `route` event before the chosen
subagent node runs, so the front-end can render "已转交订单专员处理" in
real time.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from sse_starlette.sse import EventSourceResponse

from api.deps import get_agent_for_tenant
from api.schemas import ChatRequest, validate_safe_id
from config import settings
from observability.tracing import TraceRecorder, _traces_dir
from skills.commerce import current_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _resolve_tenant(req: ChatRequest, x_tenant_id: str | None) -> str:
    """Pick the tenant id, validating header / default values too.

    Pydantic already validates ``req.tenant_id`` against the safe-id
    pattern; here we additionally guard ``x_tenant_id`` (which bypasses
    Pydantic) and the configured default, since a header like
    ``X-Tenant-Id: ../../etc`` would otherwise flow straight into a
    filesystem path. ``validate_safe_id`` raises ValueError on a bad
    value; the FastAPI handler turns that into a 400 response.
    """
    tenant_id = req.tenant_id or x_tenant_id or settings.default_tenant_id
    return validate_safe_id(tenant_id, "tenant_id")  # type: ignore[return-value]


def _new_thread_id(tenant_id: str) -> str:
    """Generate a fresh, globally-unique thread id for a request.

    Previously the default was ``f"tenant-{tenant_id}-default"`` — a
    *fixed* string shared by every caller who omitted thread_id. That
    meant two concurrent buyers in the same tenant would land in the
    same langgraph checkpointer thread and see each other's messages.
    A uuid4 suffix guarantees uniqueness per request.
    """
    return f"tenant-{tenant_id}-{uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Non-streaming chat
# --------------------------------------------------------------------------- #
@router.post("")
def chat(req: ChatRequest, x_tenant_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Non-streaming chat. Returns final answer + trace metadata."""
    try:
        tenant_id = _resolve_tenant(req, x_tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    thread_id = req.thread_id or _new_thread_id(tenant_id)
    token = current_tenant_id.set(tenant_id)
    try:
        agent = get_agent_for_tenant(tenant_id)
        recorder = TraceRecorder(
            thread_id=thread_id,
            model_name=settings.llm_model_name,
            tenant_id=tenant_id,
        )
        recorder.user_input = req.message
        final_answer = ""
        try:
            for event in agent.stream(
                {"messages": [("user", req.message)]},
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="updates",
            ):
                for _node, payload in event.items():
                    _consume(payload, recorder)
            state = agent.get_state(config={"configurable": {"thread_id": thread_id}})
            msgs = state.values.get("messages", []) if state and state.values else []
            if msgs:
                # Walk backwards to skip ToolMessage trailers (agent may
                # have terminated abnormally right after a tool call) and
                # pick the last AIMessage's content. Mirrors the fix in
                # observability/tracing.py and chat_stream().
                for msg in reversed(msgs):
                    msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()
                    if msg_type in {"ai", "aimessage"}:
                        final_answer = getattr(msg, "content", str(msg))
                        break
                else:
                    final_answer = getattr(msgs[-1], "content", str(msgs[-1]))
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent stream failed")
            recorder.finalize(final_answer, error=f"{type(exc).__name__}: {exc}")
            return {
                "answer": f"[agent error] {exc}",
                "trace_id": recorder.trace_id,
                "thread_id": thread_id,
                "tenant_id": tenant_id,
                "num_steps": len(recorder.steps),
                "ok": False,
            }
        recorder.finalize(final_answer)
        return {
            "answer": final_answer,
            "trace_id": recorder.trace_id,
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "num_steps": len(recorder.steps),
            "ok": True,
        }
    finally:
        current_tenant_id.reset(token)


# --------------------------------------------------------------------------- #
# Streaming chat (SSE)
# --------------------------------------------------------------------------- #
@router.post("/stream")
def chat_stream(
    req: ChatRequest, x_tenant_id: str | None = Header(default=None)
) -> EventSourceResponse:
    """SSE stream emitting meta / route / step_start / step_end / final / summary.

    The event payload is always a JSON string with at least an `event_type`
    echo so the front-end can dispatch without parsing the SSE wire format.
    """
    try:
        tenant_id = _resolve_tenant(req, x_tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    thread_id = req.thread_id or _new_thread_id(tenant_id)

    async def event_gen():
        token = current_tenant_id.set(tenant_id)
        started_at = time.time()
        # Pre-create recorder outside the try so it's always bound when the
        # except/finally blocks run — avoids UnboundLocalError if
        # get_agent_for_tenant raises before recorder is assigned.
        recorder = TraceRecorder(
            thread_id=thread_id,
            model_name=settings.llm_model_name,
            tenant_id=tenant_id,
        )
        recorder.user_input = req.message
        try:
            yield {
                "event": "meta",
                "data": json.dumps(
                    {"thread_id": thread_id, "tenant_id": tenant_id},
                    ensure_ascii=False,
                ),
            }
            agent = get_agent_for_tenant(tenant_id)
            final_answer = ""
            ctx = _StreamCtx()
            try:
                # Combined stream modes for true real-time UX:
                # - "messages": per-message events (LLM token chunks, tool calls,
                #   tool results) streamed the instant they happen, NOT delayed
                #   until the sub-agent node finishes. This is what makes the
                #   thinking-card steps appear one-by-one in real time.
                # - "updates": node-level updates, used only for the router's
                #   `route` event (router doesn't emit user-visible messages).
                #
                # IMPORTANT: we use `astream` (async iterator) not `stream`
                # (sync iterator). The sync iterator blocks the event loop
                # inside this async generator, causing all SSE events to be
                # buffered until the agent finishes — defeating real-time UX.
                #
                # IMPORTANT: `subgraphs=True` is REQUIRED for real-time sub-agent
                # events. The order_ops / knowledge sub-agents are compiled
                # `create_react_agent` graphs embedded as nodes in the parent
                # StateGraph. Without `subgraphs=True`, the parent astream()
                # only sees events when each sub-graph node FINISHES — so all
                # the sub-agent's tool-call / tool-result / final-LLM events
                # arrive in a single burst at the end, defeating real-time UX.
                # With `subgraphs=True`, langgraph streams events from inside
                # sub-graphs as they happen. The chunk tuple gains a leading
                # `namespace` field: (namespace, mode, data).
                async for chunk in agent.astream(
                    {"messages": [("user", req.message)]},
                    config={"configurable": {"thread_id": thread_id}},
                    stream_mode=["messages", "updates"],
                    subgraphs=True,
                ):
                    # With subgraphs=True, chunk is (namespace, mode, data).
                    # namespace is () for the root graph or a tuple identifying
                    # which sub-graph emitted the event.
                    namespace, mode, data = chunk
                    logger.debug(
                        "[sse] chunk received mode=%s ns=%s elapsed=%.2fs",
                        mode,
                        namespace,
                        time.time() - started_at,
                    )
                    if mode == "messages":
                        # `data` is (message, metadata) — emit real-time steps.
                        msg, metadata = data
                        node_name = ""
                        if isinstance(metadata, dict):
                            node_name = (
                                metadata.get("langgraph_node")
                                or metadata.get("name")
                                or ""
                            )
                        for ev_type, ev_data in _iter_message_events(
                            msg, node_name, ctx, recorder
                        ):
                            logger.debug(
                                "[sse] yielding event=%s elapsed=%.2fs",
                                ev_type,
                                time.time() - started_at,
                            )
                            yield {
                                "event": ev_type,
                                "data": json.dumps(ev_data, ensure_ascii=False),
                            }
                    elif mode == "updates":
                        # `data` is {node_name: payload}. Use it for trace
                        # recording (via _consume) and for the router route
                        # event. Step events for sub-agent nodes are already
                        # emitted in real time by the messages branch above.
                        for node_name, payload in data.items():
                            _consume(payload, recorder)
                            if node_name == "router":
                                for ev_type, ev_data in _iter_events(
                                    node_name, payload, ctx
                                ):
                                    logger.debug(
                                        "[sse] yielding event=%s elapsed=%.2fs",
                                        ev_type,
                                        time.time() - started_at,
                                    )
                                    yield {
                                        "event": ev_type,
                                        "data": json.dumps(
                                            ev_data, ensure_ascii=False
                                        ),
                                    }
                state = await agent.aget_state(
                    config={"configurable": {"thread_id": thread_id}}
                )
                msgs = state.values.get("messages", []) if state and state.values else []
                if msgs:
                    # Find the last AIMessage (skip ToolMessage if agent ended
                    # abnormally after a tool call).
                    for msg in reversed(msgs):
                        if getattr(msg, "type", "") in {"ai", "aimessage"}:
                            final_answer = getattr(msg, "content", str(msg))
                            break
                    else:
                        final_answer = getattr(msgs[-1], "content", str(msgs[-1]))
                recorder.finalize(final_answer)
                yield {
                    "event": "final",
                    "data": json.dumps(
                        {
                            "answer": final_answer,
                            "trace_id": recorder.trace_id,
                            "num_steps": len(recorder.steps),
                            "ok": True,
                        },
                        ensure_ascii=False,
                    ),
                }
                yield {
                    "event": "summary",
                    "data": json.dumps(
                        {
                            "total_latency_ms": round((time.time() - started_at) * 1000, 1),
                            "num_tools_called": ctx.num_tools,
                            "num_llm_calls": ctx.num_llm,
                            "num_steps": ctx.num_steps,
                        },
                        ensure_ascii=False,
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent stream failed")
                recorder.finalize(final_answer, error=str(exc))
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": str(exc), "trace_id": recorder.trace_id},
                        ensure_ascii=False,
                    ),
                }
                # Always emit a summary so the front-end can stop its spinner
                # even when the agent errored mid-flight.
                yield {
                    "event": "summary",
                    "data": json.dumps(
                        {
                            "total_latency_ms": round((time.time() - started_at) * 1000, 1),
                            "num_tools_called": ctx.num_tools,
                            "num_llm_calls": ctx.num_llm,
                            "num_steps": ctx.num_steps,
                            "ok": False,
                        },
                        ensure_ascii=False,
                    ),
                }
            except BaseException as exc:
                # Covers asyncio.CancelledError (client disconnected mid-stream)
                # — without this, recorder.finalize() never runs and the trace
                # file is lost.
                if type(exc).__name__ == "CancelledError":
                    logger.info("sse client disconnected; finalising trace %s", recorder.trace_id)
                    recorder.finalize(final_answer, error="client_disconnected")
                else:
                    logger.exception("non-Exception BaseException in sse stream")
                    recorder.finalize(final_answer, error=str(exc))
                raise
        finally:
            current_tenant_id.reset(token)

    return EventSourceResponse(event_gen())


@router.get("/conversations/{thread_id}/history")
def history(thread_id: str, x_tenant_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Return all traces for a thread (each trace = one user turn).

    Tenant isolation: thread_id must match the requesting tenant. We accept
    either the explicit ``trace.tenant_id`` field or the
    ``tenant-<tenant_id>`` prefix convention used by ``api.deps``.
    """
    # Validate path-parameter IDs before they hit the filesystem — a
    # crafted thread_id like ``../../etc/passwd`` would otherwise escape
    # the traces directory via Path / "f-string".
    try:
        validate_safe_id(thread_id, "thread_id")
        tenant_id_raw: str | None = x_tenant_id or settings.default_tenant_id
        tenant_id = validate_safe_id(tenant_id_raw, "tenant_id")  # type: ignore[assignment]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = _traces_dir() / f"{thread_id}.jsonl"
    if not path.exists():
        return {"thread_id": thread_id, "tenant_id": tenant_id, "traces": []}
    traces: list[dict[str, Any]] = []
    # Stream line-by-line instead of read_text() to avoid loading very long
    # conversation histories fully into memory.
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Tenant isolation: filter out traces that don't belong to this tenant.
    if tenant_id:
        traces = [t for t in traces if _trace_belongs_to_tenant(t, tenant_id, thread_id)]
    return {"thread_id": thread_id, "tenant_id": tenant_id, "traces": traces}


def _trace_belongs_to_tenant(trace: dict[str, Any], tenant_id: str, thread_id: str) -> bool:
    """Match a trace to a tenant. Prefer explicit tenant_id field, then fall
    back to the thread_id prefix convention."""
    tid = trace.get("tenant_id")
    if tid is not None:
        return str(tid) == tenant_id
    return (
        thread_id == f"tenant-{tenant_id}"
        or thread_id.startswith(f"tenant-{tenant_id}-")
    )


# --------------------------------------------------------------------------- #
# Helpers: friendly message + per-event emission
# --------------------------------------------------------------------------- #
def _friendly_for_tool(name: str, args: dict[str, Any]) -> str:
    """Translate a tool name + args into a Chinese friendly progress message."""
    a = args or {}
    if name == "query_order":
        oid = a.get("order_id") or a.get("customer_email") or ""
        return f"正在查询订单 {oid} 状态..." if oid else "正在查询订单信息..."
    if name == "query_logistics":
        tn = a.get("tracking_no") or ""
        return f"正在查询物流轨迹 {tn}..." if tn else "正在查询物流轨迹..."
    if name == "create_refund":
        oid = a.get("order_id") or ""
        return f"正在为订单 {oid} 创建退款申请..." if oid else "正在创建退款申请..."
    if name == "rag_search":
        return "正在检索知识库..."
    if name == "summarize_text":
        return "正在总结内容..."
    if name == "calculator":
        return "正在计算..."
    if name == "current_time":
        return "正在获取当前时间..."
    if name == "search":
        return "正在搜索..."
    return f"正在执行 {name}..."


_SUBAGENT_FRIENDLY: dict[str, str] = {
    "order_ops": "订单专员正在处理你的请求...",
    "knowledge": "知识库专员正在检索答案...",
    "escalation": "正在升级到人工客服...",
    "router": "正在判断你的请求应该由哪位专员处理...",
}


class _StreamCtx:
    """Mutable per-SSE-session counters and step-id timing."""

    def __init__(self) -> None:
        self.counter = 0
        self.start_times: dict[str, float] = {}
        # tool_call_id -> step_id: pairs an AIMessage.tool_calls entry with
        # its matching ToolMessage so we can emit a paired step_end.
        self.pending_tool_steps: dict[str, str] = {}
        self.num_tools = 0
        self.num_llm = 0
        self.num_steps = 0

    def next_step_id(self) -> str:
        self.counter += 1
        return f"step-{self.counter}"


def _iter_events(
    node_name: str,
    payload: Any,
    ctx: _StreamCtx,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Turn one langgraph node-update payload into a stream of SSE events.

    NOTE: With `subgraphs=True`, sub-agent (order_ops / knowledge) and
    escalation nodes' tool-call / llm-call events are emitted in REAL
    TIME by the `messages` branch of the SSE loop (via
    `_iter_message_events`). Walking the payload's messages here would
    DUPLICATE those events. So this function now ONLY handles the
    router node (which doesn't emit user-visible messages — its step
    + route event must be synthesised from the updates payload).
    """
    if not isinstance(payload, dict):
        return

    # Router node: emit one agent_think step + a `route` event.
    if node_name == "router":
        step_id = ctx.next_step_id()
        ctx.start_times[step_id] = time.time()
        ctx.num_llm += 1
        ctx.num_steps += 1
        yield (
            "step_start",
            {
                "step_id": step_id,
                "step_type": "agent_think",
                "friendly_message": "正在判断你的请求应该由哪位专员处理...",
                "node": node_name,
            },
        )
        latency_ms = round((time.time() - ctx.start_times.pop(step_id, time.time())) * 1000, 1)
        yield (
            "step_end",
            {
                "step_id": step_id,
                "preview": "",
                "latency_ms": latency_ms,
                "node": node_name,
            },
        )
        route = payload.get("route") or ""
        route_reason = payload.get("route_reason") or ""
        subagent_name = payload.get("subagent_name") or ""
        yield (
            "route",
            {
                "route": route,
                "route_reason": route_reason,
                "subagent_name": subagent_name,
                "node": node_name,
            },
        )
        return

    # Sub-agent / escalation nodes: do nothing here. Their events are
    # emitted in real time by the `messages` branch (with subgraphs=True).


def _iter_message_events(
    msg: Any,
    node_name: str,
    ctx: "_StreamCtx",
    recorder: TraceRecorder,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Real-time per-message events from stream_mode='messages'.

    Called the instant langgraph emits each message (AIMessage chunk,
    ToolMessage, etc.) — NOT delayed until the node finishes. This is
    what lets the front-end render tool-call steps one-by-one as they
    happen instead of all-at-once at node completion.

    Router node messages are skipped here (the router's step + route
    event are emitted from the updates branch using _iter_events).

    Trace recording is intentionally NOT done here: it's handled by
    `_consume` in the updates branch so the trace keeps a single
    consistent recording path.

    With `subgraphs=True`, langgraph streams LLM tokens as
    `AIMessageChunk` objects. We handle these specially:
      - Chunks carrying `tool_calls` (LLM decided to call a tool):
        emit a `step_start` for each tool call. This is the KEY event
        for real-time UX — it fires the instant the LLM decides to
        call a tool, before the tool actually runs.
      - Chunks carrying only `content` (final-answer tokens): emit a
        `token` event so the front-end CAN stream text if it wants.
        The front-end currently ignores these and relies on the
        `final` event + typing animation, but the option is there.
      - The complete `AIMessage` (emitted at end of LLM call): skip
        if we already handled the tool_calls via a chunk, otherwise
        handle as before (covers non-streaming LLM providers).
    """
    # Skip router node — its step + route event come from the updates branch.
    if node_name == "router":
        return

    msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()
    # Detect streaming chunks. langchain's AIMessageChunk.type is "ai"
    # (same as AIMessage), so we also check the class name.
    is_chunk = "chunk" in msg.__class__.__name__.lower()

    if is_chunk:
        tool_calls = getattr(msg, "tool_calls", None) or []
        content = getattr(msg, "content", "") or ""
        if tool_calls:
            # LLM decided to call one or more tools — emit a step_start
            # for each tool call the instant the decision is made.
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                # Avoid duplicate: if we already created a pending step for
                # this tool_call_id (e.g. from a prior chunk), skip.
                if tc_id and tc_id in ctx.pending_tool_steps:
                    continue
                step_id = ctx.next_step_id()
                ctx.start_times[step_id] = time.time()
                if tc_id:
                    ctx.pending_tool_steps[tc_id] = step_id
                ctx.num_tools += 1
                ctx.num_steps += 1
                yield (
                    "step_start",
                    {
                        "step_id": step_id,
                        "step_type": "tool_call",
                        "friendly_message": _friendly_for_tool(
                            tc.get("name", "unknown"), tc.get("args", {}) or {}
                        ),
                        "tool_name": tc.get("name", "unknown"),
                        "tool_args": tc.get("args", {}) or {},
                        "node": node_name,
                    },
                )
        elif content:
            # Final-answer token chunk — emit a `token` event for optional
            # streaming text display. Front-end can ignore if it prefers
            # to wait for the `final` event + typing animation.
            yield (
                "token",
                {"content": str(content), "node": node_name},
            )
        return

    if msg_type in {"ai", "aimessage"}:
        tool_calls = getattr(msg, "tool_calls", None) or []
        content = getattr(msg, "content", "") or ""

        if tool_calls:
            # Complete AIMessage with tool_calls. If we already emitted
            # step_start from a chunk, skip (avoid duplicates). Otherwise
            # emit step_start for each tool call.
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or ""
                if tc_id and tc_id in ctx.pending_tool_steps:
                    # Already emitted step_start from a chunk — skip.
                    continue
                step_id = ctx.next_step_id()
                ctx.start_times[step_id] = time.time()
                if tc_id:
                    ctx.pending_tool_steps[tc_id] = step_id
                ctx.num_tools += 1
                ctx.num_steps += 1
                yield (
                    "step_start",
                    {
                        "step_id": step_id,
                        "step_type": "tool_call",
                        "friendly_message": _friendly_for_tool(
                            tc.get("name", "unknown"), tc.get("args", {}) or {}
                        ),
                        "tool_name": tc.get("name", "unknown"),
                        "tool_args": tc.get("args", {}) or {},
                        "node": node_name,
                    },
                )
        elif content:
            # Final-answer AIMessage (no tool_calls alongside) — emit a
            # paired llm_call step_start + step_end. This covers the case
            # where the LLM provider doesn't stream tokens (no chunks).
            step_id = ctx.next_step_id()
            ctx.start_times[step_id] = time.time()
            ctx.num_llm += 1
            ctx.num_steps += 1
            yield (
                "step_start",
                {
                    "step_id": step_id,
                    "step_type": "llm_call",
                    "friendly_message": "正在分析并生成回复...",
                    "node": node_name,
                },
            )
            latency_ms = round(
                (time.time() - ctx.start_times.pop(step_id, time.time())) * 1000, 1
            )
            yield (
                "step_end",
                {
                    "step_id": step_id,
                    "preview": str(content)[:300],
                    "latency_ms": latency_ms,
                    "node": node_name,
                },
            )

    elif msg_type in {"tool", "toolmessage"}:
        tc_id = getattr(msg, "tool_call_id", "") or ""
        step_id = ctx.pending_tool_steps.pop(tc_id, None)
        if step_id is None:
            # No matching pending step (e.g. tool message from a prior turn
            # replayed by the checkpointer) — emit a fresh step pair.
            step_id = ctx.next_step_id()
            ctx.start_times[step_id] = time.time()
            ctx.num_tools += 1
            ctx.num_steps += 1
            yield (
                "step_start",
                {
                    "step_id": step_id,
                    "step_type": "tool_call",
                    "friendly_message": "正在执行工具...",
                    "node": node_name,
                },
            )
        content = getattr(msg, "content", "")
        latency_ms = round(
            (time.time() - ctx.start_times.pop(step_id, time.time())) * 1000, 1
        )
        yield (
            "step_end",
            {
                "step_id": step_id,
                "preview": str(content)[:200],
                "latency_ms": latency_ms,
                "node": node_name,
            },
        )


def _consume(payload: Any, recorder: TraceRecorder) -> None:
    """Pull LLM/tool events out of one langgraph stream payload for tracing.

    Matches each ToolMessage with the args of the preceding AIMessage's
    tool_calls entry (keyed by tool_call_id) so the trace records not just
    the tool name but also the arguments the LLM chose.
    """
    if not isinstance(payload, dict):
        return
    messages = payload.get("messages") or []
    # Build a map tool_call_id -> args from any preceding AIMessage in this
    # payload so the matching ToolMessage can pick them up.
    pending_args: dict[str, dict[str, Any]] = {}
    for msg in messages:
        msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()
        if msg_type in {"ai", "aimessage"}:
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if isinstance(tc, dict) and tc.get("id"):
                    pending_args[tc["id"]] = dict(tc.get("args", {}) or {})
            recorder.record_llm_call(msg, latency_ms=0.0)
        elif msg_type in {"tool", "toolmessage"}:
            name = getattr(msg, "name", "unknown") or "unknown"
            result = getattr(msg, "content", "")
            tc_id = getattr(msg, "tool_call_id", "") or ""
            args = pending_args.get(tc_id, {})
            recorder.record_tool_call(name, args, result, latency_ms=0.0)
