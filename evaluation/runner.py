"""Eval runner — loads cases, runs the agent, scores, writes report."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        """Run cases sequentially (original behaviour)."""
        cases = cases or self.load_cases()
        return self._run_cases(cases)

    def run_concurrent(
        self,
        cases: list[EvalCase] | None = None,
        *,
        max_workers: int | None = None,
    ) -> list[EvalResult]:
        """Run cases concurrently with a thread pool.

        Speeds up eval runs when the agent spends most of its time waiting
        on LLM API calls (I/O bound) — 8 cases go from ~16s sequential to
        ~5s with 4 workers. See `optimization_logs/2026-07-20/issues-and-fixes.md`
        P2-3.

        Args:
            cases: Cases to run (loads from dataset if None).
            max_workers: Thread pool size (defaults to
                `settings.llm_batch_max_workers`).
        """
        cases = cases or self.load_cases()
        max_workers = max_workers or settings.llm_batch_max_workers
        # Order preservation: submit in order, collect by case_id.
        results_by_case_id: dict[str, EvalResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._run_one, case): case.case_id
                for case in cases
            }
            for fut in as_completed(futures):
                case_id = futures[fut]
                try:
                    results_by_case_id[case_id] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Worker failed on case %s", case_id)
                    # Find the original case to build an error result.
                    case = next((c for c in cases if c.case_id == case_id), None)
                    if case:
                        results_by_case_id[case_id] = EvalResult(
                            case_id=case_id,
                            user_input=case.user_input,
                            prediction=f"[worker error] {exc}",
                            expected=case.expected,
                            score=0.0,
                            passed=False,
                            metric=case.metric,
                            metadata=case.metadata,
                        )
        # Reassemble in the original case order.
        return [results_by_case_id[c.case_id] for c in cases if c.case_id in results_by_case_id]

    def _run_cases(self, cases: list[EvalCase]) -> list[EvalResult]:
        """Sequential run loop (shared by `run` and as the fallback path)."""
        results: list[EvalResult] = []
        for case in cases:
            results.append(self._run_one(case))
        return results

    def _run_one(self, case: EvalCase) -> EvalResult:
        """Run a single case and score it. Isolated so it can run in a worker thread."""
        try:
            prediction = self._invoke(case.user_input)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent failed on case %s", case.case_id)
            prediction = f"[agent error] {exc}"
        metric_fn = self._metrics.get(case.metric)
        fallback_metric = next(iter(self._metrics.values())) if self._metrics else None
        if metric_fn is None:
            metric_fn = self._metrics.get("contains", fallback_metric)
        if metric_fn is None:
            logger.error(
                "No metric available for case %s (requested=%s, no fallback)",
                case.case_id, case.metric,
            )
            return EvalResult(
                case_id=case.case_id,
                user_input=case.user_input,
                prediction=prediction,
                expected=case.expected,
                score=0.0,
                passed=False,
                metric=case.metric,
                metadata=case.metadata,
            )
        score = float(metric_fn(prediction=prediction, reference=case.expected))
        return EvalResult(
            case_id=case.case_id,
            user_input=case.user_input,
            prediction=prediction,
            expected=case.expected,
            score=score,
            passed=score >= 0.5,
            metric=case.metric,
            metadata=case.metadata,
        )

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
