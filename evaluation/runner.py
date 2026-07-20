"""Eval runner — loads cases, runs the agent, scores, writes report."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger(__name__)

Metric = Callable[..., float]


class EvalCase(BaseModel):
    case_id: str
    user_input: str
    expected: str
    metric: str = "contains"  # name of metric in the runner's metric map
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalResult(BaseModel):
    case_id: str
    user_input: str
    prediction: str
    expected: str
    score: float
    passed: bool
    metric: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalRunner:
    """Run a callable agent over a YAML dataset and score each case.

    Args:
        agent_invoke: callable that takes a user string and returns the
            agent's final answer string.
        metrics: map of metric name -> metric function.
    """

    def __init__(
        self,
        agent_invoke: Callable[[str], str],
        metrics: dict[str, Metric] | None = None,
    ) -> None:
        self._invoke = agent_invoke
        self._metrics = metrics or {
            "exact_match": exact_match_import(),
            "contains": contains_import(),
        }

    def load_cases(self, path: Path | None = None) -> list[EvalCase]:
        path = path or settings.eval_dataset_path
        if not path.exists():
            logger.warning("Eval dataset not found at %s — returning empty list.", path)
            return []
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Defensive: a YAML doc that parses to None (empty file) or to a
        # non-list scalar / dict would break the list comprehension below.
        if not isinstance(data, list):
            logger.warning(
                "Eval dataset at %s is not a YAML list (got %s) — returning empty list.",
                path, type(data).__name__,
            )
            return []
        return [EvalCase(**c) for c in data]

    def run(self, cases: list[EvalCase] | None = None) -> list[EvalResult]:
        cases = cases or self.load_cases()
        results: list[EvalResult] = []
        # Fallback metric if a case names one we don't know AND the default
        # "contains" isn't registered either (e.g. caller supplied a custom
        # metrics dict without it). Without this, EvalRunner.run would
        # KeyError mid-loop and lose all results from prior cases.
        fallback_metric = next(iter(self._metrics.values())) if self._metrics else None
        for case in cases:
            try:
                prediction = self._invoke(case.user_input)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agent failed on case %s", case.case_id)
                prediction = f"[agent error] {exc}"
            metric_fn = self._metrics.get(case.metric)
            if metric_fn is None:
                metric_fn = self._metrics.get("contains", fallback_metric)
            if metric_fn is None:
                # No metrics at all — record the case with score 0 so the
                # report still surfaces what happened.
                logger.error(
                    "No metric available for case %s (requested=%s, no fallback)",
                    case.case_id, case.metric,
                )
                results.append(
                    EvalResult(
                        case_id=case.case_id,
                        user_input=case.user_input,
                        prediction=prediction,
                        expected=case.expected,
                        score=0.0,
                        passed=False,
                        metric=case.metric,
                        metadata=case.metadata,
                    )
                )
                continue
            score = float(metric_fn(prediction=prediction, reference=case.expected))
            results.append(
                EvalResult(
                    case_id=case.case_id,
                    user_input=case.user_input,
                    prediction=prediction,
                    expected=case.expected,
                    score=score,
                    passed=score >= 0.5,
                    metric=case.metric,
                    metadata=case.metadata,
                )
            )
        return results

    def write_report(self, results: list[EvalResult], path: Path | None = None) -> Path:
        path = path or (settings.eval_output_dir / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        passed = sum(r.passed for r in results)
        summary = {
            "ran_at": datetime.now().isoformat(),
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / len(results) if results else 0.0,
            "results": [r.model_dump() for r in results],
        }
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


# Local imports to avoid circular dependency at module load time.
def exact_match_import():
    from evaluation.metrics import exact_match
    return exact_match


def contains_import():
    from evaluation.metrics import contains
    return contains
