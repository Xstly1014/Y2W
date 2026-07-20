"""Retrieval eval runner — evaluate the KB retriever directly.

Unlike EvalRunner (which invokes the full agent), RetrievalEvalRunner
queries the Indexer directly and scores the retrieved docs against
ground-truth relevant doc ids. No LLM is invoked.

Eval case format (YAML):

    - case_id: q1
      query: "如何申请退款？"
      relevant_ids: ["policy.md#chunk0", "faq.md#chunk3"]  # ground truth
      collection: "kb_demo-tenant"                          # optional
      k: 5                                                   # optional, default 5
      metrics: ["recall@k", "mrr", "ndcg"]                  # optional

Doc-id convention (must align with fixture `relevant_ids`):
    "{source_basename}#chunk{chunk_index}"   e.g. "policy.md#chunk0"

The Indexer is expected to expose `search(query, k, collection) -> list[Document]`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, Field

from config import settings
from evaluation.retrieval_metrics import RETRIEVAL_METRICS

logger = logging.getLogger(__name__)

# Default dataset shipped with the package.
DEFAULT_RETRIEVAL_DATASET = (
    Path(__file__).resolve().parent / "fixtures" / "retrieval_cases.yaml"
)


class RetrievalEvalCase(BaseModel):
    case_id: str
    query: str
    relevant_ids: list[str] = Field(default_factory=list)
    collection: str = "documents"
    k: int = 5
    metrics: list[str] = Field(
        default_factory=lambda: ["recall@k", "mrr", "ndcg"]
    )


class RetrievalEvalResult(BaseModel):
    case_id: str
    query: str
    retrieved_ids: list[str]
    relevant_ids: list[str]
    k: int
    scores: dict[str, float]  # metric_name -> score
    metadata: dict = Field(default_factory=dict)


class RetrievalEvalRunner:
    """Run retrieval eval against an Indexer.

    Args:
        indexer: object with .search(query, k, collection) -> list[Document]
        metrics: optional override of metric registry (name -> fn).
    """

    def __init__(self, indexer: Any, metrics: dict | None = None) -> None:
        self._indexer = indexer
        self._metrics: dict[str, Callable[..., float]] = metrics or dict(
            RETRIEVAL_METRICS
        )

    # ------------------------------------------------------------------ #
    # Case loading
    # ------------------------------------------------------------------ #
    def load_cases(self, path: Path | None = None) -> list[RetrievalEvalCase]:
        """Load YAML cases. Returns an empty list if the file is missing
        or doesn't parse to a YAML list."""
        path = path or DEFAULT_RETRIEVAL_DATASET
        if not path.exists():
            logger.warning(
                "Retrieval eval dataset not found at %s — returning empty list.",
                path,
            )
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            logger.error("Failed to parse %s: %s", path, exc)
            return []
        if not isinstance(data, list):
            logger.warning(
                "Retrieval eval dataset at %s is not a YAML list (got %s).",
                path, type(data).__name__,
            )
            return []
        return [RetrievalEvalCase(**c) for c in data]

    # ------------------------------------------------------------------ #
    # Doc id extraction (must align with fixture relevant_ids format)
    # ------------------------------------------------------------------ #
    def _doc_id(self, doc: Any) -> str:
        """Stable doc id.

        Priority:
          1. metadata['doc_id']  (explicit, used if present)
          2. metadata['source'] basename + '#chunk' + metadata['chunk_index']
             e.g. "policy.md#chunk0"
          3. content hash fallback (md5 of page_content, first 12 hex chars)
        """
        md = getattr(doc, "metadata", None) or {}
        # 1. explicit doc_id
        explicit = md.get("doc_id")
        if explicit:
            return str(explicit)
        # 2. source + #chunk + chunk_index
        source = md.get("source")
        chunk_index = md.get("chunk_index")
        if source is not None and chunk_index is not None:
            source_name = Path(str(source)).name
            return f"{source_name}#chunk{chunk_index}"
        # 3. content hash fallback
        content = getattr(doc, "page_content", "") or ""
        digest = hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"hash:{digest}"

    # ------------------------------------------------------------------ #
    # Run
    # ------------------------------------------------------------------ #
    def run(
        self, cases: list[RetrievalEvalCase] | None = None
    ) -> list[RetrievalEvalResult]:
        """For each case: query indexer, compute scores for requested metrics.

        Cases that fail (indexer raises, unknown metric) are still emitted
        with empty retrieved_ids and all-zero scores so the report stays
        complete — never lose a case to a mid-loop exception.
        """
        cases = cases if cases is not None else self.load_cases()
        results: list[RetrievalEvalResult] = []
        for case in cases:
            retrieved_ids: list[str] = []
            scores: dict[str, float] = {}
            try:
                docs = self._indexer.search(
                    case.query, k=case.k, collection=case.collection
                )
                retrieved_ids = [self._doc_id(d) for d in docs]
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Indexer failed on case %s (collection=%s)",
                    case.case_id, case.collection,
                )
                # Leave retrieved_ids empty; every metric will score 0.0.
                retrieved_ids = []

            for metric_name in case.metrics:
                fn = self._metrics.get(metric_name)
                if fn is None:
                    logger.warning(
                        "Unknown metric %r for case %s — skipping.",
                        metric_name, case.case_id,
                    )
                    scores[metric_name] = 0.0
                    continue
                try:
                    score = float(
                        fn(
                            retrieved_ids=retrieved_ids,
                            relevant_ids=case.relevant_ids,
                            k=case.k,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Metric %r raised on case %s: %s",
                        metric_name, case.case_id, exc,
                    )
                    score = 0.0
                scores[metric_name] = score

            results.append(
                RetrievalEvalResult(
                    case_id=case.case_id,
                    query=case.query,
                    retrieved_ids=retrieved_ids,
                    relevant_ids=list(case.relevant_ids),
                    k=case.k,
                    scores=scores,
                )
            )
        return results

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def write_report(
        self, results: list[RetrievalEvalResult], path: Path | None = None
    ) -> Path:
        """Write JSON report with per-case scores + aggregate (mean of each metric)."""
        path = path or (
            settings.eval_output_dir
            / f"retrieval_eval_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summary(results)
        payload = {
            "ran_at": datetime.now().isoformat(),
            "total": len(results),
            "summary": summary,
            "results": [r.model_dump() for r in results],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def summary(self, results: list[RetrievalEvalResult]) -> dict[str, float]:
        """Return {metric_name: mean_score} aggregated across cases.

        Metrics are unioned across cases (a metric only present on some
        cases averages over those cases). Returns an empty dict if there
        are no results.
        """
        if not results:
            return {}
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for r in results:
            for name, score in r.scores.items():
                sums[name] = sums.get(name, 0.0) + score
                counts[name] = counts.get(name, 0) + 1
        return {name: sums[name] / counts[name] for name in sums}
