"""Prioritize badcases for fix queue.

Score = frequency * impact * severity * (1 - recency_decay)

- frequency: how often this badcase pattern occurs (occurrence_count)
- impact: business impact (refund-related > info-only)
- severity: 1.0 for policy violation, 0.5 for tone issues
- recency_decay: older badcases decay (Ebbinghaus-style, half-life 30 days)
"""
from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Any
from data_flywheel.classifier import CATEGORIES

# Severity per category — policy violations are most severe, tone issues least.
_SEVERITY: dict[str, float] = {
    "policy_violation": 1.0,
    "hallucination": 0.9,
    "rag_wrong": 0.8,
    "tool_wrong": 0.8,
    "rag_missed": 0.6,
    "tool_failed": 0.5,
    "refusal_escalate": 0.5,
    "tone_issue": 0.3,
    "other": 0.4,
}

# Impact keywords — refund/payment issues have higher business impact.
_IMPACT_KEYWORDS = [
    (["refund", "退款", "支付", "payment", "compensat"], 1.0),
    (["订单", "order", "logistics", "物流"], 0.7),
    (["商品", "product", "库存", "stock"], 0.5),
    (["faq", "policy", "政策"], 0.3),
]


def severity_for(category: str) -> float:
    """Return severity score in [0, 1] for a category."""
    return _SEVERITY.get(category, 0.4)


def impact_for(text: str) -> float:
    """Return impact score based on keyword matching. Default 0.3."""
    text_lower = text.lower()
    for keywords, score in _IMPACT_KEYWORDS:
        if any(kw.lower() in text_lower for kw in keywords):
            return score
    return 0.3


def recency_decay(timestamp_iso: str, half_life_days: float = 30.0) -> float:
    """Ebbinghaus-style decay: exp(-days / half_life). Returns (0, 1]."""
    try:
        ts = datetime.fromisoformat(timestamp_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - ts).total_seconds() / 86400
        return math.exp(-days / half_life_days)
    except Exception:
        return 0.5  # unknown age


def priority_score(
    record: dict[str, Any],
    *,
    half_life_days: float = 30.0,
) -> float:
    """Compute priority score in [0, ~5]. Higher = more urgent to fix.

    Formula: frequency * impact * severity * recency_decay
    - frequency = log(1 + occurrence_count) — log so 100 occurrences isn't 100x more urgent than 1
    - recency_decay = exp(-age_days / half_life)
    """
    frequency = math.log(1 + record.get("occurrence_count", 1))
    category = record.get("category", record.get("metadata", {}).get("category", "other"))
    severity = severity_for(category)
    impact = impact_for(record.get("user_input", ""))
    decay = recency_decay(record.get("timestamp", ""), half_life_days)
    return frequency * impact * severity * decay


def rank_by_priority(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return records sorted by priority_score descending. Stable sort."""
    return sorted(records, key=lambda r: priority_score(r), reverse=True)


def top_n(records: list[dict[str, Any]], n: int = 10) -> list[dict[str, Any]]:
    """Return top-N highest-priority badcases. Adds 'priority_score' field to each."""
    ranked = rank_by_priority(records)
    out = []
    for r in ranked[:n]:
        r_copy = dict(r)
        r_copy["priority_score"] = priority_score(r)
        out.append(r_copy)
    return out
