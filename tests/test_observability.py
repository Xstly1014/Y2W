"""Tests for observability: cost extraction + estimate, and the
TraceRecorder step accounting (without actually running an LLM).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from observability.cost import (
    PRICE_TABLE, estimate_cost, extract_usage,
)
from observability.tracing import TraceRecorder, _traces_dir


# --------------------------------------------------------------------------- #
# extract_usage
# --------------------------------------------------------------------------- #
def test_extract_usage_with_metadata() -> None:
    msg = SimpleNamespace(usage_metadata={
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    })
    out = extract_usage(msg)
    assert out == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_extract_usage_missing_metadata_returns_zeros() -> None:
    msg = SimpleNamespace(usage_metadata=None)
    out = extract_usage(msg)
    assert out == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_extract_usage_partial_metadata() -> None:
    msg = SimpleNamespace(usage_metadata={"input_tokens": 7})
    out = extract_usage(msg)
    assert out["input_tokens"] == 7
    assert out["output_tokens"] == 0
    assert out["total_tokens"] == 0


# --------------------------------------------------------------------------- #
# estimate_cost
# --------------------------------------------------------------------------- #
def test_estimate_cost_known_model() -> None:
    # gpt-4o-mini: 0.15 in / 0.60 out per 1M tokens
    cost = estimate_cost(input_tokens=1_000_000, output_tokens=1_000_000, model="gpt-4o-mini")
    assert cost == 0.15 + 0.60


def test_estimate_cost_unknown_model_returns_zero() -> None:
    assert estimate_cost(1_000_000, 1_000_000, "totally-fake-model") == 0.0


def test_estimate_cost_zero_tokens() -> None:
    assert estimate_cost(0, 0, "gpt-4o-mini") == 0.0


def test_price_table_has_deepseek_v4_pro() -> None:
    """The model we configured in .env must be in the price table."""
    assert "deepseek-v4-pro" in PRICE_TABLE
    in_price, out_price = PRICE_TABLE["deepseek-v4-pro"]
    assert in_price > 0
    assert out_price > 0


# --------------------------------------------------------------------------- #
# TraceRecorder — record steps + finalize (no LLM call)
# --------------------------------------------------------------------------- #
def test_trace_recorder_aggregates_tokens_and_cost() -> None:
    rec = TraceRecorder(thread_id="t-test", model_name="gpt-4o-mini")
    rec.user_input = "what is 2+2?"

    # Simulate two LLM calls back-to-back (no tool calls).
    msg1 = SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        tool_calls=None,
    )
    msg2 = SimpleNamespace(
        usage_metadata={"input_tokens": 200, "output_tokens": 10, "total_tokens": 210},
        tool_calls=None,
    )
    rec.record_llm_call(msg1, latency_ms=120.0)
    rec.record_llm_call(msg2, latency_ms=80.0)
    rec.record_tool_call("calculator_tool", {"expression": "2+2"}, "4", latency_ms=5.0)

    trace = rec.finalize(final_answer="4")
    assert trace["thread_id"] == "t-test"
    assert trace["model"] == "gpt-4o-mini"
    assert trace["user_input"] == "what is 2+2?"
    assert trace["final_answer"] == "4"
    assert trace["error"] is None
    assert trace["num_steps"] == 3
    # 100 + 200 in, 50 + 10 out
    assert trace["total_tokens_in"] == 300
    assert trace["total_tokens_out"] == 60
    # (100/1M)*0.15 + (50/1M)*0.60 + (200/1M)*0.15 + (10/1M)*0.60
    expected_cost = (100 / 1e6) * 0.15 + (50 / 1e6) * 0.60 \
                  + (200 / 1e6) * 0.15 + (10 / 1e6) * 0.60
    assert abs(trace["total_cost_usd"] - round(expected_cost, 6)) < 1e-9


def test_trace_recorder_truncates_long_tool_results() -> None:
    rec = TraceRecorder(thread_id="t-trunc", model_name="gpt-4o-mini")
    long_result = "x" * 2000
    rec.record_tool_call("big_tool", {}, long_result, latency_ms=1.0)
    trace = rec.finalize("done")
    tool_step = trace["steps"][0]
    assert len(tool_step["result"]) < 600  # 500 + " [+N chars]" suffix
    assert "+1499 chars" in tool_step["result"] or "+1500 chars" in tool_step["result"]


def test_trace_recorder_writes_to_thread_jsonl_file() -> None:
    """Finalize must persist a JSON line under data/traces/<thread_id>.jsonl."""
    rec = TraceRecorder(thread_id="t-persist", model_name="gpt-4o-mini")
    rec.user_input = "hi"
    rec.finalize("hello")

    trace_file = _traces_dir() / "t-persist.jsonl"
    assert trace_file.exists()
    lines = [l for l in trace_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["trace_id"] == rec.trace_id
    assert parsed["final_answer"] == "hello"
