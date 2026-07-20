"""High-level indexer over the FAISS vector store.

A single `Indexer` instance manages multiple named collections. Each call to
`add_documents` / `search` routes to the right in-memory FAISS index, which
is lazily created (empty) on first write and auto-saved to disk.
"""
from __future__ import annotations

from typing import Iterable

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from rag.vectorstore import build_vectorstore, load_vectorstore, save_vectorstore


class Indexer:
    def __init__(self, embeddings: Embeddings) -> None:
        self._embeddings = embeddings
        self._stores: dict[str, object] = {}

    # ----- internal helpers -----
    def _get_or_load(self, collection: str):
        if collection in self._stores:
            return self._stores[collection]
        store = load_vectorstore(self._embeddings, collection)
        if store is None:
            store = build_vectorstore(self._embeddings, collection)
        self._stores[collection] = store
        return store

    # ----- public API -----
    def add_documents(
        self,
        documents: Iterable[Document],
        collection: str = "documents",
    ) -> None:
        store = self._get_or_load(collection)
        store.add_documents(list(documents))
        save_vectorstore(store, collection)

    def search(
        self,
        query: str,
        k: int = 3,
        collection: str = "documents",
    ) -> list[Document]:
        store = self._get_or_load(collection)
        return store.similarity_search(query, k=k)

    def list_documents(self, collection: str = "documents", limit: int = 200) -> list[Document]:
        """Return up to `limit` documents stored in the collection (any order).

        Used by the KB management UI to render the document table. Relies on
        the underlying FAISS docstore; if the collection does not exist yet,
        returns an empty list.
        """
        try:
            store = self._get_or_load(collection)
        except Exception as exc:  # noqa: BLE001
            # Don't silently swallow — log so a misconfigured collection
            # (e.g. corrupt index file) surfaces in observability instead
            # of presenting an empty UI that hides the root cause.
            import logging
            logging.getLogger(__name__).warning(
                "list_documents: could not load collection %r: %s",
                collection, exc,
            )
            return []
        docstore = getattr(store, "docstore", None)
        if docstore is None:
            return []
        # FAISS's InMemoryDocstore exposes a private ``_dict`` mapping.
        # Use the public ``dict`` attribute when present (newer langchain
        # versions); fall back to ``_dict`` for compatibility. Wrap in
        # try/except so an unexpected store implementation doesn't crash
        # the whole KB listing.
        try:
            store_dict = getattr(docstore, "dict", None)
            if store_dict is None:
                store_dict = getattr(docstore, "_dict", {})
            docs = list(store_dict.values())
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "list_documents: unexpected docstore shape for %r: %s",
                collection, exc,
            )
            return []
        # Trim to metadata fields the UI cares about.
        out: list[Document] = []
        for d in docs[:limit]:
            if hasattr(d, "page_content"):
                out.append(d)
        return out

    def as_retriever(self, collection: str = "documents", k: int = 3):
        store = self._get_or_load(collection)
        return store.as_retriever(search_kwargs={"k": k})

    # ----- CRUD / stats / filtering (PG-native, FAISS-degraded) -----
    def _is_pg_store(self, store) -> bool:
        """Duck-type: PGVectorStore exposes `filter_documents`; FAISS does not."""
        return hasattr(store, "filter_documents")

    def _faiss_docstore_docs(self, store) -> list[Document]:
        """Best-effort extraction of Documents from a FAISS docstore.

        Filters out the sentinel doc that ``build_vectorstore`` inserts
        (``metadata._init == True``) so empty collections report 0 docs.
        """
        docstore = getattr(store, "docstore", None)
        if docstore is None:
            return []
        try:
            store_dict = getattr(docstore, "dict", None)
            if store_dict is None:
                store_dict = getattr(docstore, "_dict", {})
            raw = list(store_dict.values())
        except Exception:  # noqa: BLE001
            return []
        out: list[Document] = []
        for d in raw:
            if not hasattr(d, "page_content"):
                continue
            if isinstance(d.metadata, dict) and d.metadata.get("_init"):
                continue
            out.append(d)
        return out

    def delete_document(self, doc_id: str, collection: str = "documents") -> bool:
        """Delete a single document by PG row id. Returns True if deleted.

        Raises NotImplementedError for FAISS backend (FAISS does not
        support per-document deletion without rebuilding the index).
        """
        store = self._get_or_load(collection)
        if self._is_pg_store(store):
            return bool(store.delete_document(doc_id))
        raise NotImplementedError(
            "FAISS backend does not support per-doc delete; "
            "use PG backend or delete the whole collection"
        )

    def delete_collection(self, collection: str) -> int:
        """Delete all documents in a collection. Returns count deleted.

        For PG: delegates to ``PGVectorStore.delete_collection``.
        For FAISS: drops the in-memory cache and removes the on-disk
        ``index.faiss`` / ``index.pkl`` files for the collection.
        """
        store = self._get_or_load(collection)
        if self._is_pg_store(store):
            n = int(store.delete_collection())
            self._stores.pop(collection, None)
            return n
        # FAISS path: count what we have, then wipe cache + disk files.
        docs = self._faiss_docstore_docs(store)
        n = len(docs)
        self._stores.pop(collection, None)
        try:
            from rag.vectorstore import _collection_path
            path = _collection_path(collection)
            if path.exists():
                for fname in ("index.faiss", "index.pkl"):
                    f = path / fname
                    if f.exists():
                        try:
                            f.unlink()
                        except OSError:
                            pass
                try:
                    path.rmdir()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "delete_collection: failed to remove FAISS files for %r",
                collection,
            )
        return n

    def count_documents(self, collection: str) -> int:
        """Return total document count in a collection."""
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return 0
        if self._is_pg_store(store):
            return int(store.count())
        return len(self._faiss_docstore_docs(store))

    def stats(self, collection: str) -> dict:
        """Return stats dict.

        Shape: ``{"total": N, "by_source": [...], "avg_chunk_size": float}``.
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return {"total": 0, "by_source": [], "avg_chunk_size": 0.0}
        if self._is_pg_store(store):
            return store.stats()
        docs = self._faiss_docstore_docs(store)
        by_src: dict[str, int] = {}
        total_len = 0
        for d in docs:
            src = ""
            if isinstance(d.metadata, dict):
                src = str(d.metadata.get("source") or "")
            by_src[src] = by_src.get(src, 0) + 1
            total_len += len(d.page_content)
        avg = (total_len / len(docs)) if docs else 0.0
        by_source = [
            {"source": k, "chunks": v}
            for k, v in sorted(by_src.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        return {
            "total": len(docs),
            "by_source": by_source,
            "avg_chunk_size": float(avg),
        }

    def filter_documents(
        self,
        collection: str,
        *,
        source: str | None = None,
        offset: int = 0,
        limit: int = 50,
        order: str = "desc",
    ) -> tuple[list[Document], int]:
        """Return ``(docs, total)`` with filtering + pagination.

        For PG: uses SQL WHERE/OFFSET/LIMIT/ORDER BY.
        For FAISS: loads all then filters/sorts/slices in Python with the
        same semantics (``order`` is a hint — FAISS has no ``created_at`` so
        we use docstore order, reversing for ``desc``).
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return [], 0
        if self._is_pg_store(store):
            return store.filter_documents(
                source=source, offset=offset, limit=limit, order=order
            )
        docs = self._faiss_docstore_docs(store)
        if source is not None:
            docs = [
                d for d in docs
                if isinstance(d.metadata, dict)
                and str(d.metadata.get("source") or "") == source
            ]
        total = len(docs)
        # FAISS has no created_at; docstore order is effectively insertion
        # order. ``desc`` (default) → newest-first → reverse; ``asc`` → keep.
        if order.lower() != "asc":
            docs = list(reversed(docs))
        if offset > 0:
            docs = docs[offset:]
        if limit is not None and limit >= 0:
            docs = docs[:limit]
        return docs, total

    def list_versions_by_source(self, collection: str, source: str) -> list[Document]:
        """Return all chunks of one source, ordered by chunk_index.

        For PG: uses SQL ORDER BY (metadata->>'chunk_index')::int.
        For FAISS: filters in Python.
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return []
        if self._is_pg_store(store) and hasattr(store, "list_versions_by_source"):
            return store.list_versions_by_source(source)
        docs = self._faiss_docstore_docs(store)
        filtered = [
            d for d in docs
            if isinstance(d.metadata, dict)
            and str(d.metadata.get("source") or "") == source
        ]

        def _ci(d: Document) -> int:
            if isinstance(d.metadata, dict):
                try:
                    return int(d.metadata.get("chunk_index") or 0)
                except (TypeError, ValueError):
                    return 0
            return 0

        return sorted(filtered, key=_ci)

    def source_exists(self, collection: str, source: str) -> bool:
        """True if at least one chunk with this metadata.source already exists."""
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return False
        if self._is_pg_store(store) and hasattr(store, "source_exists"):
            return bool(store.source_exists(source))
        for d in self._faiss_docstore_docs(store):
            if isinstance(d.metadata, dict) and str(d.metadata.get("source") or "") == source:
                return True
        return False

    # ----- metadata-level helpers (used by LongTermMemory) -----
    # These all degrade gracefully for FAISS: per-doc metadata update is
    # not supported there without rebuilding the index, so we return a
    # "not supported" sentinel (False / None / []) instead of raising.
    # Callers (the memory module) treat the sentinel as "no-op happened"
    # and document that behaviour in their docstrings.
    def search_with_scores(
        self,
        query: str,
        k: int = 3,
        collection: str = "documents",
    ) -> list[tuple[Document, float]]:
        """Return ``[(doc, similarity), ...]`` sorted by similarity desc.

        For PG: native cosine similarity in [0, 1].
        For FAISS: uses ``similarity_search_with_score`` (returns L2
        distance) and converts to similarity via ``1 / (1 + d)`` so the
        returned ordering direction matches the PG backend (higher = better).
        If the store lacks ``similarity_search_with_score`` entirely, falls
        back to plain ``similarity_search`` with a constant 0.5 score.
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return []
        # PG path
        if hasattr(store, "similarity_search_with_score"):
            # PGVectorStore returns cosine similarity directly. FAISS also
            # has this method but returns L2 distance — distinguish by
            # duck-typing on the PG-specific marker.
            if self._is_pg_store(store):
                return list(store.similarity_search_with_score(query, k=k))
            # FAISS path: convert L2 distance -> similarity proxy.
            try:
                pairs = store.similarity_search_with_score(query, k=k)
            except Exception:  # noqa: BLE001
                pairs = []
            return [
                (doc, 1.0 / (1.0 + float(dist)))
                for doc, dist in pairs
            ]
        # Fallback: no score method available.
        docs = store.similarity_search(query, k=k)
        return [(d, 0.5) for d in docs]

    def find_by_doc_id(
        self, collection: str, doc_id: str
    ) -> Document | None:
        """Find a single doc by ``metadata.doc_id``.

        For PG: indexed SQL lookup.
        For FAISS: linear scan of the in-memory docstore (slow but correct).
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return None
        if self._is_pg_store(store) and hasattr(store, "find_by_doc_id"):
            return store.find_by_doc_id(doc_id)
        for d in self._faiss_docstore_docs(store):
            if isinstance(d.metadata, dict) and str(d.metadata.get("doc_id") or "") == doc_id:
                return d
        return None

    def update_metadata_by_doc_id(
        self, collection: str, doc_id: str, updates: dict
    ) -> bool:
        """Merge ``updates`` into a doc's metadata. Returns True if updated.

        For PG: native JSONB ``||`` merge.
        For FAISS: NOT SUPPORTED (FAISS docstore is pickled on save;
        in-place mutation would silently vanish after reload). Returns False.
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return False
        if self._is_pg_store(store) and hasattr(store, "update_metadata_by_doc_id"):
            return bool(store.update_metadata_by_doc_id(doc_id, updates))
        return False

    def delete_by_doc_id(self, collection: str, doc_id: str) -> bool:
        """Delete a doc by ``metadata.doc_id`` (not the PG primary key).

        For PG: DELETE WHERE metadata->>'doc_id' = %s.
        For FAISS: NOT SUPPORTED (FAISS can't delete a single doc without
        rebuilding the index). Returns False.
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return False
        if self._is_pg_store(store) and hasattr(store, "delete_by_doc_id"):
            return bool(store.delete_by_doc_id(doc_id))
        return False

    def list_all_documents(
        self, collection: str, limit: int | None = None
    ) -> list[Document]:
        """Return all docs in a collection (no ordering guarantee).

        Used by ``LongTermMemory.stats`` and ``forget_expired`` to scan
        the whole memory set in one pass. The Indexer's existing
        ``list_documents`` is intended for the KB UI (caps at 200, sorts
        by recency); this method is the unfiltered full-scan counterpart.
        """
        try:
            store = self._get_or_load(collection)
        except Exception:  # noqa: BLE001
            return []
        if self._is_pg_store(store) and hasattr(store, "list_all_documents"):
            return store.list_all_documents(limit)
        docs = self._faiss_docstore_docs(store)
        if limit is not None:
            docs = docs[: int(limit)]
        return docs
