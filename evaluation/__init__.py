"""Evaluation package.

Runs the agent over a YAML dataset of cases, scores each answer against
the expected behaviour, and writes a JSON report. Designed to plug into
the badcase flywheel — failed cases are emitted in flywheel format so
they can be reviewed, labelled and reused for post-training.

Two eval surfaces:
  * answers   — EvalRunner invokes the full agent and scores the final
                answer (LLM-based, end-to-end).
  * retrieval — RetrievalEvalRunner queries the Indexer directly and
                scores the retrieved doc ids against ground-truth ids
                with standard IR metrics (Recall@K / MRR / NDCG / ...).
                No LLM is invoked.

Future expansion hooks:
  * tool-call trajectory evaluation (not just final answer)
  * regression dashboards
"""
from evaluation.metrics import (
    contains,
    exact_match,
    fuzzy_match,
    length_ratio,
    llm_judge,
)
from evaluation.retrieval_metrics import (
    RETRIEVAL_METRICS,
    average_precision,
    hit_rate,
    mrr,
    ndcg,
    precision_at_k,
    recall_at_k,
)
from evaluation.retrieval_runner import (
    RetrievalEvalCase,
    RetrievalEvalResult,
    RetrievalEvalRunner,
)
from evaluation.runner import EvalCase, EvalResult, EvalRunner

__all__ = [
    # answer metrics
    "exact_match",
    "contains",
    "fuzzy_match",
    "length_ratio",
    "llm_judge",
    # retrieval metrics
    "recall_at_k",
    "precision_at_k",
    "mrr",
    "ndcg",
    "hit_rate",
    "average_precision",
    "RETRIEVAL_METRICS",
    # runners
    "EvalCase",
    "EvalRunner",
    "EvalResult",
    "RetrievalEvalCase",
    "RetrievalEvalRunner",
    "RetrievalEvalResult",
]
