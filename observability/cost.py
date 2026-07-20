"""Cost estimation.

LangChain's `AIMessage` carries a `usage_metadata` dict (if the model
returns usage) with `input_tokens` / `output_tokens` / `total_tokens`.
We extract it and multiply by a per-model price table.

Price table is intentionally tiny and out-of-date — adjust before
relying on it for real billing.
"""
from __future__ import annotations

from typing import Any

# USD per 1M tokens. Update from provider docs when you care about accuracy.
# Missing model -> free (so cost shows 0.0 rather than crashing).
PRICE_TABLE: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deepseek-v4-pro": (0.27, 1.10),  # placeholder; verify with provider
    "deepseek-chat": (0.14, 0.28),
}


def extract_usage(message: Any) -> dict[str, int]:
    """Pull token counts out of a LangChain AIMessage.

    Returns {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if usage metadata is missing.
    """
    usage = getattr(message, "usage_metadata", None) or {}
    return {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> float:
    """Estimate USD cost for a single LLM call."""
    price = PRICE_TABLE.get(model)
    if price is None:
        return 0.0
    in_price, out_price = price
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price
