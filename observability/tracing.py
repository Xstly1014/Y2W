"""Agent invocation tracing.

Wraps an agent call to capture, for ONE user input:
  * every LLM call (latency, token usage, cost)
  * every tool call (name, args, result, latency)
  * the final answer
  * total latency & total cost

Traces are appended to `data/traces/<thread_id>.jsonl`, one trace per line.
When the eval runner captures a badcase, it can attach the trace id so
you can later replay exactly what went wrong.

Implementation note: we use `agent.stream(...)` instead of `agent.invoke(...)`
because stream emits intermediate events (model output, tool calls, tool
outputs) that invoke hides. We collect them and also assemble the final
answer from the last AI message.

Future expansion hooks:
  * LangSmith / LangFuse export (set env, swap writer)
  * replay() function to re-run a trace for debugging
  * live tail via websocket for a dashboard
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage

from config import settings
from observability.cost import estimate_cost, extract_usage

logger = logging.getLogger(__name__)


def _traces_dir() -> Path:
    path = Path(settings.vector_store_dir).parent / "traces"
    path.mkdir(parents=True, exist_ok=True)
    return path


# One lock per trace file path so concurrent TraceRecorders writing to the
# same thread_id file don't interleave bytes.
_TRACE_FILE_LOCKS: dict[str, Lock] = {}
_TRACE_FILE_LOCKS_GUARD = Lock()


def _trace_file_lock(path: Path) -> Lock:
    key = str(path)
    with _TRACE_FILE_LOCKS_GUARD:
        lock = _TRACE_FILE_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _TRACE_FILE_LOCKS[key] = lock
        return lock


class TraceRecorder:
    """Accumulates steps for one agent invocation and writes the trace to disk."""

    def __init__(
        self,
        thread_id: str,
        model_name: str,
        tenant_id: str | None = None,
    ) -> None:
        self.trace_id = str(uuid4())
        self.thread_id = thread_id
        self.model_name = model_name
        # tenant_id is recorded so the history endpoint can filter traces by
        # tenant without relying solely on the ``tenant-<id>`` thread_id
        # naming convention (which not all callers follow — e.g. CLI chats
        # and the smoke-test script use arbitrary thread_ids).
        self.tenant_id = tenant_id
        self.started_at = time.time()
        self.steps: list[dict[str, Any]] = []
        self.user_input: str | None = None
        self.final_answer: str | None = None
        self.error: str | None = None

    # ----- recorders -----
    def record_llm_call(self, message: Any, latency_ms: float) -> None:
        usage = extract_usage(message)
        cost = estimate_cost(
            usage["input_tokens"], usage["output_tokens"], self.model_name
        )
        tool_calls_raw = getattr(message, "tool_calls", None)
        # Defensive: some providers return non-list values; normalise.
        if not isinstance(tool_calls_raw, list):
            tool_calls_raw = []
        self.steps.append(
            {
                "type": "llm_call",
                "latency_ms": round(latency_ms, 1),
                "tokens_in": usage["input_tokens"],
                "tokens_out": usage["output_tokens"],
                "cost_usd": round(cost, 6),
                # Tool calls requested by this LLM turn, if any.
                "tool_calls": [
                    {"name": tc.get("name", ""), "args": tc.get("args", {}) or {}}
                    for tc in tool_calls_raw
                    if isinstance(tc, dict)
                ],
            }
        )

    def record_tool_call(
        self, name: str, args: dict, result: str, latency_ms: float
    ) -> None:
        # Truncate huge tool results so the trace file stays readable.
        result_str = str(result)
        if len(result_str) > 500:
            result_str = result_str[:500] + f"... [+{len(result_str) - 500} chars]"
        self.steps.append(
            {
                "type": "tool_call",
                "name": name,
                "args": args or {},
                "result": result_str,
                "latency_ms": round(latency_ms, 1),
            }
        )

    def finalize(self, final_answer: str, error: str | None = None) -> dict[str, Any]:
        finished_at = time.time()
        total_latency = round((finished_at - self.started_at) * 1000, 1)
        total_tokens_in = sum(s.get("tokens_in", 0) for s in self.steps)
        total_tokens_out = sum(s.get("tokens_out", 0) for s in self.steps)
        total_cost = sum(s.get("cost_usd", 0.0) for s in self.steps)
        self.final_answer = final_answer
        self.error = error
        trace = {
            "trace_id": self.trace_id,
            "thread_id": self.thread_id,
            "tenant_id": self.tenant_id,
            "model": self.model_name,
            "user_input": self.user_input,
            "final_answer": self.final_answer,
            "error": self.error,
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat(),
            "finished_at": datetime.fromtimestamp(finished_at, tz=timezone.utc).isoformat(),
            "total_latency_ms": total_latency,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_cost_usd": round(total_cost, 6),
            "num_steps": len(self.steps),
            "steps": self.steps,
        }
        self._write(trace)
        return trace

    def _write(self, trace: dict[str, Any]) -> None:
        path = _traces_dir() / f"{self.thread_id}.jsonl"
        line = json.dumps(trace, ensure_ascii=False) + "\n"
        lock = _trace_file_lock(path)
        with lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
        logger.debug("trace %s written to %s", self.trace_id, path)


def trace_invocation(
    agent: Any,
    user_input: str,
    *,
    thread_id: str = "default",
    model_name: str | None = None,
) -> tuple[str, TraceRecorder]:
    """Run the agent with tracing. Returns (final_answer, recorder).

    Use this anywhere you currently call `agent.invoke(...)` to get
    observability for free.
    """
    model_name = model_name or settings.llm_model_name
    recorder = TraceRecorder(thread_id=thread_id, model_name=model_name)
    recorder.user_input = user_input

    final_answer = ""
    try:
        # stream() emits dict events keyed by the current langgraph node.
        for event in agent.stream(
            {"messages": [HumanMessage(content=user_input)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            for _node_name, payload in event.items():
                _consume_payload(payload, recorder)
        # Re-read final answer from agent state. Walk backwards to skip
        # ToolMessage trailers (agent may have terminated abnormally right
        # after a tool call) and pick the last AIMessage's content.
        state = agent.get_state(config={"configurable": {"thread_id": thread_id}})
        messages = state.values.get("messages", []) if state and state.values else []
        for msg in reversed(messages):
            msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()
            if msg_type in {"ai", "aimessage"}:
                final_answer = getattr(msg, "content", str(msg))
                break
        else:
            if messages:
                final_answer = getattr(messages[-1], "content", str(messages[-1]))
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent invocation failed during trace")
        recorder.finalize(final_answer, error=f"{type(exc).__name__}: {exc}")
        raise
    else:
        recorder.finalize(final_answer)
    return final_answer, recorder


def _consume_payload(payload: Any, recorder: TraceRecorder) -> None:
    """Pull LLM/tool events out of one langgraph stream payload."""
    if not isinstance(payload, dict):
        return
    messages = payload.get("messages") or []
    for msg in messages:
        msg_type = getattr(msg, "type", "") or msg.__class__.__name__.lower()
        # ToolMessage = the result of a tool call.
        if msg_type in {"tool", "toolmessage"}:
            name = getattr(msg, "name", "unknown") or "unknown"
            result = getattr(msg, "content", "")
            # We don't have args here (they live on the preceding AIMessage's
            # tool_calls), but the name + result is usually enough for diagnosis.
            recorder.record_tool_call(name, {}, result, latency_ms=0.0)
        elif msg_type in {"ai", "aimessage"}:
            # An AIMessage may carry tool_calls (LLM decided to call a tool)
            # or be the final answer. We record every AI turn as an llm_call;
            # tool_calls inside it are listed.
            # Latency is not directly available; we approximate with 0.
            recorder.record_llm_call(msg, latency_ms=0.0)
