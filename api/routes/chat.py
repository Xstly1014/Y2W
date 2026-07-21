"""Chat routes: streaming SSE + simple invoke.

POST /api/chat            -> non-streaming invoke, returns final answer + trace_id
POST /api/chat/stream     -> SSE stream of routing + step + final events
GET  /api/chat/conversations/{thread_id}/history  -> retrieve prior messages

SSE event types emitted on /stream:
    meta        : session metadata (thread_id, tenant_id)
    route       : router decision (route, route_reason, subagent_name)
    step_start  : a tool call / llm call / agent-think begins
    step_end    : the matching step ends (preview, latency_ms)
    action_card : Kiki-style "帮我操作" button (one event per button)
    final       : the final answer (answer, trace_id, num_steps, ok, action_cards)
    summary     : aggregated stats (total_latency_ms, num_tools_called, ...)
    error       : failure (message, trace_id)

Each event dict is JSON-encoded into the SSE `data:` field. The router node
emits its own agent_think step plus a `route` event before the chosen
subagent node runs, so the front-end can render "已转交订单专员处理" in
real time.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
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

# Kiki-mode UX tuning: how long the router step stays in the "running"
# state before flipping to "done" with the sub-agent handoff. Bumped
# 0.7s -> 0.9s so the "正在判断" beat is unmistakably visible — too
# short and the user can't read the line before the route event
# arrives. The user explicitly asked for "把耗时的 LLM 延迟匀到前面
# 的不耗时操作上", so we make the front-end steps feel like deliberate
# thinking beats, not a flash.
_ROUTER_STEP_HOLD_S: float = float(os.getenv("KIKI_ROUTER_HOLD_S", "0.9"))

# Kiki-mode UX tuning: minimum visible duration for ANY step (router,
# sub-agent think, tool call, etc.) measured from step_start to
# step_end. Bumped 0.7s -> 0.9s. The user asked for "把耗时的 LLM
# 延迟匀到前面的不耗时操作上" — distribute the slow LLM's delay
# onto the front fast steps. We do this by making every step hold
# for at least 0.9s of "thinking" time, so a 5s LLM call doesn't
# surprise the user after a series of 50ms holds. Combined with the
# per-step budget cap below, this means a 5s LLM call no longer
# surprises the user after a series of 0.05s holds.
_MIN_STEP_DURATION_S: float = float(os.getenv("KIKI_MIN_STEP_DURATION_S", "0.9"))

# Kiki-mode UX tuning: how long the "visual gap" between consecutive
# steps is. In pacing v5 the gap is folded INTO the front-loaded
# hold of the next step_start (computed from `last_step_end_at`),
# so this constant is no longer used at the event_gen layer. We
# keep it for now in case any external code reads it; the
# front-loaded hold naturally produces the same 0.2s beat for fast
# steps because the step_start sleep floors at _MIN_HOLD_FLOOR_S
# (0.25s) anyway. See the comment block in event_gen for the full
# rationale.
_STEP_TRANSITION_GAP_S: float = float(os.getenv("KIKI_STEP_GAP_S", "0.2"))  # noqa: F841

# Kiki-mode UX tuning: total visible budget for the whole stream.
# Each step's hold = min(MIN_STEP, remaining_budget / steps_left) so
# a long LLM call doesn't get preceded by a bunch of gratuitous holds
# (which would inflate total wall-clock). When the budget is fresh,
# fast steps hold up to MIN_STEP; once we've spent most of the budget
# (e.g. on a slow LLM), later fast steps get the floor instead. This
# is what the user asked for as "把耗时的 LLM 延迟匀到前面的不耗时
# 操作上" — we don't actually pre-pay, but we make the per-step hold
# proportional to how much budget is left, so a 5s LLM doesn't
# surprise the user after several 50ms steps. Bumped 8.0s -> 10.0s
# so the user has room to wait through a slow LLM call without
# every later step skipping its hold.
_TARGET_TOTAL_S: float = float(os.getenv("KIKI_TARGET_TOTAL_S", "10.0"))

# Estimated total step count for a typical Kiki pipeline (router +
# LLM + tool call + LLM + final). Used to compute steps_left in the
# per-step budget. Slight over-estimate is fine — the budget will
# simply have leftover slack we don't spend. Lowered 5 -> 4 so the
# per-step budget is more generous (10.0s / 4 = 2.5s/step cap) and
# the user can still feel each step's "thinking" beat even when
# steps_left is high late in the stream.
_TYPICAL_STEPS: int = int(os.getenv("KIKI_TYPICAL_STEPS", "4"))

# Hard lower bound on per-step hold. Even if the budget calculation
# would say "hold for 0.05s", we floor at this value so a step
# doesn't appear and disappear in a single frame. Bumped 0.25s ->
# 0.3s so the smallest visible beat is still clearly perceptible.
_MIN_HOLD_FLOOR_S: float = float(os.getenv("KIKI_MIN_HOLD_FLOOR_S", "0.3"))

# --------------------------------------------------------------------------- #
# Dynamic friendly-message generation (Kiki mode)
# --------------------------------------------------------------------------- #
# The hard-coded _friendly_for_tool mapping below is fast and reliable, but
# the user asked for "in干什么就说什么" (whatever I'm doing, just say it) —
# so we try to refine the static text with a short, fast LLM call the first
# time a (tool_name, args_hash) pair is seen. The refined text is cached in
# an OrderedDict (LRU, 512 entries) and reused for the lifetime of the
# process. If the LLM call fails / times out, we fall back to the static
# mapping. This costs ~80ms per unique tool+arg combo (LLM call) and 0ms
# after that.
#
# NOTE: This is opt-in via env WEB_DYNAMIC_PROGRESS=1. Disabled by default
# because most callers find the static mapping good enough and don't want
# the extra LLM latency per tool call. The Kiki frontend layout itself
# doesn't depend on this — the static mapping also produces Kiki-style
# short Chinese phrases.
_FRIENDLY_CACHE: OrderedDict[str, str] = OrderedDict()
_FRIENDLY_CACHE_MAX = 512
_FRIENDLY_CACHE_GUARD = threading.Lock()
_FRIENDLY_RE_PATTERN = re.compile(r"[\[（(].{0,40}[\]）)]|[#*`>|]+")


def _cache_get(key: str) -> str | None:
    with _FRIENDLY_CACHE_GUARD:
        if key in _FRIENDLY_CACHE:
            _FRIENDLY_CACHE.move_to_end(key)
            return _FRIENDLY_CACHE[key]
    return None


def _cache_set(key: str, value: str) -> None:
    with _FRIENDLY_CACHE_GUARD:
        _FRIENDLY_CACHE[key] = value
        _FRIENDLY_CACHE.move_to_end(key)
        while len(_FRIENDLY_CACHE) > _FRIENDLY_CACHE_MAX:
            _FRIENDLY_CACHE.popitem(last=False)


def _refine_friendly(tool_name: str, args: dict[str, Any], fallback: str) -> str:
    """Try to produce a 8-15 char Chinese progress phrase via fast LLM.

    Bounded: we don't await the LLM — we fire a background thread that
    updates the cache for the NEXT time the same (tool, args) is seen.
    The current step still uses the fallback, so this never adds
    latency to the first call. After the first call, the cache hit
    returns the refined text immediately.
    """
    if not getattr(settings, "web_dynamic_progress", False):
        return fallback
    # Build a stable key from tool name + sorted arg values.
    try:
        args_blob = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        args_blob = str(args)
    key_src = f"{tool_name}|{args_blob}"
    cache_key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    # Cache miss — fire-and-forget background refinement.
    def _bg_refine() -> None:
        try:
            from api.deps import get_llm
            llm = get_llm()
            prompt = (
                "你是一个 UI 进度提示生成器。给定一个工具调用，"
                "生成一个 8-15 字的中文短句，告诉用户 agent 正在做什么。"
                "风格：克制、专业、用户可读，不用 emoji。\n\n"
                f"工具名：{tool_name}\n"
                f"参数：{args_blob[:200]}\n\n"
                "只输出短句本身，不要任何标点、解释、markdown 标记。"
            )
            from langchain_core.messages import HumanMessage
            resp = llm.invoke([HumanMessage(content=prompt)])
            text = (getattr(resp, "content", "") or "").strip()
            # Strip any punctuation / brackets the LLM may have added.
            text = _FRIENDLY_RE_PATTERN.sub("", text).strip()
            # Hard cap to 20 chars.
            if len(text) > 20:
                text = text[:20]
            if text and text != fallback:
                _cache_set(cache_key, text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("friendly refine failed for %s: %s", tool_name, exc)
    threading.Thread(target=_bg_refine, daemon=True).start()
    return fallback


# --------------------------------------------------------------------------- #
# [ACTION] block extraction (Kiki mode)
# --------------------------------------------------------------------------- #
_ACTION_RE = re.compile(r"\[ACTION\]\s*(\{.*?\})\s*\[/ACTION\]", re.DOTALL)


def _extract_action_cards(answer: str) -> tuple[str, list[dict[str, str]]]:
    """Pull [ACTION]…[/ACTION] blocks out of the agent's final answer.

    Returns (cleaned_text, cards) where `cleaned_text` has all [ACTION]
    blocks removed (so the visible bubble doesn't echo the JSON), and
    `cards` is a list of validated action dicts. Invalid blocks are
    silently dropped (logged as warning).
    """
    cards: list[dict[str, str]] = []
    if not answer or "[ACTION]" not in answer:
        return answer or "", cards
    seen_ids: set[str] = set()

    def _repl(m: re.Match[str]) -> str:
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("invalid [ACTION] JSON: %s; raw=%r", exc, m.group(1)[:80])
            return ""
        if not isinstance(obj, dict):
            return ""
        aid = str(obj.get("id", "")).strip()
        label = str(obj.get("label", "")).strip()
        prompt = str(obj.get("prompt", "")).strip()
        if not aid or not label or not prompt:
            return ""
        # id pattern: ^[a-z][a-z0-9-]{1,40}$
        if not re.match(r"^[a-z][a-z0-9-]{1,40}$", aid):
            logger.warning("invalid [ACTION] id format: %r", aid)
            return ""
        if len(label) > 16:
            label = label[:16]
        if len(prompt) > 500:
            prompt = prompt[:500]
        if aid in seen_ids:
            return ""
        seen_ids.add(aid)
        cards.append({"id": aid, "label": label, "prompt": prompt})
        return ""

    cleaned = _ACTION_RE.sub(_repl, answer).strip()
    return cleaned, cards


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
async def chat(
    req: ChatRequest, x_tenant_id: str | None = Header(default=None)
) -> dict[str, Any]:
    """Non-streaming chat. Returns final answer + trace metadata.

    NOTE: this endpoint is `async def` because the multi-agent graph
    declares its router node as `async def router_node` (see
    `core/multi_agent.py` and the P0-2 rationale in
    `optimization_logs/2026-07-20/issues-and-fixes.md`). A sync
    `agent.stream()` call would fail with "No synchronous function
    provided to router" — we must drive the graph via `astream()`.
    Sync `get_state` is short-lived (no LLM call) so it's safe to call
    directly from the event loop.
    """
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
        action_cards: list[dict[str, str]] = []
        try:
            async for event in agent.astream(
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
            # Kiki-mode: pull [ACTION]…[/ACTION] blocks out of the final
            # answer so the visible answer doesn't echo the JSON payload.
            final_answer, action_cards = _extract_action_cards(final_answer)
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
                "action_cards": [],
            }
        recorder.finalize(final_answer)
        return {
            "answer": final_answer,
            "trace_id": recorder.trace_id,
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "num_steps": len(recorder.steps),
            "ok": True,
            "action_cards": action_cards,
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
                            # Kiki-mode pacing v5: "front-loaded" hold.
                            # The per-step hold used to be applied AFTER
                            # step_end (sleep 0.7s after a fast step
                            # before yielding step_end), but that meant
                            # the slow LLM call that came next still
                            # produced a visible "5s wait" for the user.
                            # Now we sleep BEFORE step_start instead,
                            # using (target_hold - elapsed_since_last_end)
                            # as the sleep. A slow LLM (5s) has already
                            # made us wait long enough → sleep 0, step
                            # snaps in. A fast LLM (50ms) → sleep the
                            # diff (~0.65s) so the user still sees a
                            # clear "thinking" beat. This is the user's
                            # request: "把耗时的 LLM 延迟匀到前面的不
                            # 耗时操作上".
                            if ev_type == "step_start":
                                now = time.time()
                                ref = (
                                    ctx.last_step_end_at
                                    if ctx.last_step_end_at is not None
                                    else ctx.stream_started_at
                                )
                                elapsed_since_ref = now - ref
                                stream_elapsed = now - ctx.stream_started_at
                                remaining_budget = max(
                                    0.0,
                                    _TARGET_TOTAL_S - stream_elapsed,
                                )
                                # If a slow LLM has already eaten
                                # most of the budget, fall back to the
                                # floor so we don't keep holding when
                                # the user is already past the target.
                                steps_left = max(
                                    1, _TYPICAL_STEPS - ctx.steps_completed
                                )
                                per_step_budget = remaining_budget / steps_left
                                target_hold = min(
                                    _MIN_STEP_DURATION_S, per_step_budget
                                )
                                target_hold = max(
                                    _MIN_HOLD_FLOOR_S, target_hold
                                )
                                # If the LLM was slow, elapsed already
                                # exceeds target → sleep 0. If the LLM
                                # was fast, sleep the gap. This is the
                                # whole point of pacing v5.
                                sleep_s = max(0.0, target_hold - elapsed_since_ref)
                                # Clamp to remaining budget so we don't
                                # blow past _TARGET_TOTAL_S when there
                                # are still several step_starts left.
                                if stream_elapsed + sleep_s > _TARGET_TOTAL_S:
                                    sleep_s = max(
                                        0.0, _TARGET_TOTAL_S - stream_elapsed
                                    )
                                if sleep_s > 0.01:
                                    await asyncio.sleep(sleep_s)
                            if ev_type == "step_end":
                                step_id = ev_data.get("step_id")
                                if step_id:
                                    started = ctx.start_times.get(step_id)
                                    if started is not None:
                                        # Recompute latency_ms so the
                                        # value on the wire matches the
                                        # visible duration (the actual
                                        # LLM/tool runtime, NOT the
                                        # pre-step sleep — that's a
                                        # frontend concern, recorded
                                        # separately by the front-end
                                        # if it wants).
                                        new_latency_ms = round(
                                            (time.time() - started) * 1000, 1
                                        )
                                        ev_data = {
                                            **ev_data,
                                            "latency_ms": new_latency_ms,
                                        }
                                        ctx.start_times.pop(step_id, None)
                                        ctx.steps_completed += 1
                                # Kiki pacing v5: record when this step
                                # ended so the NEXT step_start can
                                # compute its front-loaded hold. We
                                # NO LONGER sleep after step_end — the
                                # hold has moved to step_start, where
                                # it can absorb the slow-LLM wait
                                # instead of stacking on top of it.
                                ctx.last_step_end_at = time.time()
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
                        # `data` is {node_name: payload}. We only need the
                        # router node here for the `route` event + trace
                        # recording. Sub-agent (order_ops / knowledge) and
                        # escalation nodes' LLM/tool events are already
                        # recorded by the `messages` branch above (with
                        # real latency_ms) — calling `_consume` on them
                        # would double-record with latency=0.
                        #
                        # Kiki-mode streaming UX: the router node's
                        # step_start and step_end used to fire in the
                        # same millisecond, so the user saw three steps
                        # appear "instantly". We now deliberately emit
                        # step_start, sleep ~400ms, then emit step_end
                        # + route. This gives the front-end time to
                        # render the "正在判断" line in the running
                        # state before it flips to "已转交" — matching
                        # the Kiki reference where each step is visible
                        # for a beat before the next one appears.
                        for node_name, payload in data.items():
                            if node_name == "router":
                                _consume(payload, recorder)
                                # Register the router step_start so the
                                # front-end can render the "正在判断"
                                # line immediately, then hold the step
                                # visible for at least
                                # _ROUTER_STEP_HOLD_S (0.7s) so the
                                # user perceives a real beat of
                                # "thinking" before it flips to done.
                                #
                                # Pacing v5: the router step is the
                                # FIRST step in the stream, so there's
                                # no `last_step_end_at` yet — the
                                # front-loaded hold is meaningless for
                                # the router itself. We instead apply
                                # the hold BETWEEN step_start and
                                # step_end (the classical position) so
                                # the user always sees a clear
                                # "正在判断" beat before the route
                                # decision. For SUBSEQUENT steps the
                                # hold is computed in the messages
                                # branch (front-loaded, before
                                # step_start).
                                step_id = ctx.next_step_id()
                                ctx.start_times[step_id] = time.time()
                                ctx.num_llm += 1
                                ctx.num_steps += 1
                                yield {
                                    "event": "step_start",
                                    "data": json.dumps(
                                        {
                                            "step_id": step_id,
                                            "step_type": "agent_think",
                                            "friendly_message": "正在判断",
                                            "node": node_name,
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                                # The router's own LLM call has
                                # already happened (synchronously
                                # during the updates-mode dispatch),
                                # so this sleep IS the visible "正在
                                # 判断" beat. Bounded by remaining
                                # budget so we don't blow past
                                # _TARGET_TOTAL_S if the stream is
                                # already long.
                                stream_elapsed = time.time() - ctx.stream_started_at
                                router_budget = max(
                                    0.0, _TARGET_TOTAL_S - stream_elapsed
                                )
                                router_hold = max(
                                    _ROUTER_STEP_HOLD_S, _MIN_HOLD_FLOOR_S
                                )
                                router_hold = min(router_hold, router_budget)
                                if router_hold > 0.01:
                                    await asyncio.sleep(router_hold)
                                latency_ms = round(
                                    (time.time() - ctx.start_times.pop(step_id, time.time())) * 1000,
                                    1,
                                )
                                yield {
                                    "event": "step_end",
                                    "data": json.dumps(
                                        {
                                            "step_id": step_id,
                                            "preview": "",
                                            "latency_ms": latency_ms,
                                            "node": node_name,
                                        },
                                        ensure_ascii=False,
                                    ),
                                }
                                # Router step counts toward steps_completed
                                # so the per-step budget tapers correctly
                                # for the sub-agent steps that follow.
                                ctx.steps_completed += 1
                                # Kiki pacing v5: record when this step
                                # ended so the NEXT step_start can
                                # compute its front-loaded hold. We no
                                # longer sleep after step_end — the
                                # visual gap now lives in the front-
                                # loaded hold of the NEXT step_start
                                # (the route event arrives 0ms later,
                                # so the gap is naturally absorbed by
                                # the subsequent step's hold).
                                ctx.last_step_end_at = time.time()
                                route = payload.get("route") or ""
                                route_reason = payload.get("route_reason") or ""
                                subagent_name = payload.get("subagent_name") or ""
                                yield {
                                    "event": "route",
                                    "data": json.dumps(
                                        {
                                            "route": route,
                                            "route_reason": route_reason,
                                            "subagent_name": subagent_name,
                                            "node": node_name,
                                        },
                                        ensure_ascii=False,
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
                # Kiki-mode: pull [ACTION]…[/ACTION] blocks out of the final
                # answer. We also emit one `action_card` SSE event per card
                # so the front end can render each button the moment it's
                # known (without waiting for `summary` to drain).
                visible_answer, action_cards = _extract_action_cards(final_answer)
                for card in action_cards:
                    yield {
                        "event": "action_card",
                        "data": json.dumps(card, ensure_ascii=False),
                    }
                recorder.finalize(visible_answer)
                yield {
                    "event": "final",
                    "data": json.dumps(
                        {
                            "answer": visible_answer,
                            "trace_id": recorder.trace_id,
                            "num_steps": len(recorder.steps),
                            "ok": True,
                            "action_cards": action_cards,
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
    # --- web_ops tools (Kiki mode: AI operates web pages) ---
    if name == "web_open_url":
        url = a.get("url") or ""
        return f"正在打开网页 {url[:40]}..." if url else "正在打开网页..."
    if name == "web_extract_text":
        return "正在提取页面内容..."
    if name == "web_list_links":
        return "正在查看页面有哪些可点击的链接..."
    if name == "web_click":
        tgt = a.get("target") or ""
        return f"正在点击「{tgt[:20]}」..." if tgt else "正在点击页面元素..."
    if name == "web_fill":
        sel = a.get("selector") or ""
        return f"正在填写表单 {sel[:20]}..." if sel else "正在填写表单..."
    if name == "web_press_key":
        key = a.get("key") or ""
        return f"正在按键 {key}..." if key else "正在按键..."
    if name == "web_wait_for":
        tgt = a.get("target") or ""
        return f"正在等待「{tgt[:20]}」出现..." if tgt else "正在等待页面元素..."
    if name == "web_screenshot":
        return "正在截图..."
    return f"正在执行 {name}..."


_SUBAGENT_FRIENDLY: dict[str, str] = {
    "order_ops": "订单专员正在处理",
    "knowledge": "知识库专员正在检索",
    "escalation": "正在升级到人工客服",
    "router": "正在判断",
}


def _friendly_progress(tool_name: str, args: dict[str, Any]) -> str:
    """Return the Kiki-style progress phrase for a tool call.

    Uses the static mapping by default; if `WEB_DYNAMIC_PROGRESS=1`,
    tries to refine it via background LLM (first call returns the
    fallback, subsequent same-(tool,args) calls return the refined text
    from the LRU cache).
    """
    fallback = _friendly_for_tool(tool_name, args)
    return _refine_friendly(tool_name, args, fallback)


class _StreamCtx:
    """Mutable per-SSE-session counters and step-id timing.

    Also tracks the timestamp of the last processed message so we can
    estimate LLM-call latency (delta between consecutive messages) and
    stores tool_call args keyed by tool_call_id so the matching
    ToolMessage can record them in the trace.
    """

    def __init__(self) -> None:
        self.counter = 0
        self.start_times: dict[str, float] = {}
        # tool_call_id -> step_id: pairs an AIMessage.tool_calls entry with
        # its matching ToolMessage so we can emit a paired step_end.
        self.pending_tool_steps: dict[str, str] = {}
        # tool_call_id -> args: captured from the AIMessage that requested
        # the tool call, so the ToolMessage handler can record them in the
        # trace (ToolMessage itself only carries the result, not the args).
        self.tool_args: dict[str, dict[str, Any]] = {}
        # Kiki multi-turn: LLM can emit visible text (an interim answer)
        # ALONGSIDE tool_calls in the same ReAct turn. Streaming chunks
        # deliver content first, then tool_call chunks. We accumulate
        # content here; when a tool_call chunk arrives we flush the buffer
        # as an `interim_answer` event (so the front-end renders a mid-
        # conversation AI bubble before the next step starts). Content
        # that never gets flushed is the final answer (handled by the
        # `final` event from graph state).
        self.interim_buffer: str = ""
        # Timestamp of the last message we fully processed. Used to estimate
        # LLM-call latency as the delta between consecutive messages. The
        # first message (user input) has no prior message, so we seed it
        # with the SSE session start time.
        self.last_msg_time: float = time.perf_counter()
        self.num_tools = 0
        self.num_llm = 0
        self.num_steps = 0
        # Kiki-mode pacing v2: track the SSE session start so each
        # step's hold can be computed as a fraction of the remaining
        # total budget (instead of a flat per-step floor). This lets
        # us hold fast early steps longer (when the budget is fresh)
        # and shorter later (when a slow LLM has eaten the budget),
        # so the user never sees several 50ms steps followed by a
        # single 5s LLM wait — every step is visibly "thinking".
        self.stream_started_at: float = time.time()
        # Number of step_end events already emitted this session.
        # Used to estimate steps_left for the per-step budget calc.
        self.steps_completed: int = 0
        # Kiki-mode pacing v5: timestamp of the most recent step_end
        # (or stream start if no step has ended yet). Used by the
        # event_gen loop to "front-load" the per-step hold: instead
        # of sleeping AFTER step_end (which is invisible — the step
        # is already done and the user sees the next step snap in),
        # we sleep BEFORE step_start. That way a slow LLM call
        # (5s) naturally absorbs the per-step hold — we don't add
        # any extra sleep before the next step_start because the
        # LLM has already made us wait long enough. A fast LLM
        # (50ms) makes us sleep the difference (~0.65s) so the
        # user still perceives a clear "thinking" beat before the
        # next step appears. This is what the user asked for as
        # "把耗时的 LLM 延迟匀到前面的不耗时操作上".
        self.last_step_end_at: float | None = None

    def next_step_id(self) -> str:
        self.counter += 1
        return f"step-{self.counter}"

    def mark_msg_done(self) -> None:
        """Update last_msg_time to 'now' after fully processing a message."""
        self.last_msg_time = time.perf_counter()


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
                "friendly_message": "正在判断",
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

    Trace recording is done HERE (not in `_consume`) so we can pass
    real latency_ms computed from `time.perf_counter()` deltas:
      - LLM call latency ≈ delta between the current AIMessage and the
        previously-processed message (ToolMessage or user input).
      - Tool call latency = delta between the ToolMessage and the
        step_start time recorded when the matching AIMessage's tool_call
        was first seen (via chunk or complete AIMessage).

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
            # Kiki multi-turn: if content was accumulated before this
            # tool_call chunk, flush it as an interim_answer so the
            # front-end renders a visible AI bubble BEFORE the tool
            # runs (e.g. "我找到了活动页，下面帮你看看有哪些可以领取").
            if ctx.interim_buffer.strip():
                # De-dup: if the buffered interim text matches the
                # friendly_progress of the upcoming tool call exactly,
                # skip flushing. The progress card already surfaces
                # the phrase; rendering an extra interim bubble just
                # duplicates it. The mock LLM emits "narration" as
                # both the AIMessage content AND gets translated to
                # the same friendly_progress by _friendly_for_tool.
                norm = lambda s: re.sub(r'[。…，！？.,!?]+$', '', s).strip()
                first_tc = next((t for t in tool_calls if isinstance(t, dict)), None)
                next_friendly = ""
                if first_tc:
                    next_friendly = norm(_friendly_progress(
                        first_tc.get("name", "unknown"),
                        first_tc.get("args", {}) or {},
                    ))
                if norm(ctx.interim_buffer) == next_friendly:
                    ctx.interim_buffer = ""
                else:
                    yield (
                        "interim_answer",
                        {"answer": ctx.interim_buffer.strip(), "node": node_name},
                    )
                    ctx.interim_buffer = ""
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
                    ctx.tool_args[tc_id] = dict(tc.get("args", {}) or {})
                ctx.num_tools += 1
                ctx.num_steps += 1
                yield (
                    "step_start",
                    {
                        "step_id": step_id,
                        "step_type": "tool_call",
                        "friendly_message": _friendly_progress(
                            tc.get("name", "unknown"), tc.get("args", {}) or {}
                        ),
                        "tool_name": tc.get("name", "unknown"),
                        "tool_args": tc.get("args", {}) or {},
                        "node": node_name,
                    },
                )
        elif content:
            # Accumulate content chunks into the interim buffer. This
            # text is EITHER an interim answer (if a tool_call chunk
            # follows later) OR the final answer (handled by the `final`
            # event from graph state, not from this buffer). We buffer
            # instead of emitting `token` events because the front-end
            # renders interim/final answers as complete bubbles.
            ctx.interim_buffer += str(content)
        return

    if msg_type in {"ai", "aimessage"}:
        tool_calls = getattr(msg, "tool_calls", None) or []
        content = getattr(msg, "content", "") or ""

        # Record this LLM call with real latency. Latency is approximated
        # as the delta between the current message arrival and the
        # previously-processed message (ToolMessage or the SSE session
        # start for the first LLM call in a turn).
        llm_latency_ms = round(
            (time.perf_counter() - ctx.last_msg_time) * 1000, 1
        )
        recorder.record_llm_call(msg, latency_ms=max(0.0, llm_latency_ms))
        ctx.num_llm += 1
        ctx.mark_msg_done()

        if tool_calls:
            # Kiki multi-turn: flush any accumulated interim content.
            # For non-streaming providers (no chunks), the complete
            # AIMessage carries both content + tool_calls — the content
            # IS the interim answer. For streaming providers, chunks
            # already buffered the content; the complete message is a
            # duplicate (all tc_ids already pending) so we only flush
            # the buffer without double-counting content.
            has_new_tc = any(
                not (isinstance(tc, dict) and tc.get("id")
                     and tc.get("id") in ctx.pending_tool_steps)
                for tc in tool_calls
            )
            interim_text = ctx.interim_buffer.strip()
            ctx.interim_buffer = ""
            if has_new_tc and content.strip():
                # Non-streaming provider: content is right here in the
                # complete message.
                interim_text = (
                    (interim_text + "\n" + content).strip()
                    if interim_text else content.strip()
                )
            # De-dup: if the interim answer is byte-for-byte the same as
            # the friendly_progress that will fire for the next tool
            # call, drop it — the progress card already shows the same
            # phrase. Without this guard, mock LLM messages that pair a
            # tool_call with a narration (e.g. "正在查询订单 1001 状态..."
            # + query_order) would surface the phrase twice: once as an
            # interim bubble and once as a running step. The frontend
            # tried to de-dup but lost the race (interim arrives before
            # the step is pushed), so the duplicate slipped through.
            if interim_text and has_new_tc:
                norm = lambda s: re.sub(r'[。…，！？.,!?]+$', '', s).strip()
                next_friendly = norm(_friendly_progress(
                    tool_calls[0].get("name", "unknown") if isinstance(tool_calls[0], dict) else "unknown",
                    tool_calls[0].get("args", {}) if isinstance(tool_calls[0], dict) else {},
                ))
                if norm(interim_text) == next_friendly:
                    interim_text = ""
            if interim_text:
                yield (
                    "interim_answer",
                    {"answer": interim_text, "node": node_name},
                )
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
                    ctx.tool_args[tc_id] = dict(tc.get("args", {}) or {})
                ctx.num_tools += 1
                ctx.num_steps += 1
                yield (
                    "step_start",
                    {
                        "step_id": step_id,
                        "step_type": "tool_call",
                        "friendly_message": _friendly_progress(
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
            # Discard any interim buffer: the final answer comes from
            # graph state (the `final` event), not from streamed chunks.
            ctx.interim_buffer = ""
            step_id = ctx.next_step_id()
            ctx.start_times[step_id] = time.time()
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
            # NOTE: do NOT pop start_times here — the chat_stream
            # event_gen layer needs to peek it to enforce the
            # Kiki-mode _MIN_STEP_DURATION_S pacing before yielding
            # step_end. event_gen pops it after the sleep, so the
            # latency_ms reported here is a placeholder (0.0) and
            # will be corrected by event_gen before going on the
            # wire.
            yield (
                "step_end",
                {
                    "step_id": step_id,
                    "preview": str(content)[:300],
                    "latency_ms": 0.0,
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
        # Real tool latency: delta between ToolMessage arrival and the
        # step_start time recorded when the tool_call was first seen.
        # We capture this for the trace (uses the real duration) but
        # do NOT pop start_times here — the chat_stream event_gen
        # layer needs to peek it for the Kiki-mode min-step-duration
        # pacing. event_gen rewrites latency_ms to the post-pad
        # duration before the SSE step_end is emitted.
        tool_latency_ms = round(
            (time.time() - ctx.start_times.get(step_id, time.time())) * 1000, 1
        )
        tool_name = getattr(msg, "name", "unknown") or "unknown"
        tool_args = ctx.tool_args.pop(tc_id, {}) if tc_id else {}
        # Record the tool call in the trace with real latency + args.
        recorder.record_tool_call(
            tool_name, tool_args, content, latency_ms=max(0.0, tool_latency_ms)
        )
        ctx.mark_msg_done()
        yield (
            "step_end",
            {
                "step_id": step_id,
                "preview": str(content)[:200],
                "latency_ms": 0.0,  # placeholder; event_gen rewrites
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
