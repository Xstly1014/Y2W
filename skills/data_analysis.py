"""Data-analysis skill.

Pure-Python tools for quick structural / statistical summaries of CSV and
JSON strings. No LLM call — these are deterministic and free, intended as
lightweight inspection helpers the agent can reach for before deciding
whether a heavier analysis pass is warranted.

  * `analyze_csv`  — per-column stats (numeric: mean/std/min/max; categorical:
                     unique count + top-3 values)
  * `analyze_json` — structural summary (top-level keys, value-type
                     distribution, max nesting depth)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
from collections import Counter
from typing import Any

from langchain_core.tools import BaseTool, tool

from skills.base import Skill

logger = logging.getLogger(__name__)


def _is_number(s: Any) -> bool:
    """True if `s` looks like an int/float (and isn't an empty/None)."""
    if s is None:
        return False
    if isinstance(s, (int, float)):
        # bool is a subclass of int — exclude it explicitly.
        return not isinstance(s, bool)
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _to_float(s: Any) -> float:
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    return float(str(s).strip())


def _analyze_csv(csv_text: str) -> str:
    """Compute per-column stats for the CSV string."""
    if not csv_text or not csv_text.strip():
        return "analyze_csv error: empty input"
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze_csv parse failed")
        return f"analyze_csv error: {exc}"

    if not rows:
        return "analyze_csv error: no data rows"
    if not reader.fieldnames:
        return "analyze_csv error: no columns (header missing)"

    col_results: dict[str, dict[str, Any]] = {}
    for col in reader.fieldnames:
        values = [r.get(col) for r in rows]
        non_empty = [v for v in values if v is not None and str(v).strip() != ""]

        # Treat as numeric if EVERY non-empty value parses as a number.
        numeric = non_empty and all(_is_number(v) for v in non_empty)
        if numeric:
            nums = [_to_float(v) for v in non_empty]
            n = len(nums)
            mean = sum(nums) / n
            variance = sum((x - mean) ** 2 for x in nums) / n
            std = math.sqrt(variance)
            col_results[col] = {
                "type": "numeric",
                "count": n,
                "mean": round(mean, 4),
                "std": round(std, 4),
                "min": min(nums),
                "max": max(nums),
            }
        else:
            counts = Counter(non_empty)
            top3 = counts.most_common(3)
            col_results[col] = {
                "type": "categorical",
                "count": len(non_empty),
                "unique": len(counts),
                "top3": [{"value": v, "count": c} for v, c in top3],
            }

    summary = {
        "rows": len(rows),
        "columns": len(reader.fieldnames),
        "stats": col_results,
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def _max_depth(obj: Any, current: int = 1) -> int:
    """Return the maximum nesting depth of a JSON-decoded object.

    Scalars are depth 1; a list/dict adds one level per nested container.
    """
    if isinstance(obj, dict):
        if not obj:
            return current + 1
        return max((_max_depth(v, current + 1) for v in obj.values()), default=current + 1)
    if isinstance(obj, list):
        if not obj:
            return current + 1
        return max((_max_depth(v, current + 1) for v in obj), default=current + 1)
    return current


def _analyze_json(json_text: str) -> str:
    """Return a structural summary of the JSON string."""
    if not json_text or not json_text.strip():
        return "analyze_json error: empty input"
    try:
        data = json.loads(json_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze_json parse failed")
        return f"analyze_json error: {exc}"

    # Top-level keys only make sense for an object.
    top_keys: list[str] = []
    if isinstance(data, dict):
        top_keys = list(data.keys())

    # Walk the whole structure to build a value-type distribution.
    type_counts: Counter[str] = Counter()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            type_counts["object"] += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            type_counts["array"] += 1
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            type_counts["boolean"] += 1
        elif isinstance(node, int):
            type_counts["integer"] += 1
        elif isinstance(node, float):
            type_counts["float"] += 1
        elif isinstance(node, str):
            type_counts["string"] += 1
        elif node is None:
            type_counts["null"] += 1
        else:
            type_counts[f"other:{type(node).__name__}"] += 1

    walk(data)
    depth = _max_depth(data)

    summary = {
        "top_level_type": type(data).__name__,
        "top_level_keys": top_keys,
        "value_type_distribution": dict(type_counts),
        "max_nesting_depth": depth,
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


class DataAnalysisSkill(Skill):
    """Contribute CSV / JSON analysis tools (pure Python, no LLM)."""

    name: str = "data_analysis"
    description: str = "Pure-Python CSV/JSON statistical and structural analysis."
    version: str = "0.1.0"
    tags: tuple[str, ...] = ("analysis", "data")
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    enabled_by_default: bool = True

    def build_tools(self) -> list[BaseTool]:
        @tool
        def analyze_csv(csv_text: str) -> str:
            """Compute per-column statistics for a CSV string.

            Numeric columns get count/mean/std/min/max; categorical columns
            get count/unique count + top-3 most frequent values. Returns a
            JSON summary. Use this when the user shares CSV data and wants
            a quick statistical overview.
            """
            return _analyze_csv(csv_text)

        @tool
        def analyze_json(json_text: str) -> str:
            """Return a structural summary of a JSON string.

            Reports top-level type, top-level keys (if object), value-type
            distribution across the whole tree, and the maximum nesting
            depth. Use this to inspect unfamiliar JSON before deeper
            processing.
            """
            return _analyze_json(json_text)

        return [analyze_csv, analyze_json]
