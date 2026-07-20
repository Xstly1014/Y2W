"""Information-retrieval metrics for evaluating knowledge-base recall.

Each metric takes (retrieved_ids, relevant_ids, k=None) and returns a float
in [0.0, 1.0]. `retrieved_ids` is the ranked list of doc ids returned by
the retriever; `relevant_ids` is the ground-truth set of relevant doc ids.

When `k` is given, only the top-k of `retrieved_ids` are considered.

All functions are pure: no LLM, no I/O, no global state. They operate on
plain strings (doc ids) so they can be unit-tested without any Document /
Indexer / embedding machinery.
"""
from __future__ import annotations

import math
from typing import Iterable


def _effective_k(retrieved_ids: list[str], k: int | None) -> int:
    """Actual cutoff to apply: min(k, len(retrieved_ids)) when k is given,
    otherwise len(retrieved_ids). Always >= 0."""
    n = len(retrieved_ids)
    if k is None:
        return n
    return max(0, min(k, n))


def recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int | None = None,
) -> float:
    """Recall@K = |relevant ∩ top-k retrieved| / |relevant|.

    Returns 0.0 if relevant_ids is empty (avoid div-by-zero).
    """
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[: _effective_k(retrieved_ids, k)]
    rel_set = set(relevant_ids)
    hits = sum(1 for doc_id in top_k if doc_id in rel_set)
    return hits / len(rel_set)


def precision_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int | None = None,
) -> float:
    """Precision@K = |relevant ∩ top-k retrieved| / k.

    If k is None, uses len(retrieved_ids); returns 0.0 if retrieved is empty.
    """
    eff_k = _effective_k(retrieved_ids, k)
    if eff_k == 0:
        return 0.0
    top_k = retrieved_ids[:eff_k]
    rel_set = set(relevant_ids)
    hits = sum(1 for doc_id in top_k if doc_id in rel_set)
    # Denominator is the cutoff actually applied (k when given, else
    # len(retrieved_ids)); this matches the standard Precision@K definition.
    denom = k if k is not None else len(retrieved_ids)
    if denom <= 0:
        return 0.0
    return hits / denom


def mrr(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int | None = None,
) -> float:
    """Mean Reciprocal Rank = 1 / rank_of_first_relevant.

    Only considers top-k. Returns 0.0 if no relevant doc in top-k.
    """
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[: _effective_k(retrieved_ids, k)]
    rel_set = set(relevant_ids)
    for rank, doc_id in enumerate(top_k, start=1):
        if doc_id in rel_set:
            return 1.0 / rank
    return 0.0


def ndcg(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int | None = None,
) -> float:
    """Normalized Discounted Cumulative Gain.

    DCG  = sum(rel_i / log2(i+1)) for i=1..k
    IDCG = DCG of ideal ranking (all relevant first)
    NDCG = DCG / IDCG (0.0 if IDCG is 0).

    Binary relevance: rel_i = 1 if retrieved_ids[i-1] in relevant_ids else 0.
    """
    if not relevant_ids:
        return 0.0
    eff_k = _effective_k(retrieved_ids, k)
    if eff_k == 0:
        return 0.0
    rel_set = set(relevant_ids)
    top_k = retrieved_ids[:eff_k]

    # DCG of the retrieved ranking.
    dcg = 0.0
    for i, doc_id in enumerate(top_k, start=1):
        if doc_id in rel_set:
            dcg += 1.0 / math.log2(i + 1)

    # IDCG: ideal ranking places all relevant docs first. The number of
    # relevant positions considered is min(|relevant|, eff_k) — we cannot
    # gain more reward than having every slot up to k be relevant.
    ideal_hits = min(len(rel_set), eff_k)
    idcg = 0.0
    for i in range(1, ideal_hits + 1):
        idcg += 1.0 / math.log2(i + 1)

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def hit_rate(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int | None = None,
) -> float:
    """Hit@K = 1.0 if ANY relevant doc in top-k, else 0.0."""
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[: _effective_k(retrieved_ids, k)]
    rel_set = set(relevant_ids)
    for doc_id in top_k:
        if doc_id in rel_set:
            return 1.0
    return 0.0


def average_precision(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int | None = None,
) -> float:
    """AP = (1/|relevant|) * sum(Precision@i * rel_i) for i=1..k.

    Standard IR formula (Manning et al.). Penalizes missing relevant docs
    by dividing by the total |relevant|, not the number of hits in top-k.
    Returns 0.0 if relevant_ids is empty.
    """
    if not relevant_ids:
        return 0.0
    eff_k = _effective_k(retrieved_ids, k)
    if eff_k == 0:
        return 0.0
    rel_set = set(relevant_ids)
    top_k = retrieved_ids[:eff_k]

    hits = 0
    precision_sum = 0.0
    for i, doc_id in enumerate(top_k, start=1):
        if doc_id in rel_set:
            hits += 1
            precision_sum += hits / i  # Precision@i = hits_so_far / i
    return precision_sum / len(rel_set)


# Registry: name -> function, for CLI dispatch and runner lookup.
RETRIEVAL_METRICS: dict[str, callable] = {
    "recall@k": recall_at_k,
    "precision@k": precision_at_k,
    "mrr": mrr,
    "ndcg": ndcg,
    "hit@k": hit_rate,
    "ap": average_precision,
}
