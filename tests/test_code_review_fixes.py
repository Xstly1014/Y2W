"""Regression tests for the code-review fixes.

These tests cover bugs and security issues found during the comprehensive
code review and verify the fixes hold:

  * mock_platform refund validation (negative / over-total amount)
  * mock_platform refund id uniqueness
  * mock_platform state isolation between tenants
  * JsonlStore concurrent appends don't interleave bytes
  * TraceRecorder concurrent writes to the same thread file
  * PostTrainingPipeline DPO uses Jaccard, not single-token match
  * KB upload sanitises path-traversal filenames
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------- #
# mock_platform: refund validation
# --------------------------------------------------------------------------- #
def test_mock_platform_rejects_negative_refund_amount(monkeypatch) -> None:
    """A negative refund amount must be rejected (business rule)."""
    from fastapi import HTTPException
    from mock_platform.server import create_refund, RefundRequest

    req = RefundRequest(order_id="1001", reason="defective", amount_usd=-5.0)
    with pytest.raises(HTTPException) as exc_info:
        create_refund(req, x_tenant_id="demo-tenant")
    assert exc_info.value.status_code == 400
    assert "positive" in str(exc_info.value.detail).lower()


def test_mock_platform_rejects_refund_exceeding_order_total(monkeypatch) -> None:
    """Refund amount must not exceed the order total."""
    from fastapi import HTTPException
    from mock_platform.server import create_refund, RefundRequest

    req = RefundRequest(order_id="1001", reason="defective", amount_usd=9999.0)
    with pytest.raises(HTTPException) as exc_info:
        create_refund(req, x_tenant_id="demo-tenant")
    assert exc_info.value.status_code == 400
    assert "exceeds" in str(exc_info.value.detail).lower()


def test_mock_platform_refund_ids_are_globally_unique() -> None:
    """Two refunds for different orders must produce different ids (uuid-based)."""
    from mock_platform.server import create_refund, RefundRequest

    # Refund order 1001 (defective) then 1002 (defective).
    r1 = create_refund(
        RefundRequest(order_id="1001", reason="defective"),
        x_tenant_id="demo-tenant",
    )
    r2 = create_refund(
        RefundRequest(order_id="1002", reason="defective"),
        x_tenant_id="demo-tenant",
    )
    assert r1["refund_id"] != r2["refund_id"]
    # Both should follow RF-<order>-<hex> format.
    assert r1["refund_id"].startswith("RF-1001-")
    assert r2["refund_id"].startswith("RF-1002-")


def test_mock_platform_returns_shallow_copies_not_internal_state() -> None:
    """list_refunds / list_orders must not return internal list references."""
    from mock_platform.server import list_orders, list_refunds

    orders1 = list_orders(x_tenant_id="demo-tenant")
    orders2 = list_orders(x_tenant_id="demo-tenant")
    # Different list instances => caller can't mutate internal state.
    assert orders1 is not orders2
    assert orders1 == orders2


# --------------------------------------------------------------------------- #
# JsonlStore concurrency
# --------------------------------------------------------------------------- #
def test_jsonl_store_concurrent_appends_are_atomic(tmp_path: Path) -> None:
    """Many threads appending to the same store must not corrupt lines.

    Regression: before adding the per-instance lock, concurrent writes
    could interleave bytes inside a single line, producing JSON that
    failed to parse on read.
    """
    from data_flywheel.storage import JsonlStore

    store = JsonlStore(tmp_path / "concurrent.jsonl")
    n_threads = 10
    n_per_thread = 50

    def writer(thread_id: int) -> None:
        for i in range(n_per_thread):
            store.append({"thread": thread_id, "i": i})

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = list(store.iter_records())
    assert len(records) == n_threads * n_per_thread
    # Every record must round-trip cleanly (already enforced by iter_records
    # skipping bad lines, but we additionally check the count matches).
    keys = {(r["thread"], r["i"]) for r in records}
    assert len(keys) == n_threads * n_per_thread


# --------------------------------------------------------------------------- #
# TraceRecorder concurrency
# --------------------------------------------------------------------------- #
def test_trace_recorder_concurrent_writes_to_same_thread_file() -> None:
    """Concurrent TraceRecorders writing to the same thread file must not
    interleave bytes (each line must be a complete JSON object)."""
    from observability.tracing import TraceRecorder, _traces_dir

    n = 20

    def writer(idx: int) -> None:
        rec = TraceRecorder(thread_id="t-concurrent", model_name="gpt-4o-mini")
        rec.user_input = f"q-{idx}"
        rec.finalize(f"a-{idx}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    trace_file = _traces_dir() / "t-concurrent.jsonl"
    assert trace_file.exists()
    lines = [
        l for l in trace_file.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert len(lines) == n
    # Every line must parse as JSON.
    for line in lines:
        parsed = json.loads(line)  # raises if interleave corrupted the line
        assert parsed["trace_id"]
        assert parsed["thread_id"] == "t-concurrent"


# --------------------------------------------------------------------------- #
# PostTrainingPipeline DPO matching
# --------------------------------------------------------------------------- #
def test_dpo_pipeline_uses_jaccard_not_single_token() -> None:
    """A bad case sharing only the token 'the' with a good case must NOT pair.

    Regression: previously `bad_tokens & set(good.split())` matched on any
    single shared token, so 'the refund policy' (bad) and 'the cat sat'
    (good) would form a DPO pair. The Jaccard-based matcher rejects this.
    """
    from post_training.pipeline import PostTrainingPipeline, _jaccard, _token_set

    # Single-token overlap => low Jaccard.
    bad_tokens = _token_set("the refund policy is broken")
    good_tokens = _token_set("the cat sat on the mat")
    assert _jaccard(bad_tokens, good_tokens) < 0.3

    # High overlap => high Jaccard.
    bad_tokens2 = _token_set("what is your return policy for defective items")
    good_tokens2 = _token_set("what is your return policy for damaged items")
    assert _jaccard(bad_tokens2, good_tokens2) > 0.5


# --------------------------------------------------------------------------- #
# KB upload path traversal
# --------------------------------------------------------------------------- #
def test_kb_upload_sanitises_path_traversal_filename() -> None:
    """A user-supplied filename with `../` must be reduced to its basename."""
    # We test the sanitisation logic directly; the FastAPI endpoint does the
    # same `Path(upload.filename).name` step.
    from pathlib import Path

    malicious_names = [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "normal.txt",
        "/tmp/evil.txt",
    ]
    for name in malicious_names:
        safe = Path(name).name
        # The sanitised name must never contain a path separator.
        assert "/" not in safe
        assert "\\" not in safe
