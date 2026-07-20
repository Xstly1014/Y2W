"""Data flywheel package.

Closes the loop between production usage, evaluation and model improvement:
  1. Failed agent runs (bad cases) and successful runs (good cases) are
     appended to JSONL files.
  2. The post-training pipeline consumes these files to build SFT / DPO
     training sets.
  3. Improved models are re-evaluated; new failures feed back into step 1.

Extended with auto-classification, deduplication and priority ranking:
  * `classifier`  — rule + LLM hybrid categorization of badcases
  * `deduper`     — exact + near-dup (embedding cosine) detection
  * `prioritizer` — frequency × impact × severity × recency scoring

Future expansion hooks:
  * human-in-the-loop labelling UI
  * preference pairs (chosen / rejected) for DPO
"""
from data_flywheel.collector import BadCaseCollector
from data_flywheel.classifier import (
    classify,
    classify_rule_based,
    classify_with_llm,
    CATEGORIES,
    ClassificationResult,
)
from data_flywheel.deduper import (
    dedup_check,
    is_exact_dup,
    is_near_dup,
    DedupResult,
)
from data_flywheel.prioritizer import (
    priority_score,
    rank_by_priority,
    top_n,
    severity_for,
    impact_for,
)
from data_flywheel.storage import JsonlStore

__all__ = [
    "BadCaseCollector",
    "classify",
    "classify_rule_based",
    "classify_with_llm",
    "CATEGORIES",
    "ClassificationResult",
    "dedup_check",
    "is_exact_dup",
    "is_near_dup",
    "DedupResult",
    "priority_score",
    "rank_by_priority",
    "top_n",
    "severity_for",
    "impact_for",
    "JsonlStore",
]
