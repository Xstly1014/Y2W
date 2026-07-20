"""Feedback, traces, flywheel, dashboard, health routes."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

from api.deps import get_collector
from api.schemas import DashboardStats, FeedbackRequest, TraceQuery, validate_safe_id
from config import settings
from observability.tracing import _traces_dir

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Feedback → flywheel
# --------------------------------------------------------------------------- #
feedback_router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@feedback_router.post("")
def feedback(req: FeedbackRequest) -> dict[str, Any]:
    """Record a thumbs-up/down. y → good case, n → bad case (with trace_id)."""
    collector = get_collector()
    metadata: dict[str, Any] = {"source": "web"}
    if req.trace_id:
        metadata["trace_id"] = req.trace_id
    if req.thread_id:
        metadata["thread_id"] = req.thread_id
    if req.tenant_id:
        metadata["tenant_id"] = req.tenant_id
    collector.record_interaction(
        user_input=req.user_input,
        prediction=req.prediction,
        passed=req.passed,
        expected=req.expected,
        score=req.score,
        metadata=metadata,
    )
    return {
        "ok": True,
        "recorded_as": "good" if req.passed else "bad",
        "stats": collector.stats(),
    }


@feedback_router.get("")
def list_feedback(
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """List recorded feedback (bad + good cases) for the flywheel UI.

    Returns ``{"feedback": [...], "stats": {"bad": N, "good": N}}``. Each
    record carries the original fields written by ``record_interaction``
    (``id``, ``timestamp``, ``user_input``, ``prediction``, ``passed``,
    ``metadata``). Newest first.
    """
    collector = get_collector()
    bad = list(collector.bad_store.iter_records())
    good = list(collector.good_store.iter_records())
    all_feedback = bad + good
    all_feedback.sort(
        key=lambda r: str(r.get("timestamp") or ""),
        reverse=True,
    )
    return {
        "feedback": all_feedback[:limit],
        "stats": collector.stats(),
    }


# --------------------------------------------------------------------------- #
# Traces
# --------------------------------------------------------------------------- #
traces_router = APIRouter(prefix="/api/traces", tags=["traces"])


def _matches_tenant(trace: dict[str, Any], tenant_id: str) -> bool:
    """Tighter tenant match: prefer the explicit tenant_id field, then fall
    back to a `tenant-<id>` prefix check on thread_id (the convention used by
    api.deps). Loose substring matching caused false positives (e.g. tenant
    "demo" matched thread "demo-rag" AND "demo-commerce").
    """
    tid = trace.get("tenant_id")
    if tid is not None:
        return str(tid) == tenant_id
    thread_id = str(trace.get("thread_id", ""))
    return thread_id == f"tenant-{tenant_id}" or thread_id.startswith(f"tenant-{tenant_id}-")


@traces_router.get("")
def list_traces(
    thread_id: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """List traces, optionally filtered by thread_id. Sorted by latency desc."""
    # Validate IDs before they hit the filesystem — a crafted thread_id like
    # ``../../etc/passwd`` would otherwise escape the traces directory.
    try:
        validate_safe_id(thread_id, "thread_id")
        validate_safe_id(tenant_id, "tenant_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    traces_dir = _traces_dir()
    files: list[Path] = []
    if thread_id:
        f = traces_dir / f"{thread_id}.jsonl"
        if f.exists():
            files.append(f)
    else:
        files = sorted(traces_dir.glob("*.jsonl"))
    all_traces: list[dict[str, Any]] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                all_traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if tenant_id:
        all_traces = [t for t in all_traces if _matches_tenant(t, tenant_id)]
    # Defensive: total_latency_ms may be None or missing — coerce to 0 to
    # avoid TypeError in sort comparison.
    all_traces.sort(
        key=lambda t: (t.get("total_latency_ms") or 0), reverse=True
    )
    return {
        "count": len(all_traces),
        "traces": all_traces[:limit],
    }


@traces_router.get("/{trace_id}")
def get_trace(
    trace_id: str,
    x_tenant_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Find a single trace by id across all thread files.

    Tenant isolation: only traces whose ``tenant_id`` field matches the
    requesting tenant (or whose ``thread_id`` follows the
    ``tenant-<id>-...`` convention) are reachable. Without this check,
    any tenant could fetch any other tenant's trace by guessing / scraping
    trace ids — a cross-tenant data leak.
    """
    # trace_id is a path, not a filename — but the lookup iterates files
    # rather than opening ``<trace_id>.jsonl`` directly, so a traversal
    # here can't escape the traces dir. Still validate to reject obviously
    # malformed IDs (and to keep the API contract uniform).
    try:
        validate_safe_id(trace_id, "trace_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Resolve the requesting tenant for the isolation filter. The header
    # bypasses Pydantic, so validate it here too.
    tenant_id_raw = x_tenant_id or settings.default_tenant_id
    try:
        tenant_id: str = validate_safe_id(tenant_id_raw, "tenant_id")  # type: ignore[assignment]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    traces_dir = _traces_dir()
    for f in traces_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if t.get("trace_id") == trace_id:
                # Enforce tenant isolation BEFORE returning the trace body.
                if not _matches_tenant(t, tenant_id):
                    raise HTTPException(
                        status_code=403,
                        detail="trace does not belong to the requesting tenant",
                    )
                return t
    raise HTTPException(status_code=404, detail=f"trace {trace_id} not found")


# --------------------------------------------------------------------------- #
# Flywheel
# --------------------------------------------------------------------------- #
flywheel_router = APIRouter(prefix="/api/flywheel", tags=["flywheel"])


@flywheel_router.get("/stats")
def flywheel_stats() -> dict[str, Any]:
    collector = get_collector()
    stats = collector.stats()
    # Sample most recent bad cases for the dashboard.
    bad_cases = list(collector.bad_store.iter_records())[-5:]
    good_cases = list(collector.good_store.iter_records())[-5:]
    return {
        "stats": stats,
        "recent_bad": bad_cases,
        "recent_good": good_cases,
    }


@flywheel_router.post("/post-train")
def post_train() -> dict[str, Any]:
    """Build SFT/DPO datasets from the flywheel. Returns artefact paths."""
    from post_training.pipeline import PostTrainingPipeline
    pipeline = PostTrainingPipeline()
    pipeline.build()
    return {
        "ok": True,
        "artefacts": pipeline.artefact_paths(),
    }


# --------------------------------------------------------------------------- #
# Dashboard (aggregate)
# --------------------------------------------------------------------------- #
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@dashboard_router.get("", response_model=DashboardStats)
def dashboard(
    x_tenant_id: str | None = Header(default=None),
) -> DashboardStats:
    """Aggregate stats for the seller dashboard home page."""
    tenant_id = x_tenant_id or settings.default_tenant_id
    try:
        validate_safe_id(tenant_id, "tenant_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Flywheel
    collector = get_collector()
    fly_stats = collector.stats()

    # Traces — cap memory by only reading the last N most-recently-modified
    # files instead of every trace file ever written.
    traces_dir = _traces_dir()
    files = sorted(traces_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    traces: list[dict[str, Any]] = []
    for f in files[:20]:  # last 20 thread files only
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    recent_traces = traces[-100:]
    avg_latency = (
        sum(t.get("total_latency_ms", 0) for t in recent_traces) / len(recent_traces)
        if recent_traces else 0.0
    )
    total_cost = sum(t.get("total_cost_usd", 0.0) for t in recent_traces)

    # Refunds today from the mock platform (best-effort).
    refunds_today = 0
    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            r = client.get(
                f"{settings.mock_platform_base_url}/refunds",
                headers={"X-Tenant-Id": tenant_id},
            )
            if r.status_code == 200:
                refunds = r.json()
                today = datetime.now(timezone.utc).date().isoformat()
                refunds_today = sum(
                    1 for x in refunds
                    if str(x.get("created_at", "")).startswith(today)
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not fetch refunds from mock platform: %s", exc)

    return DashboardStats(
        tenant_id=tenant_id,
        flywheel=fly_stats,
        traces={
            "total": len(traces),
            "recent": len(recent_traces),
            "errors": sum(1 for t in recent_traces if t.get("error")),
        },
        refunds_today=refunds_today,
        avg_latency_ms=round(avg_latency, 1),
        total_cost_usd=round(total_cost, 6),
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
health_router = APIRouter(prefix="/api/health", tags=["health"])


@health_router.get("")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "0719agent-api",
        "version": "0.1.0",
        "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
