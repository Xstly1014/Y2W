"""Tests for the evaluation layer: metrics + EvalRunner.

No LLM calls — we use a stub invoke and unit-test the metric functions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.metrics import contains, exact_match
from evaluation.runner import EvalCase, EvalRunner


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def test_exact_match_pass() -> None:
    assert exact_match(prediction="42", reference="42") == 1.0


def test_exact_match_whitespace_normalized() -> None:
    assert exact_match(prediction="  42  ", reference="42") == 1.0


def test_exact_match_fail() -> None:
    assert exact_match(prediction="43", reference="42") == 0.0


def test_contains_pass() -> None:
    assert contains(prediction="The answer is 84.", reference="84") == 1.0


def test_contains_case_sensitive() -> None:
    """contains is intentionally case-sensitive — document the behavior."""
    assert contains(prediction="Hello World", reference="hello") == 0.0
    assert contains(prediction="Hello World", reference="Hello") == 1.0


def test_contains_fail() -> None:
    assert contains(prediction="no numbers here", reference="42") == 0.0


# --------------------------------------------------------------------------- #
# EvalRunner
# --------------------------------------------------------------------------- #
def _write_cases_yaml(path: Path) -> None:
    path.write_text(
        """
- case_id: c1
  user_input: "What is 12 * 7?"
  expected: "84"
  metric: contains
  metadata:
    category: calculator
- case_id: c2
  user_input: "What is sqrt(144) + 3?"
  expected: "15"
  metric: contains
  metadata:
    category: calculator
""".strip(),
        encoding="utf-8",
    )


def test_eval_runner_load_cases(tmp_path: Path) -> None:
    cases_yaml = tmp_path / "cases.yaml"
    _write_cases_yaml(cases_yaml)
    runner = EvalRunner(agent_invoke=lambda x: "")
    cases = runner.load_cases(cases_yaml)
    assert len(cases) == 2
    assert cases[0].case_id == "c1"
    assert cases[0].metric == "contains"
    assert cases[1].metadata["category"] == "calculator"


def test_eval_runner_runs_and_scores(stub_agent_invoke) -> None:
    """The stub returns "84" for "12 * 7" and "15" for "sqrt" — both pass."""
    cases = [
        EvalCase(case_id="c1", user_input="What is 12 * 7?",
                 expected="84", metric="contains"),
        EvalCase(case_id="c2", user_input="What is sqrt(144) + 3?",
                 expected="15", metric="contains"),
    ]
    runner = EvalRunner(agent_invoke=stub_agent_invoke)
    results = runner.run(cases)
    assert len(results) == 2
    assert all(r.passed for r in results)
    assert results[0].score == 1.0


def test_eval_runner_handles_agent_error(stub_agent_invoke) -> None:
    """If the invoke raises, the runner records an error string + score 0."""
    def broken_invoke(_x: str) -> str:
        raise RuntimeError("boom")
    cases = [EvalCase(case_id="c1", user_input="q", expected="anything",
                      metric="contains")]
    runner = EvalRunner(agent_invoke=broken_invoke)
    results = runner.run(cases)
    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].score == 0.0
    assert "agent error" in results[0].prediction


def test_eval_runner_unknown_metric_falls_back_to_contains(stub_agent_invoke) -> None:
    cases = [
        EvalCase(case_id="c1", user_input="What is 12 * 7?",
                 expected="84", metric="totally_unknown_metric"),
    ]
    runner = EvalRunner(agent_invoke=stub_agent_invoke)
    results = runner.run(cases)
    assert results[0].passed is True  # contains fallback passes


def test_eval_runner_write_report(stub_agent_invoke, tmp_path: Path) -> None:
    cases = [EvalCase(case_id="c1", user_input="What is 12 * 7?",
                      expected="84", metric="contains")]
    runner = EvalRunner(agent_invoke=stub_agent_invoke)
    results = runner.run(cases)
    out = tmp_path / "report.json"
    path = runner.write_report(results, path=out)
    assert path.exists()
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["total"] == 1
    assert payload["passed"] == 1
    assert payload["pass_rate"] == 1.0
