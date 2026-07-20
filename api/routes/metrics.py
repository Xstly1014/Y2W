"""Professional developer dashboard metrics API.

Exposes richer analytics than the existing dashboard_router:
latency percentiles, error / FCR / feedback rates, tool success rate,
RAG retrieval stats, multi-agent route distribution, conversation
aggregates, and per-day trends for latency / cost / feedback.

Data sources:
  * trace JSONL files under data/traces/<thread_id>.jsonl
  * feedback JSONL files under data/flywheel/{good,bad}cases.jsonl

All endpoints tolerate missing / extra / malformed fields in trace JSON:
per-trace errors are caught and skipped, never propagated to the caller.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query

from observability.tracing import _traces_dir

logger = logging.getLogger(__name__)

metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _load_all_traces() -> list[dict[str, Any]]:
    """Read every trace JSONL file under data/traces/. Returns a flat list.

    Each trace dict is augmented with `_file_mtime` (ISO string) so callers
    can fall back to it when `started_at` / `finished_at` are missing.
    Corrupt lines and unreadable files are silently skipped.

    The returned list is sorted by best-available timestamp (oldest first)
    so callers can take `[-N:]` to get the most recent N traces.
    """
    traces: list[dict[str, Any]] = []
    for f in _traces_dir().glob("*.jsonl"):
        try:
            mtime_iso = datetime.fromtimestamp(
                f.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        except OSError:
            mtime_iso = None
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("could not read trace file %s: %s", f, exc)
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(t, dict):
                continue
            if mtime_iso:
                # Use setdefault so a real trace timestamp is never overwritten.
                t.setdefault("_file_mtime", mtime_iso)
            traces.append(t)

    # Best-effort chronological ordering across files.
    def _sort_key(t: dict[str, Any]) -> str:
        for key in ("started_at", "finished_at", "_file_mtime"):
            v = t.get(key)
            if isinstance(v, str) and v:
                return v
        return ""

    traces.sort(key=_sort_key)
    return traces


def _filter_by_tenant(
    traces: list[dict[str, Any]], tenant_id: str | None
) -> list[dict[str, Any]]:
    """Best-effort tenant filter.

    Traces carrying an explicit `tenant_id` field are matched exactly.
    Traces without it fall back to a strict `tenant-<id>` prefix check on
    `thread_id` (mirrors api/routes/ops.py:_matches_tenant). Loose substring
    matching previously caused false positives — e.g. tenant "demo" matched
    threads "demo-rag" AND "demo-commerce". When `tenant_id` is None or
    empty, all traces are returned unchanged.
    """
    if not tenant_id:
        return traces
    out: list[dict[str, Any]] = []
    for t in traces:
        tid = t.get("tenant_id")
        if tid is not None:
            if str(tid) == tenant_id:
                out.append(t)
        else:
            thread_id = str(t.get("thread_id", ""))
            if (
                thread_id == f"tenant-{tenant_id}"
                or thread_id.startswith(f"tenant-{tenant_id}-")
            ):
                out.append(t)
    return out


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (numpy default). Returns 0.0 if empty."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = k - f
    if f + 1 < len(s):
        return float(s[f] + (s[f + 1] - s[f]) * c)
    return float(s[f])


def _parse_date(trace: dict[str, Any]) -> str | None:
    """Extract 'YYYY-MM-DD' from a trace.

    Tries `started_at`, then `finished_at`, then the file mtime the
    loader attached under `_file_mtime`. Returns None if nothing usable.
    """
    for key in ("started_at", "finished_at", "_file_mtime"):
        val = trace.get(key)
        if isinstance(val, str) and len(val) >= 10:
            return val[:10]
    return None


def _is_llm_step(step: dict[str, Any]) -> bool:
    """True for both 'llm' and 'llm_call' type steps (backward compatible)."""
    t = str(step.get("type", "")).lower()
    return t.startswith("llm")


def _is_tool_step(step: dict[str, Any]) -> bool:
    """True for both 'tool' and 'tool_call' type steps (backward compatible)."""
    t = str(step.get("type", "")).lower()
    return t.startswith("tool")


def _step_name(step: dict[str, Any]) -> str:
    return str(step.get("name") or "unknown")


def _step_preview(step: dict[str, Any]) -> str:
    """Tool result preview: prefer `result_preview`, fall back to `result`."""
    for key in ("result_preview", "result", "preview"):
        v = step.get(key)
        if isinstance(v, str):
            return v
        if v is not None:
            return str(v)
    return ""


def _load_all_feedback() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load (good_cases, bad_cases) from the flywheel stores.

    Never raises — returns ([], []) if the collector cannot be built.
    """
    try:
        from api.deps import get_collector
        collector = get_collector()
        good = list(collector.good_store.iter_records())
        bad = list(collector.bad_store.iter_records())
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not load flywheel feedback: %s", exc)
        return [], []
    return good, bad


def _feedback_date(rec: dict[str, Any]) -> str | None:
    ts = rec.get("timestamp")
    if isinstance(ts, str) and len(ts) >= 10:
        return ts[:10]
    return None


def _feedback_trace_id(rec: dict[str, Any]) -> str | None:
    meta = rec.get("metadata") or {}
    tid = meta.get("trace_id")
    if tid:
        return str(tid)
    return None


def _filter_feedback_by_tenant(
    records: list[dict[str, Any]], tenant_id: str | None
) -> list[dict[str, Any]]:
    """Filter flywheel feedback records by tenant.

    Mirrors the trace filter: exact match on `metadata.tenant_id`, else
    strict `tenant-<id>` prefix check on `metadata.thread_id`. Loose
    substring match previously caused false positives (e.g. tenant "demo"
    matched "demo-rag" and "demo-commerce").
    """
    if not tenant_id:
        return records
    out: list[dict[str, Any]] = []
    for rec in records:
        meta = rec.get("metadata") or {}
        rec_tid = meta.get("tenant_id")
        if rec_tid is not None:
            if str(rec_tid) == tenant_id:
                out.append(rec)
            continue
        thread_id = str(meta.get("thread_id", ""))
        if (
            thread_id == f"tenant-{tenant_id}"
            or thread_id.startswith(f"tenant-{tenant_id}-")
        ):
            out.append(rec)
    return out


def _last_n_days(n: int) -> list[str]:
    """Return list of date strings 'YYYY-MM-DD' for the last n days, oldest first."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
@metrics_router.get("/overview")
def overview(tenant_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Aggregate developer KPIs: latency percentiles, error / FCR / feedback rates."""
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    total = len(traces)
    if total == 0:
        return {
            "total_traces": 0,
            "total_errors": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "p99_latency_ms": 0.0,
            "avg_steps": 0.0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_cost_usd": 0.0,
            "fcr_rate": 0.0,
            "feedback_rate": 0.0,
            "feedback_positive_rate": 0.0,
            "tool_success_rate": 0.0,
        }

    errors = sum(1 for t in traces if t.get("error"))
    latencies = [float(t.get("total_latency_ms", 0) or 0) for t in traces]
    steps_counts = [int(t.get("num_steps", 0) or 0) for t in traces]
    tokens_in = sum(int(t.get("total_tokens_in", 0) or 0) for t in traces)
    tokens_out = sum(int(t.get("total_tokens_out", 0) or 0) for t in traces)
    cost = sum(float(t.get("total_cost_usd", 0.0) or 0.0) for t in traces)

    # FCR approximation: exactly 1 LLM step AND no error.
    fcr_count = 0
    for t in traces:
        if t.get("error"):
            continue
        steps = t.get("steps") or []
        llm_count = sum(1 for s in steps if _is_llm_step(s))
        if llm_count == 1:
            fcr_count += 1

    # Tool success rate: tool calls in error-free traces / total tool calls.
    # Uses trace.error as a proxy since per-step error tracking is not available.
    total_tool_calls = 0
    failed_tool_calls = 0
    for t in traces:
        steps = t.get("steps") or []
        tool_calls = sum(1 for s in steps if _is_tool_step(s))
        total_tool_calls += tool_calls
        if t.get("error"):
            failed_tool_calls += tool_calls

    # Feedback stats (filtered by tenant — without this, every tenant's
    # dashboard showed the global feedback pool).
    good_cases, bad_cases = _load_all_feedback()
    good_cases = _filter_feedback_by_tenant(good_cases, tenant_id)
    bad_cases = _filter_feedback_by_tenant(bad_cases, tenant_id)
    feedback_trace_ids: set[str] = set()
    for rec in good_cases + bad_cases:
        tid = _feedback_trace_id(rec)
        if tid:
            feedback_trace_ids.add(tid)
    traces_with_feedback = sum(
        1 for t in traces if t.get("trace_id") in feedback_trace_ids
    )
    good = len(good_cases)
    bad = len(bad_cases)
    feedback_total = good + bad

    return {
        "total_traces": total,
        "total_errors": errors,
        "error_rate": round(errors / total, 4),
        "avg_latency_ms": round(sum(latencies) / total, 2),
        "p50_latency_ms": round(_percentile(latencies, 50), 2),
        "p95_latency_ms": round(_percentile(latencies, 95), 2),
        "p99_latency_ms": round(_percentile(latencies, 99), 2),
        "avg_steps": round(sum(steps_counts) / total, 2),
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "total_cost_usd": round(cost, 6),
        "fcr_rate": round(fcr_count / total, 4),
        "feedback_rate": round(traces_with_feedback / total, 4),
        "feedback_positive_rate": round(good / feedback_total, 4) if feedback_total else 0.0,
        "tool_success_rate": round(
            (total_tool_calls - failed_tool_calls) / total_tool_calls, 4
        ) if total_tool_calls else 0.0,
    }


# --------------------------------------------------------------------------- #
# Latency trend
# --------------------------------------------------------------------------- #
@metrics_router.get("/latency-trend")
def latency_trend(
    days: int = Query(default=14, ge=1, le=90),
    tenant_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Daily latency buckets (avg / p50 / p95) for the last N days."""
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in traces:
        date = _parse_date(t)
        if not date:
            continue
        buckets[date].append(float(t.get("total_latency_ms", 0) or 0))

    days_out: list[dict[str, Any]] = []
    for date in _last_n_days(days):
        vals = buckets.get(date, [])
        if vals:
            days_out.append({
                "date": date,
                "count": len(vals),
                "avg_ms": round(sum(vals) / len(vals), 2),
                "p50_ms": round(_percentile(vals, 50), 2),
                "p95_ms": round(_percentile(vals, 95), 2),
            })
        else:
            days_out.append({
                "date": date,
                "count": 0,
                "avg_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
            })
    return {"days": days_out}


# --------------------------------------------------------------------------- #
# Cost trend
# --------------------------------------------------------------------------- #
@metrics_router.get("/cost-trend")
def cost_trend(
    days: int = Query(default=14, ge=1, le=90),
    tenant_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Daily token & cost buckets for the last N days."""
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    buckets: dict[str, list[dict[str, float]]] = defaultdict(list)
    for t in traces:
        date = _parse_date(t)
        if not date:
            continue
        buckets[date].append({
            "tokens_in": float(t.get("total_tokens_in", 0) or 0),
            "tokens_out": float(t.get("total_tokens_out", 0) or 0),
            "cost_usd": float(t.get("total_cost_usd", 0.0) or 0.0),
        })

    days_out: list[dict[str, Any]] = []
    for date in _last_n_days(days):
        rows = buckets.get(date, [])
        if rows:
            days_out.append({
                "date": date,
                "count": len(rows),
                "tokens_in": int(sum(r["tokens_in"] for r in rows)),
                "tokens_out": int(sum(r["tokens_out"] for r in rows)),
                "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
            })
        else:
            days_out.append({
                "date": date,
                "count": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
            })
    return {"days": days_out}


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@metrics_router.get("/tools")
def tools(tenant_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Per-tool aggregate: call count, avg latency, error count.

    Error count is estimated from the trace-level `error` flag: every
    tool call in an errored trace is counted as errored.
    """
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "latency_sum": 0.0, "error_count": 0}
    )
    for t in traces:
        has_error = bool(t.get("error"))
        for step in t.get("steps") or []:
            if not _is_tool_step(step):
                continue
            name = _step_name(step)
            agg[name]["calls"] += 1
            agg[name]["latency_sum"] += float(step.get("latency_ms", 0) or 0)
            if has_error:
                agg[name]["error_count"] += 1

    tools_out: list[dict[str, Any]] = []
    for name, stats in sorted(agg.items(), key=lambda kv: kv[1]["calls"], reverse=True):
        calls = stats["calls"]
        tools_out.append({
            "name": name,
            "calls": calls,
            "avg_latency_ms": round(stats["latency_sum"] / calls, 2) if calls else 0.0,
            "error_count": stats["error_count"],
        })
    return {"tools": tools_out}


# --------------------------------------------------------------------------- #
# Multi-agent routes
# --------------------------------------------------------------------------- #
@metrics_router.get("/agents")
def agents(tenant_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Per-route distribution for multi-agent architectures.

    Traces currently do not carry a `route` field. When none of the loaded
    traces has one, returns `available: false` and an empty list so the
    front-end can degrade gracefully. Once traces populate `route`
    (order_ops / knowledge / escalation), the same endpoint lights up.
    """
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    available = any("route" in t for t in traces)
    if not available:
        return {"available": False, "routes": []}

    agg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in traces:
        route = t.get("route")
        if not route:
            continue
        agg[str(route)].append(t)

    routes_out: list[dict[str, Any]] = []
    for route, items in agg.items():
        latencies = [float(t.get("total_latency_ms", 0) or 0) for t in items]
        steps_counts = [int(t.get("num_steps", 0) or 0) for t in items]
        errors = sum(1 for t in items if t.get("error"))
        routes_out.append({
            "route": route,
            "count": len(items),
            "avg_latency_ms": round(sum(latencies) / len(items), 2) if items else 0.0,
            "avg_steps": round(sum(steps_counts) / len(items), 2) if items else 0.0,
            "error_count": errors,
        })
    return {"available": True, "routes": routes_out}


# --------------------------------------------------------------------------- #
# Retrieval (RAG)
# --------------------------------------------------------------------------- #
@metrics_router.get("/retrieval")
def retrieval(tenant_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Basic RAG retrieval stats. Not a true recall@k (needs labelled data).

    `empty_rate` uses `result_preview` length < 10 as a heuristic for
    "no useful result returned". Once we have ground-truth relevance
    labels, this endpoint can be extended to compute recall@k / MRR.
    """
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    rag_calls: list[dict[str, Any]] = []
    for t in traces:
        for step in t.get("steps") or []:
            if not _is_tool_step(step):
                continue
            if _step_name(step) != "rag_search":
                continue
            preview = _step_preview(step)
            rag_calls.append({
                "_sort": t.get("started_at") or t.get("finished_at") or t.get("_file_mtime") or "",
                "trace_id": t.get("trace_id"),
                "user_input": t.get("user_input"),
                "preview": preview,
                "latency_ms": float(step.get("latency_ms", 0) or 0),
            })

    rag_calls.sort(key=lambda c: c.get("_sort", ""))
    total = len(rag_calls)
    empty = sum(1 for c in rag_calls if len(c["preview"]) < 10)
    avg_latency = (
        sum(c["latency_ms"] for c in rag_calls) / total if total else 0.0
    )
    avg_len = (
        sum(len(c["preview"]) for c in rag_calls) / total if total else 0.0
    )
    recent = [
        {k: v for k, v in c.items() if k != "_sort"}
        for c in rag_calls[-20:]
    ]
    return {
        "total_rag_calls": total,
        "avg_rag_latency_ms": round(avg_latency, 2),
        "empty_rate": round(empty / total, 4) if total else 0.0,
        "avg_preview_length": round(avg_len, 2),
        "recent_calls": recent,
    }


# --------------------------------------------------------------------------- #
# Conversations
# --------------------------------------------------------------------------- #
@metrics_router.get("/conversations")
def conversations(tenant_id: str | None = Query(default=None)) -> dict[str, Any]:
    """Per-thread aggregates: turns, last active, total cost."""
    traces = _filter_by_tenant(_load_all_traces(), tenant_id)
    by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in traces:
        tid = t.get("thread_id")
        if tid:
            by_thread[str(tid)].append(t)

    total_conversations = len(by_thread)
    turns = [len(items) for items in by_thread.values()]
    avg_turns = sum(turns) / total_conversations if total_conversations else 0.0

    def _thread_last_active(items: list[dict[str, Any]]) -> str:
        best = ""
        for t in items:
            for key in ("started_at", "finished_at", "_file_mtime"):
                v = t.get(key)
                if isinstance(v, str) and v > best:
                    best = v
        return best

    sorted_threads = sorted(
        by_thread.items(),
        key=lambda kv: _thread_last_active(kv[1]),
        reverse=True,
    )
    recent_conversations: list[dict[str, Any]] = []
    for thread_id, items in sorted_threads[:10]:
        total_cost = sum(float(t.get("total_cost_usd", 0.0) or 0.0) for t in items)
        recent_conversations.append({
            "thread_id": thread_id,
            "turns": len(items),
            "last_active": _thread_last_active(items),
            "total_cost_usd": round(total_cost, 6),
        })

    return {
        "total_conversations": total_conversations,
        "avg_turns_per_conversation": round(avg_turns, 2),
        "recent_conversations": recent_conversations,
    }


# --------------------------------------------------------------------------- #
# Feedback trend
# --------------------------------------------------------------------------- #
@metrics_router.get("/feedback-trend")
def feedback_trend(
    days: int = Query(default=14, ge=1, le=90),
    tenant_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Daily good / bad feedback buckets for the last N days."""
    good_cases, bad_cases = _load_all_feedback()
    good_cases = _filter_feedback_by_tenant(good_cases, tenant_id)
    bad_cases = _filter_feedback_by_tenant(bad_cases, tenant_id)

    good_by_date: dict[str, int] = defaultdict(int)
    bad_by_date: dict[str, int] = defaultdict(int)
    for rec in good_cases:
        d = _feedback_date(rec)
        if d:
            good_by_date[d] += 1
    for rec in bad_cases:
        d = _feedback_date(rec)
        if d:
            bad_by_date[d] += 1

    days_out: list[dict[str, Any]] = []
    for date in _last_n_days(days):
        g = good_by_date.get(date, 0)
        b = bad_by_date.get(date, 0)
        total = g + b
        days_out.append({
            "date": date,
            "good": g,
            "bad": b,
            "total": total,
            "positive_rate": round(g / total, 4) if total else 0.0,
        })
    return {"days": days_out}
