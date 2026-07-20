"""Post-training data pipeline.

Builds two artefacts from the flywheel stores:
  * `sft.jsonl`  — supervised fine-tuning set from good cases.
                   One record per line: {"messages": [{"role":..., "content":...}]}.
  * `dpo.jsonl`  — preference pairs: {"prompt":..., "chosen":..., "rejected":...}.
                   Built by matching bad predictions against good predictions
                   on similar inputs (Jaccard similarity on token sets).

Usage:
    pipeline = PostTrainingPipeline()
    pipeline.build()
    print(pipeline.artefact_paths())
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config import settings
from data_flywheel.collector import BadCaseCollector

logger = logging.getLogger(__name__)

# Minimum Jaccard similarity between bad-input and good-input token sets
# for the two to be paired as a DPO preference. Lower = more permissive.
_DPO_MIN_JACCARD = 0.3


def _token_set(text: str) -> set[str]:
    """Lowercase whitespace-tokenised set, ignoring single-char tokens."""
    return {tok for tok in text.lower().split() if len(tok) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _cosine(a: Any, b: Any) -> float:
    """Cosine similarity between two numeric vectors (lists or 1-D arrays).

    Pure-Python implementation: works for `list[float]` and any object
    supporting iteration / indexing (e.g. numpy arrays). Returns 0.0 for
    zero-norm or mismatched inputs.
    """
    if a is None or b is None:
        return 0.0
    try:
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            xf = float(x)
            yf = float(y)
            dot += xf * yf
            na += xf * xf
            nb += yf * yf
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / ((na ** 0.5) * (nb ** 0.5))
    except (TypeError, ValueError):
        return 0.0


class PostTrainingPipeline:
    def __init__(self, collector: BadCaseCollector | None = None) -> None:
        self.collector = collector or BadCaseCollector()
        self.output_dir: Path = settings.post_train_output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ---------- SFT ----------
    def build_sft(self) -> Path:
        """Write `sft.jsonl` from good cases (user_input -> prediction)."""
        out = self.output_dir / "sft.jsonl"
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for rec in self.collector.good_store.iter_records():
                messages = [
                    {"role": "user", "content": rec["user_input"]},
                    {"role": "assistant", "content": rec["prediction"]},
                ]
                f.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
                n += 1
        logger.info("Wrote %d SFT records to %s", n, out)
        return out

    # ---------- DPO ----------
    def build_dpo(self) -> Path:
        """Write `dpo.jsonl` preference pairs.

        For each bad case, find the good case whose user_input is most
        similar (Jaccard on token sets). Pair them only if similarity
        exceeds `_DPO_MIN_JACCARD`. This is still crude — replace with
        embedding-based matching when scale demands it.
        """
        goods = list(self.collector.good_store.iter_records())
        good_tokens = [(g, _token_set(g["user_input"])) for g in goods]
        out = self.output_dir / "dpo.jsonl"
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for bad in self.collector.bad_store.iter_records():
                bad_tokens = _token_set(bad["user_input"])
                best_g = None
                best_sim = 0.0
                for g, toks in good_tokens:
                    sim = _jaccard(bad_tokens, toks)
                    if sim > best_sim:
                        best_sim = sim
                        best_g = g
                if best_g is None or best_sim < _DPO_MIN_JACCARD:
                    continue
                f.write(
                    json.dumps(
                        {
                            "prompt": bad["user_input"],
                            "chosen": best_g["prediction"],
                            "rejected": bad["prediction"],
                            "similarity": round(best_sim, 3),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                n += 1
        logger.info("Wrote %d DPO pairs to %s", n, out)
        return out

    def build(self) -> None:
        self.build_sft()
        self.build_dpo()

    def artefact_paths(self) -> dict[str, Path]:
        return {
            "sft": self.output_dir / "sft.jsonl",
            "dpo": self.output_dir / "dpo.jsonl",
        }

    # ------------------------------------------------------------------ #
    # Enhanced builders (backward-compatible: do not replace build_sft /
    # build_dpo; opt-in via these methods).
    # ------------------------------------------------------------------ #
    def build_sft_filtered(
        self,
        *,
        min_response_length: int = 10,
        max_response_length: int = 4000,
        dedup: bool = True,
    ) -> Path:
        """Like `build_sft` but with quality filtering.

        Filters:
        - Empty assistant responses
        - Too short (< `min_response_length` chars)
        - Too long (> `max_response_length` chars)
        - Duplicates (normalized user input) when `dedup=True`

        Writes the filtered set to `sft.jsonl` (overwrites the legacy
        artefact so downstream tooling picks up the cleaned data).
        Returns the path to the written file.
        """
        out = self.output_dir / "sft.jsonl"
        seen: set[str] = set()
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for rec in self.collector.good_store.iter_records():
                user_content = rec.get("user_input", "") or ""
                asst_content = rec.get("prediction", "") or ""

                if not asst_content.strip():
                    continue
                if len(asst_content) < min_response_length:
                    continue
                if len(asst_content) > max_response_length:
                    continue

                if dedup:
                    norm = re.sub(r"\s+", " ", user_content.lower().strip())
                    if norm in seen:
                        continue
                    seen.add(norm)

                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": asst_content},
                ]
                f.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")
                n += 1
        logger.info(
            "Wrote %d filtered SFT records to %s (min=%d, max=%d, dedup=%s)",
            n, out, min_response_length, max_response_length, dedup,
        )
        return out

    def build_dpo_enhanced(
        self,
        *,
        min_similarity: float = 0.5,
        embeddings: Any | None = None,
    ) -> Path:
        """Like `build_dpo` but with stricter matching.

        Improvements over `build_dpo`:
        - Higher similarity threshold (default 0.5, vs. 0.3 in `build_dpo`)
        - Uses embedding cosine similarity when `embeddings` is provided
          (any object with an `embed(text) -> list[float]` / array-like
          method); otherwise falls back to Jaccard on token sets
        - Skips pairs where chosen == rejected (exact match after strip)
        - Skips pairs where the bad-side prediction (rejected) is empty

        Writes to `dpo.jsonl` (overwrites the legacy artefact). Returns
        the path to the written file.
        """
        goods = list(self.collector.good_store.iter_records())
        good_tokens = [(g, _token_set(g["user_input"])) for g in goods]

        # Pre-compute good-case embeddings once if an embeddings client
        # was supplied. We tolerate any object exposing `.embed(text)`.
        good_embeddings: list[tuple[dict[str, Any], Any]] = []
        if embeddings is not None:
            for g in goods:
                try:
                    vec = embeddings.embed(g.get("user_input", ""))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("embedding failed for good case: %s", exc)
                    vec = None
                good_embeddings.append((g, vec))

        out = self.output_dir / "dpo.jsonl"
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for bad in self.collector.bad_store.iter_records():
                bad_input = bad.get("user_input", "") or ""
                rejected = bad.get("prediction", "") or ""

                # Skip empty rejected (chosen would also be meaningless).
                if not rejected.strip():
                    continue

                best_g: dict[str, Any] | None = None
                best_sim = 0.0

                if embeddings is not None and good_embeddings:
                    try:
                        bad_vec = embeddings.embed(bad_input)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("embedding failed for bad case: %s", exc)
                        bad_vec = None
                    if bad_vec is not None:
                        for g, g_vec in good_embeddings:
                            if g_vec is None:
                                continue
                            sim = _cosine(bad_vec, g_vec)
                            if sim > best_sim:
                                best_sim = sim
                                best_g = g
                else:
                    bad_tokens = _token_set(bad_input)
                    for g, toks in good_tokens:
                        sim = _jaccard(bad_tokens, toks)
                        if sim > best_sim:
                            best_sim = sim
                            best_g = g

                if best_g is None or best_sim < min_similarity:
                    continue

                chosen = best_g.get("prediction", "") or ""
                # Skip identical pairs — they carry no preference signal.
                if chosen.strip() == rejected.strip():
                    continue

                f.write(
                    json.dumps(
                        {
                            "prompt": bad_input,
                            "chosen": chosen,
                            "rejected": rejected,
                            "similarity": round(best_sim, 3),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                n += 1
        logger.info(
            "Wrote %d enhanced DPO pairs to %s (min_sim=%.2f, embeddings=%s)",
            n, out, min_similarity, "yes" if embeddings is not None else "no",
        )
        return out
