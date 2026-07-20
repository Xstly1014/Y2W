"""Deduplicate badcases by semantic similarity.

Two strategies:
1. Exact dedup: same user_input (case-insensitive, whitespace-normalized)
2. Near-dup: cosine similarity on text embeddings > threshold (default 0.92)

When a duplicate is found, increment the original's `occurrence_count`
instead of storing a new record. This preserves the audit trail while
keeping the dataset focused on unique failure modes.
"""
from __future__ import annotations
import logging, re
from typing import Any, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def is_exact_dup(a: str, b: str) -> bool:
    """True if two inputs are identical after normalization."""
    return _normalize(a) == _normalize(b)


def is_near_dup(
    a: str,
    b: str,
    embeddings: Any,
    threshold: float = 0.92,
) -> bool:
    """True if cosine similarity between embeddings(a) and embeddings(b) >= threshold.

    Returns False on any error (don't crash on embed failure).
    """
    try:
        import numpy as np
        ea = np.asarray(embeddings.embed_query(a), dtype=np.float32)
        eb = np.asarray(embeddings.embed_query(b), dtype=np.float32)
        # Cosine similarity.
        na, nb = np.linalg.norm(ea), np.linalg.norm(eb)
        if na == 0 or nb == 0:
            return False
        sim = float((ea @ eb) / (na * nb))
        return sim >= threshold
    except Exception as exc:
        logger.warning("is_near_dup failed: %s", exc)
        return False


@dataclass
class DedupResult:
    is_duplicate: bool
    duplicate_of: str | None  # original record id
    strategy: str = "none"    # "exact" | "near" | "none"


def dedup_check(
    new_input: str,
    existing_records: Iterable[dict[str, Any]],
    *,
    embeddings: Any | None = None,
    near_dup_threshold: float = 0.92,
) -> DedupResult:
    """Check if new_input duplicates any existing record.

    Strategy:
    1. Exact match against existing user_inputs (fast, O(n))
    2. If embeddings provided AND no exact match: near-dup check (slow, O(n) embed calls)

    Returns DedupResult with duplicate_of = existing record id if dup found.
    """
    # Phase 1: exact
    new_norm = _normalize(new_input)
    for rec in existing_records:
        if _normalize(rec.get("user_input", "")) == new_norm:
            return DedupResult(is_duplicate=True, duplicate_of=rec.get("id"), strategy="exact")
    # Phase 2: near-dup (only if embeddings given)
    if embeddings is not None:
        for rec in existing_records:
            existing_input = rec.get("user_input", "")
            if not existing_input:
                continue
            if is_near_dup(new_input, existing_input, embeddings, near_dup_threshold):
                return DedupResult(is_duplicate=True, duplicate_of=rec.get("id"), strategy="near")
    return DedupResult(is_duplicate=False, duplicate_of=None, strategy="none")
