"""Tests for the data flywheel: JsonlStore + BadCaseCollector.

The collector reads its paths from `settings`. The conftest autouse
fixture has already redirected those paths to a tmp dir, so these
tests are isolated.
"""
from __future__ import annotations

from pathlib import Path

from data_flywheel.collector import BadCaseCollector
from data_flywheel.storage import JsonlStore
from evaluation.runner import EvalResult


# --------------------------------------------------------------------------- #
# JsonlStore
# --------------------------------------------------------------------------- #
def test_jsonl_store_append_and_iter(tmp_path: Path) -> None:
    path = tmp_path / "store.jsonl"
    store = JsonlStore(path)
    store.append({"id": "a", "text": "hello"})
    store.append({"id": "b", "text": "world"})

    records = list(store.iter_records())
    assert len(records) == 2
    assert records[0]["id"] == "a"
    assert records[1]["text"] == "world"


def test_jsonl_store_count(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "c.jsonl")
    assert store.count() == 0
    for i in range(5):
        store.append({"i": i})
    assert store.count() == 5


def test_jsonl_store_clear(tmp_path: Path) -> None:
    store = JsonlStore(tmp_path / "c.jsonl")
    store.append({"x": 1})
    assert store.count() == 1
    store.clear()
    assert store.count() == 0


def test_jsonl_store_handles_unicode(tmp_path: Path) -> None:
    """CJK / emoji must round-trip without encoding issues."""
    path = tmp_path / "uni.jsonl"
    store = JsonlStore(path)
    store.append({"zh": "你好", "emoji": "🚀"})
    records = list(store.iter_records())
    assert records[0]["zh"] == "你好"
    assert records[0]["emoji"] == "🚀"


def test_jsonl_store_skips_blank_lines(tmp_path: Path) -> None:
    """A trailing newline (or accidental blank line) must not crash iter."""
    path = tmp_path / "blank.jsonl"
    path.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
    store = JsonlStore(path)
    records = list(store.iter_records())
    assert len(records) == 2


# --------------------------------------------------------------------------- #
# BadCaseCollector
# --------------------------------------------------------------------------- #
def test_collector_routes_bad_and_good() -> None:
    collector = BadCaseCollector()
    collector.record_interaction(
        user_input="q1", prediction="bad answer",
        passed=False, metadata={"trace_id": "t1"},
    )
    collector.record_interaction(
        user_input="q2", prediction="good answer",
        passed=True, metadata={"trace_id": "t2"},
    )
    stats = collector.stats()
    assert stats == {"bad": 1, "good": 1}


def test_collector_envelope_has_required_fields() -> None:
    collector = BadCaseCollector()
    collector.record_interaction(
        user_input="hello", prediction="hi",
        passed=True, metadata={"trace_id": "abc"},
    )
    good_records = list(collector.good_store.iter_records())
    assert len(good_records) == 1
    rec = good_records[0]
    # Required envelope fields:
    for key in ["id", "timestamp", "user_input", "prediction",
                "expected", "score", "passed", "metadata"]:
        assert key in rec
    # Metadata merged correctly:
    assert rec["metadata"]["trace_id"] == "abc"
    assert rec["metadata"]["source"] == "live"


def test_collector_record_case_from_eval_result() -> None:
    """EvalRunner feeds EvalResult objects to the collector."""
    collector = BadCaseCollector()
    bad = EvalResult(
        case_id="c1", user_input="q", prediction="wrong",
        expected="right", score=0.0, passed=False,
        metric="contains", metadata={"category": "calc"},
    )
    good = EvalResult(
        case_id="c2", user_input="q", prediction="right",
        expected="right", score=1.0, passed=True,
        metric="contains", metadata={"category": "calc"},
    )
    collector.record_case(bad)
    collector.record_case(good)

    assert collector.stats() == {"bad": 1, "good": 1}

    # EvalResult metadata must propagate (case_id, metric, category).
    bad_records = list(collector.bad_store.iter_records())
    assert bad_records[0]["metadata"]["case_id"] == "c1"
    assert bad_records[0]["metadata"]["metric"] == "contains"
    assert bad_records[0]["metadata"]["category"] == "calc"


def test_collector_stats_starts_empty() -> None:
    """A fresh collector on empty stores reports zeros, not None."""
    collector = BadCaseCollector()
    assert collector.stats() == {"bad": 0, "good": 0}
