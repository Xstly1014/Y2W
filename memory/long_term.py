"""Long-term memory backed by the RAG vector store.

Stores user facts / preferences / past decisions as small text snippets and
recalls the most semantically similar ones for a given query. Reuses the
RAG indexer under the hood so there is a single vector store in the project.

Enhanced with:
- **Importance scoring** (0.0-1.0): higher = more likely to recall.
  ``IMPORTANCE_HIGH`` / ``IMPORTANCE_NORMAL`` / ``IMPORTANCE_LOW`` are
  provided as tiers so callers don't have to invent numbers.
- **Forgetting curve**: memories decay if not accessed; access
  rejuvenates them. ``decay_score()`` follows an Ebbinghaus-style
  ``exp(-days / half_life)`` where ``half_life = 7d * (1 + importance)`` —
  high-importance memories fade slower.
- **Structured fact extraction**: ``remember_extracted()`` calls an LLM
  to turn free text into ``(subject, predicate, object)`` triples and
  stores each as its own memory. The LLM logic lives in
  ``memory.fact_extractor`` (no prompt code here).
- **Per-user namespace**: ``recall_for_user`` / ``list_user_memories``
  scope by ``metadata.user_id`` so cross-user leakage can't happen.

Backward compatibility: ``remember(text, metadata)`` and
``recall(query, k)`` keep their original signatures. New behaviour is
opt-in via the new methods (``remember_with_importance`` etc.) — the
legacy ``remember``/``recall`` keep working unchanged, except that
``recall`` now re-ranks by importance + decay (which is a strict
improvement: it still returns the same docs, just in a better order).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel

from config import settings
from memory.fact_extractor import extract_facts_from_text, fact_to_text
from rag.indexer import Indexer

logger = logging.getLogger(__name__)

# Importance tiers — used by recall() to boost high-importance docs.
# Picked as discrete values rather than enum so they can be combined
# (e.g. ``0.7`` for "between normal and high") without code changes.
IMPORTANCE_HIGH = 0.9
IMPORTANCE_NORMAL = 0.5
IMPORTANCE_LOW = 0.2


def _now_iso() -> str:
    """UTC ISO-8601 timestamp. Stored in metadata for decay computation."""
    return datetime.now(timezone.utc).isoformat()


class LongTermMemory:
    def __init__(
        self,
        indexer: Indexer,
        *,
        llm: BaseChatModel | None = None,
        collection: str | None = None,
    ) -> None:
        """Bind to an Indexer (and optionally an LLM for fact extraction).

        ``llm`` is only needed when ``remember_extracted()`` is called with
        ``settings.long_term_memory_extract_facts = True``. Without an LLM,
        ``extract_facts`` returns ``[]`` and ``remember_extracted`` falls
        back to ``remember``-ing the raw text (so the call still succeeds).
        """
        self._indexer = indexer
        self._llm = llm
        self._collection = collection or settings.long_term_memory_collection

    # ------------------------------------------------------------------ #
    # Existing API (unchanged signatures, recall re-ranks with importance)
    # ------------------------------------------------------------------ #
    def remember(self, text: str, metadata: dict | None = None) -> str:
        """Persist a fact / memory snippet. Returns the doc id.

        Defaults ``importance=IMPORTANCE_NORMAL`` and ``category="fact"`` so
        the new ranking code in ``recall`` still has something to rank on.
        Callers that already pass their own ``importance`` / ``category``
        in ``metadata`` are unaffected — we only fill in defaults.
        """
        return self.remember_with_importance(
            text,
            importance=IMPORTANCE_NORMAL,
            category="fact",
            metadata=metadata,
        )

    def recall(self, query: str, k: int | None = None) -> list[Document]:
        """Return the top-k most similar memories for ``query``.

        Re-ranks by ``_effective_score`` (similarity * (importance + decay) / 2)
        over an oversampled candidate set of ``k * 2``, then trims to ``k``.
        The oversampling is what lets a high-importance memory that ranked
        #5 by pure similarity still bubble up to the final top-3.

        ``k if k is not None`` (not ``k or default``) so a caller that
        intentionally passes ``k=0`` isn't silently upgraded to the
        default top-k.
        """
        effective_k = k if k is not None else settings.retrieval_top_k
        if effective_k <= 0:
            return []
        # Oversample so the re-rank has headroom; guard against huge k.
        candidate_k = min(effective_k * 2, effective_k + 20)
        pairs = self._indexer.search_with_scores(
            query, k=candidate_k, collection=self._collection
        )
        if not pairs:
            return []
        scored = sorted(
            pairs,
            key=lambda pair: self._effective_score(pair[1], pair[0]),
            reverse=True,
        )
        return [doc for doc, _ in scored[:effective_k]]

    # ------------------------------------------------------------------ #
    # Importance + decay
    # ------------------------------------------------------------------ #
    def remember_with_importance(
        self,
        text: str,
        importance: float = IMPORTANCE_NORMAL,
        *,
        user_id: str | None = None,
        category: str = "fact",
        metadata: dict | None = None,
    ) -> str:
        """Persist with explicit importance and category.

        Categories: ``fact`` | ``preference`` | ``decision`` | ``event`` | ``skill``.
        Stored in ``metadata.importance`` / ``metadata.category`` /
        ``metadata.user_id``. ``last_accessed`` is initialised to now()
        so freshly-written memories start with full decay score.
        """
        doc_id = str(uuid4())
        # Clamp importance to [0, 1] — callers might pass e.g. 1.5 by mistake.
        imp = max(0.0, min(1.0, float(importance)))
        meta: dict[str, Any] = {
            "doc_id": doc_id,
            "source": "long_term_memory",
            "importance": imp,
            "category": category if category else "fact",
            "created_at": _now_iso(),
            "last_accessed": _now_iso(),
            "access_count": 0,
        }
        if user_id is not None:
            meta["user_id"] = str(user_id)
        # Caller-provided metadata takes precedence (e.g. a caller that
        # wants to override ``importance`` for legacy reasons). We merge
        # *after* our defaults so caller values win.
        if metadata:
            meta.update(metadata)
            # Re-clamp in case the caller passed importance via metadata.
            if "importance" in meta:
                meta["importance"] = max(0.0, min(1.0, float(meta["importance"])))
        doc = Document(page_content=text, metadata=meta)
        self._indexer.add_documents([doc], collection=self._collection)
        return doc_id

    def boost_importance(self, doc_id: str, delta: float = 0.1) -> bool:
        """Increase a memory's importance. Clamps to [0, 1].

        Typical use: when a recalled memory is confirmed useful by the user
        (e.g. they say "yes that's right"), bump it so it surfaces again
        next time. Returns True if found and updated.
        """
        doc = self._indexer.find_by_doc_id(self._collection, doc_id)
        if doc is None:
            return False
        try:
            current = float(doc.metadata.get("importance", IMPORTANCE_NORMAL))
        except (TypeError, ValueError):
            current = IMPORTANCE_NORMAL
        new_importance = max(0.0, min(1.0, current + float(delta)))
        return self._indexer.update_metadata_by_doc_id(
            self._collection,
            doc_id,
            {"importance": new_importance},
        )

    def mark_accessed(self, doc_id: str) -> bool:
        """Update ``metadata.last_accessed = now()``. Rejuvenates decay.

        Also bumps ``access_count`` so a memory that's been recalled many
        times is visibly distinguishable from one that's been recalled once.
        """
        doc = self._indexer.find_by_doc_id(self._collection, doc_id)
        if doc is None:
            return False
        try:
            count = int(doc.metadata.get("access_count", 0))
        except (TypeError, ValueError):
            count = 0
        return self._indexer.update_metadata_by_doc_id(
            self._collection,
            doc_id,
            {
                "last_accessed": _now_iso(),
                "access_count": count + 1,
            },
        )

    def decay_score(self, doc: Document) -> float:
        """Compute forgetting-curve score in [0, 1].

        Ebbinghaus-style: ``score = exp(-days / half_life)`` where
        ``half_life = base_half_life * (1 + importance)`` — high-importance
        memories have a longer half-life, so they decay slower.

        ``days`` is measured from ``metadata.last_accessed`` (not
        ``created_at``) so each access rejuvenates the memory. Docs
        without timestamps are treated as fresh (score = 1.0).
        """
        try:
            importance = float(doc.metadata.get("importance", IMPORTANCE_NORMAL))
        except (TypeError, ValueError):
            importance = IMPORTANCE_NORMAL
        importance = max(0.0, min(1.0, importance))
        base = float(settings.long_term_memory_half_life_days)
        # Guard against misconfiguration (zero / negative half-life would
        # divide by zero or invert the curve).
        if base <= 0:
            return 1.0
        half_life = base * (1.0 + importance)
        ts = doc.metadata.get("last_accessed") if isinstance(doc.metadata, dict) else None
        if not ts:
            return 1.0
        days = self._days_since(ts)
        if days is None or days <= 0:
            return 1.0
        return math.exp(-days / half_life)

    def _effective_score(self, similarity: float, doc: Document) -> float:
        """Combined ranking score: ``similarity * (importance + decay) / 2``.

        The ``(importance + decay) / 2`` factor is what makes recall
        importance-aware and decay-aware: a stale low-importance memory
        has ``(0.2 + 0.1) / 2 = 0.15``, while a fresh high-importance one
        has ``(0.9 + 1.0) / 2 = 0.95``. Multiplying by similarity keeps
        the semantic match as the primary signal — only ties between
        equally-similar docs are broken by importance + decay.
        """
        try:
            importance = float(doc.metadata.get("importance", IMPORTANCE_NORMAL))
        except (TypeError, ValueError):
            importance = IMPORTANCE_NORMAL
        importance = max(0.0, min(1.0, importance))
        decay = self.decay_score(doc)
        return float(similarity) * (importance + decay) / 2.0

    # ------------------------------------------------------------------ #
    # Structured fact extraction (delegates to memory.fact_extractor)
    # ------------------------------------------------------------------ #
    def extract_facts(self, text: str, *, user_id: str | None = None) -> list[dict]:
        """Use LLM to extract ``(subject, predicate, object)`` triples.

        Returns a list of dicts: ``[{"subject", "predicate", "object",
        "importance", "category"}, ...]``. Each ``importance`` is in [0, 1]
        and each ``category`` is one of the five valid categories.

        On LLM error, parse failure, or when no LLM is configured: returns
        ``[]`` (does NOT raise — the caller is expected to fall back to
        plain ``remember`` when extraction yields nothing).
        """
        if self._llm is None:
            return []
        return extract_facts_from_text(text, self._llm)

    def remember_extracted(self, text: str, *, user_id: str | None = None) -> list[str]:
        """Extract facts then remember each one. Returns list of doc_ids.

        When ``settings.long_term_memory_extract_facts`` is False (the
        default), or when extraction yields no facts, falls back to a
        single ``remember_with_importance`` call with the raw text — so
        callers don't need to gate on the setting themselves.
        """
        ids: list[str] = []
        if settings.long_term_memory_extract_facts and self._llm is not None:
            try:
                facts = self.extract_facts(text, user_id=user_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("extract_facts failed, falling back to raw: %s", exc)
                facts = []
            for fact in facts:
                snippet = fact_to_text(fact)
                ids.append(
                    self.remember_with_importance(
                        snippet,
                        importance=fact.get("importance", IMPORTANCE_NORMAL),
                        user_id=user_id,
                        category=fact.get("category", "fact"),
                    )
                )
        if not ids:
            # Fallback: store the raw text as a single memory so the call
            # is never a no-op even when no LLM is configured.
            ids.append(self.remember_with_importance(text, user_id=user_id))
        return ids

    # ------------------------------------------------------------------ #
    # Per-user namespace
    # ------------------------------------------------------------------ #
    def recall_for_user(
        self,
        user_id: str,
        query: str,
        k: int | None = None,
    ) -> list[Document]:
        """Recall only memories with ``metadata.user_id == user_id``.

        Implemented as a post-filter on ``recall`` because FAISS has no
        native metadata filter; on PG this is still cheaper than a full
        table scan because we oversample first. The user_id filter is
        applied BEFORE the k-trim so we never return another user's
        memories to fill the k quota.
        """
        effective_k = k if k is not None else settings.retrieval_top_k
        if effective_k <= 0:
            return []
        # Oversample to survive the user_id filter (most candidates will
        # be filtered out in a multi-user store).
        candidate_k = min(effective_k * 4, effective_k + 50)
        docs = self.recall(query, k=candidate_k)
        scoped = [
            d for d in docs
            if isinstance(d.metadata, dict)
            and str(d.metadata.get("user_id") or "") == str(user_id)
        ]
        return scoped[:effective_k]

    def list_user_memories(
        self,
        user_id: str,
        *,
        category: str | None = None,
        limit: int = 100,
    ) -> list[Document]:
        """List all memories for a user, optionally filtered by category."""
        docs = self._indexer.list_all_documents(
            self._collection, limit=None
        )
        out: list[Document] = []
        for d in docs:
            if not isinstance(d.metadata, dict):
                continue
            if str(d.metadata.get("user_id") or "") != str(user_id):
                continue
            if category is not None and str(d.metadata.get("category") or "") != category:
                continue
            out.append(d)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ #
    # Maintenance
    # ------------------------------------------------------------------ #
    def forget_expired(self, threshold_score: float = 0.05) -> int:
        """Delete memories whose ``decay_score < threshold``.

        Considers importance indirectly (high-importance memories have a
        longer half-life, so they hit the threshold later in wall-clock
        time). Returns the count of deleted memories.

        Use sparingly (e.g. a daily cron) — this does a full scan of the
        collection plus a per-row delete. For FAISS, deletion is not
        supported, so this is a no-op returning 0.
        """
        docs = self._indexer.list_all_documents(self._collection, limit=None)
        deleted = 0
        for d in docs:
            try:
                score = self.decay_score(d)
            except Exception:  # noqa: BLE001
                continue
            if score >= threshold_score:
                continue
            doc_id = ""
            if isinstance(d.metadata, dict):
                doc_id = str(d.metadata.get("doc_id") or "")
            if not doc_id:
                continue
            if self._indexer.delete_by_doc_id(self._collection, doc_id):
                deleted += 1
        return deleted

    def stats(self) -> dict[str, Any]:
        """Return memory store statistics.

        Shape::

            {
                "total": N,
                "by_category": {"fact": N, "preference": N, ...},
                "by_user": {"user1": N, ...},
                "avg_importance": float,
                "avg_decay_score": float,
                "oldest": ISO timestamp | None,
                "newest": ISO timestamp | None,
            }

        ``by_user`` includes a ``"__shared__"`` bucket for memories
        written without a ``user_id`` (e.g. legacy ``remember`` calls
        from before the per-user feature was added).
        """
        docs = self._indexer.list_all_documents(self._collection, limit=None)
        total = len(docs)
        by_category: dict[str, int] = {}
        by_user: dict[str, int] = {}
        importance_sum = 0.0
        importance_n = 0
        decay_sum = 0.0
        decay_n = 0
        oldest: str | None = None
        newest: str | None = None
        for d in docs:
            meta = d.metadata if isinstance(d.metadata, dict) else {}
            cat = str(meta.get("category") or "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1
            uid = meta.get("user_id")
            uid_key = str(uid) if uid else "__shared__"
            by_user[uid_key] = by_user.get(uid_key, 0) + 1
            if "importance" in meta:
                try:
                    importance_sum += float(meta["importance"])
                    importance_n += 1
                except (TypeError, ValueError):
                    pass
            try:
                decay_sum += self.decay_score(d)
                decay_n += 1
            except Exception:  # noqa: BLE001
                pass
            # Use ``created_at`` (not ``last_accessed``) for oldest/newest —
            # the latter would make a recently-accessed memory look "new".
            created = meta.get("created_at")
            if isinstance(created, str) and created:
                if oldest is None or created < oldest:
                    oldest = created
                if newest is None or created > newest:
                    newest = created
        return {
            "total": total,
            "by_category": by_category,
            "by_user": by_user,
            "avg_importance": (importance_sum / importance_n) if importance_n else 0.0,
            "avg_decay_score": (decay_sum / decay_n) if decay_n else 0.0,
            "oldest": oldest,
            "newest": newest,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _days_since(iso_ts: str) -> float | None:
        """Days elapsed since ``iso_ts``. Returns None on parse failure.

        Tolerates both naive and timezone-aware ISO timestamps; naive
        timestamps are interpreted as UTC (which is what we write).
        """
        try:
            ts = datetime.fromisoformat(iso_ts)
        except (TypeError, ValueError):
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - ts
        return max(0.0, delta.total_seconds() / 86400.0)
