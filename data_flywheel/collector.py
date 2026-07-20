"""Badcase / goodcase collector.

Wraps two `JsonlStore`s. The eval runner calls `record_case()` with an
`EvalResult`; the agent loop may also call `record_interaction()` for
live traffic.

Extended with auto-classification, deduplication and priority ranking:
  * `record_case_classified` / `record_interaction_classified` add
    `metadata.category`, `metadata.confidence`, `occurrence_count`,
    `first_seen` / `last_seen`, and merge duplicates in-place.
  * `list_badcases` / `list_by_priority` / `category_stats` query the
    bad store through the classifier / prioritizer lenses.
  * `deduplicate_existing` retroactively merges dup records that were
    written by the legacy `record_case` / `record_interaction` paths.

`embeddings` and `llm` are optional — when `None`, near-dup detection
and LLM classification degrade gracefully (rule-only / exact-only).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from config import settings
from data_flywheel.classifier import classify
from data_flywheel.deduper import dedup_check
from data_flywheel.prioritizer import top_n
from data_flywheel.storage import JsonlStore

logger = logging.getLogger(__name__)


class BadCaseCollector:
    def __init__(
        self,
        badcase_path: Any | None = None,
        goodcase_path: Any | None = None,
        *,
        embeddings: Any | None = None,  # optional, for near-dup detection
        llm: Any | None = None,         # optional, for LLM classification
    ) -> None:
        self.bad_store = JsonlStore(badcase_path or settings.badcase_store_path)
        self.good_store = JsonlStore(goodcase_path or settings.goodcase_store_path)
        self._embeddings = embeddings
        self._llm = llm

    def _envelope(
        self,
        user_input: str,
        prediction: str,
        expected: str | None,
        score: float | None,
        passed: bool,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "prediction": prediction,
            "expected": expected,
            "score": score,
            "passed": passed,
            "metadata": metadata or {},
        }

    def record_case(self, result) -> None:
        """Record an EvalResult to the bad or good store based on `passed`."""
        record = self._envelope(
            user_input=result.user_input,
            prediction=result.prediction,
            expected=result.expected,
            score=result.score,
            passed=result.passed,
            metadata={"case_id": result.case_id, "metric": result.metric, **result.metadata},
        )
        target = self.good_store if result.passed else self.bad_store
        target.append(record)
        logger.debug("recorded %s case id=%s", "good" if result.passed else "bad", record["id"])

    def record_interaction(
        self,
        user_input: str,
        prediction: str,
        passed: bool,
        expected: str | None = None,
        score: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a live agent interaction (e.g. user thumbs-up/down)."""
        record = self._envelope(
            user_input=user_input,
            prediction=prediction,
            expected=expected,
            score=score,
            passed=passed,
            metadata={"source": "live", **(metadata or {})},
        )
        target = self.good_store if passed else self.bad_store
        target.append(record)

    def stats(self) -> dict[str, int]:
        return {"bad": self.bad_store.count(), "good": self.good_store.count()}

    # ------------------------------------------------------------------ #
    # New: classified + deduplicated recording
    # ------------------------------------------------------------------ #
    def record_case_classified(self, result, *, dedup: bool = True) -> dict[str, Any]:
        """Like `record_case` but adds classification + dedup metadata.

        Adds:
        - metadata.category   (auto-classified)
        - metadata.confidence
        - occurrence_count    (incremented if dup found, else 1)
        - first_seen / last_seen timestamps

        When `dedup=True` and a duplicate is found in the target store,
        the original record's `occurrence_count` is incremented in-place
        and NO new record is appended. The updated original is returned.

        Returns the stored (or updated) record dict.
        """
        target = self.good_store if result.passed else self.bad_store

        cls_result = classify(
            user_input=result.user_input,
            prediction=result.prediction,
            expected=result.expected,
            llm=self._llm,
        )

        now = datetime.now().isoformat()
        record = self._envelope(
            user_input=result.user_input,
            prediction=result.prediction,
            expected=result.expected,
            score=result.score,
            passed=result.passed,
            metadata={
                "case_id": result.case_id,
                "metric": result.metric,
                **result.metadata,
                "category": cls_result.category,
                "confidence": cls_result.confidence,
            },
        )
        record["occurrence_count"] = 1
        record["first_seen"] = now
        record["last_seen"] = now

        if dedup:
            existing = list(target.iter_records())
            dup = dedup_check(
                result.user_input, existing, embeddings=self._embeddings,
            )
            if dup.is_duplicate and dup.duplicate_of:
                updated = self._increment_occurrence(target, dup.duplicate_of, now)
                logger.debug(
                    "merged dup case into id=%s (strategy=%s)",
                    dup.duplicate_of, dup.strategy,
                )
                return updated

        target.append(record)
        logger.debug("recorded classified case id=%s category=%s", record["id"], cls_result.category)
        return record

    def record_interaction_classified(
        self,
        user_input: str,
        prediction: str,
        passed: bool,
        expected: str | None = None,
        score: float | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        dedup: bool = True,
    ) -> dict[str, Any]:
        """Like `record_interaction` but classified + deduped."""
        target = self.good_store if passed else self.bad_store

        cls_result = classify(
            user_input=user_input,
            prediction=prediction,
            expected=expected,
            llm=self._llm,
        )

        now = datetime.now().isoformat()
        record = self._envelope(
            user_input=user_input,
            prediction=prediction,
            expected=expected,
            score=score,
            passed=passed,
            metadata={
                "source": "live",
                **(metadata or {}),
                "category": cls_result.category,
                "confidence": cls_result.confidence,
            },
        )
        record["occurrence_count"] = 1
        record["first_seen"] = now
        record["last_seen"] = now

        if dedup:
            existing = list(target.iter_records())
            dup = dedup_check(
                user_input, existing, embeddings=self._embeddings,
            )
            if dup.is_duplicate and dup.duplicate_of:
                updated = self._increment_occurrence(target, dup.duplicate_of, now)
                logger.debug(
                    "merged dup interaction into id=%s (strategy=%s)",
                    dup.duplicate_of, dup.strategy,
                )
                return updated

        target.append(record)
        return record

    def _increment_occurrence(
        self,
        store: JsonlStore,
        record_id: str,
        new_timestamp: str,
    ) -> dict[str, Any]:
        """Increment `occurrence_count` on an existing record by rewriting the store.

        `JsonlStore` is append-only, so we read all records, mutate the
        matching one, then `clear()` + re-`append()` everything. Cross-
        process safety is NOT guaranteed (same as `JsonlStore` itself).

        Returns the updated record dict.
        """
        records = list(store.iter_records())
        updated: dict[str, Any] | None = None
        for rec in records:
            if rec.get("id") == record_id:
                rec["occurrence_count"] = rec.get("occurrence_count", 1) + 1
                rec["last_seen"] = new_timestamp
                updated = rec
                break
        store.clear()
        for rec in records:
            store.append(rec)
        # `updated` is the same dict reference we just re-appended.
        return dict(updated) if updated else {}

    # ------------------------------------------------------------------ #
    # New: query / analyze
    # ------------------------------------------------------------------ #
    def list_badcases(
        self,
        *,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return badcases, optionally filtered by category.

        Category lookup is tolerant of legacy records: checks top-level
        `category` first, then `metadata.category`, then defaults to
        "other" so old JSONL data still parses.
        """
        out: list[dict[str, Any]] = []
        for rec in self.bad_store.iter_records():
            if category is None:
                out.append(rec)
            else:
                rec_cat = rec.get("category") or rec.get("metadata", {}).get("category", "other")
                if rec_cat == category:
                    out.append(rec)
            if len(out) >= limit:
                break
        return out

    def list_by_priority(self, n: int = 10) -> list[dict[str, Any]]:
        """Return top-N highest-priority badcases (uses prioritizer).

        Each returned record gets a `priority_score` field injected by
        `prioritizer.top_n`.
        """
        records = list(self.bad_store.iter_records())
        return top_n(records, n)

    def category_stats(self) -> dict[str, int]:
        """Return `{category: count}` for all badcases.

        Records without a category field are bucketed under "other" so
        legacy JSONL data still produces a sensible distribution.
        """
        counts: dict[str, int] = {}
        for rec in self.bad_store.iter_records():
            cat = rec.get("category") or rec.get("metadata", {}).get("category", "other")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def deduplicate_existing(self, *, dry_run: bool = True) -> dict[str, int]:
        """Scan existing badcases and merge duplicates in-place.

        For each record, checks against the already-merged unique set:
          - Exact match (normalized text) → merge, bump `occurrence_count`
          - Near match (embedding cosine ≥ threshold) → merge too
          - Otherwise → keep as a new unique record

        Merging preserves the earlier `first_seen` and the later `last_seen`
        so the audit trail spans the full window of occurrences.

        Args:
            dry_run: When True, only reports what would be merged; the
                bad store is left untouched. When False, the store is
                rewritten with the merged record set.

        Returns:
            `{"exact_merged": N, "near_merged": M, "remaining": K}` where
            `remaining` is the post-merge unique count.
        """
        records = list(self.bad_store.iter_records())
        merged: list[dict[str, Any]] = []
        exact_merged = 0
        near_merged = 0

        for rec in records:
            user_input = rec.get("user_input", "")
            dup = dedup_check(
                user_input, merged, embeddings=self._embeddings,
            )
            if dup.is_duplicate and dup.duplicate_of:
                for orig in merged:
                    if orig.get("id") == dup.duplicate_of:
                        orig["occurrence_count"] = (
                            orig.get("occurrence_count", 1) + rec.get("occurrence_count", 1)
                        )
                        # Preserve earliest first_seen.
                        orig_first = orig.get("first_seen") or orig.get("timestamp", "")
                        rec_first = rec.get("first_seen") or rec.get("timestamp", "")
                        if rec_first and (not orig_first or rec_first < orig_first):
                            orig["first_seen"] = rec_first
                        # Preserve latest last_seen.
                        orig_last = orig.get("last_seen") or orig.get("timestamp", "")
                        rec_last = rec.get("last_seen") or rec.get("timestamp", "")
                        if rec_last and (not orig_last or rec_last > orig_last):
                            orig["last_seen"] = rec_last
                        break
                if dup.strategy == "exact":
                    exact_merged += 1
                elif dup.strategy == "near":
                    near_merged += 1
            else:
                # Initialize tracking fields on legacy records so downstream
                # prioritizer / stats don't have to keep special-casing.
                if "occurrence_count" not in rec:
                    rec["occurrence_count"] = 1
                if "first_seen" not in rec:
                    rec["first_seen"] = rec.get("timestamp", "")
                if "last_seen" not in rec:
                    rec["last_seen"] = rec.get("timestamp", "")
                merged.append(rec)

        if not dry_run:
            self.bad_store.clear()
            for rec in merged:
                self.bad_store.append(rec)

        return {
            "exact_merged": exact_merged,
            "near_merged": near_merged,
            "remaining": len(merged),
        }
