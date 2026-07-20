"""Eval metrics — small, composable scoring functions.

Each metric takes (prediction, reference, **kwargs) and returns a float
in [0.0, 1.0]. Add more metrics here as the project grows.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.language_models import BaseChatModel


def exact_match(prediction: str, reference: str, **_: Any) -> float:
    """1.0 if the prediction equals the reference exactly, else 0.0."""
    return 1.0 if prediction.strip() == reference.strip() else 0.0


def contains(prediction: str, reference: str, **_: Any) -> float:
    """1.0 if the prediction contains the reference string, else 0.0."""
    return 1.0 if reference.strip() in prediction else 0.0


def llm_judge(llm: BaseChatModel) -> Callable[..., float]:
    """Return a metric that uses an LLM to judge semantic correctness.

    Usage:
        judge = llm_judge(my_llm)
        score = judge(prediction=p, reference=r)
    """
    def _judge(prediction: str, reference: str, **_: Any) -> float:
        prompt = (
            "You are a strict grader. Decide whether the predicted answer is "
            "semantically correct given the reference. Reply with ONLY '1' or '0'.\n\n"
            f"Reference: {reference}\n"
            f"Prediction: {prediction}\n"
        )
        out = llm.invoke(prompt).content.strip()
        return 1.0 if out.startswith("1") else 0.0

    return _judge


def _tokenize(text: str) -> list[str]:
    """Whitespace tokenization, lowercased. Empty string -> []."""
    return text.lower().split()


def fuzzy_match(prediction: str, reference: str, **_: Any) -> float:
    """Token-level F1 score between prediction and reference.

    Simple whitespace tokenization (lowercased) + F1 over token sets.
    Returns float in [0, 1]. Useful for catching near-misses that
    exact_match / contains would score as 0.
    """
    pred_tokens = _tokenize(prediction)
    ref_tokens = _tokenize(reference)
    if not pred_tokens or not ref_tokens:
        return 0.0
    pred_set = set(pred_tokens)
    ref_set = set(ref_tokens)
    # Intersection over the *multisets* (so repeated tokens count), but
    # token counts are bounded by min(count_in_pred, count_in_ref) per
    # token — standard set-based F1 uses unique tokens, which is what we
    # implement here for simplicity.
    common = pred_set & ref_set
    if not common:
        return 0.0
    precision = len(common) / len(pred_set)
    recall = len(common) / len(ref_set)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def length_ratio(prediction: str, reference: str, **_: Any) -> float:
    """len(pred) / len(ref), clamped to [0, 1].

    Useful for detecting degenerate (too short) answers — a prediction
    that's 10x shorter than the reference scores 1.0 here only if the
    caller inverts it; raw ratio surfaces short answers as low values
    when ref is long. Predictions longer than the reference saturate at 1.0.
    """
    ref_len = len(reference)
    if ref_len == 0:
        # No reference to compare against; treat as "no signal" -> 0.0
        # rather than div-by-zero. Matches the empty-input convention used
        # by the IR metrics in retrieval_metrics.py.
        return 0.0
    ratio = len(prediction) / ref_len
    return min(1.0, max(0.0, ratio))
