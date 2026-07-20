"""Cost estimation.

LangChain's `AIMessage` carries a `usage_metadata` dict (if the model
returns usage) with `input_tokens` / `output_tokens` / `total_tokens`.
We extract it and multiply by a per-model price table.

The price table ships as `_DEFAULT_PRICE_TABLE` (in-code defaults) and
can be overridden by creating `config/price_table.json` at the project
root — no code change needed to update prices. See P2-10.

JSON schema:
    {
        "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
        ...
    }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PRICE_TABLE_PATH = _PROJECT_ROOT / "config" / "price_table.json"

# USD per 1M tokens. In-code defaults; overridden by config/price_table.json
# if it exists. Missing model -> free (so cost shows 0.0 rather than crashing).
_DEFAULT_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # (input_per_1M, output_per_1M)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "deepseek-v4-pro": (0.27, 1.10),  # placeholder; verify with provider
    "deepseek-chat": (0.14, 0.28),
}


def _load_price_table() -> dict[str, tuple[float, float]]:
    """Load price table: defaults merged with optional JSON override.

    Tries to read ``config/price_table.json``. If present, its entries
    override (and can add to) the in-code defaults. On any read/parse
    error, falls back to defaults and logs a warning.
    """
    table = dict(_DEFAULT_PRICE_TABLE)
    if not _PRICE_TABLE_PATH.exists():
        return table
    try:
        raw = json.loads(_PRICE_TABLE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("price_table.json must be a JSON object")
        for model, vals in raw.items():
            if not isinstance(vals, dict):
                continue
            in_p = float(vals.get("input_per_1m", 0.0))
            out_p = float(vals.get("output_per_1m", 0.0))
            table[model] = (in_p, out_p)
        logger.info("loaded price overrides from %s (%d models)", _PRICE_TABLE_PATH, len(raw))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(
            "failed to load %s, using in-code defaults: %s", _PRICE_TABLE_PATH, exc,
        )
    return table


PRICE_TABLE: dict[str, tuple[float, float]] = _load_price_table()


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
