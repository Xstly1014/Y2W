"""Vector store factory — picks the backend configured in settings.

Three backends:
  * ``faiss``     — original FAISS index files on disk (default, zero infra)
  * ``pg_python`` — PostgreSQL table + numpy cosine similarity
  * ``pgvector``  — PostgreSQL + pgvector extension (HNSW/IVFFlat indexes)

All three expose the same surface so the Indexer / retriever / rag_tool don't
need to know which is active. See ``config.settings.vector_store_backend``.
"""
from __future__ import annotations

from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings

from config import settings


def _collection_path(collection: str) -> Path:
    return Path(settings.vector_store_dir) / collection


def _is_pg_backend() -> bool:
    return settings.vector_store_backend in ("pg_python", "pgvector")


def build_vectorstore(
    embeddings: Embeddings,
    collection: str,
):
    """Create a new empty vector store for `collection`.

    For FAISS this returns an in-memory index with a sentinel doc (matches
    the original behaviour). For PG backends it returns a PGVectorStore
    bound to `collection` (the table is created lazily on first insert).
    """
    if _is_pg_backend():
        # Lazy import so projects that never enable the PG backend don't
        # need psycopg2 installed.
        from rag.pg_vectorstore import PGVectorStore, ensure_agent_vectors_db

        ensure_agent_vectors_db()
        return PGVectorStore(embeddings, collection)
    return FAISS.from_texts(
        texts=[""], embedding=embeddings, metadatas=[{"_init": True}]
    )


def load_vectorstore(
    embeddings: Embeddings,
    collection: str,
):
    """Load an existing persisted store, or None if not present.

    For FAISS: checks the on-disk index directory.
    For PG: always returns a PGVectorStore (the table always "exists"; an
    empty collection just returns no results). We still return None when
    the backend is FAISS and the directory is missing, so the indexer can
    decide whether to bootstrap a fresh index.
    """
    if _is_pg_backend():
        from rag.pg_vectorstore import PGVectorStore, ensure_agent_vectors_db

        ensure_agent_vectors_db()
        return PGVectorStore(embeddings, collection)
    path = _collection_path(collection)
    if not path.exists():
        return None
    return FAISS.load_local(
        folder_path=str(path),
        embeddings=embeddings,
        allow_dangerous_deserialization=True,
    )


def save_vectorstore(store, collection: str) -> None:
    """Persist a store. No-op for PG backends (writes are immediate)."""
    # PGVectorStore has save_local as a no-op, so calling it is safe for both.
    if hasattr(store, "save_local"):
        # FAISS.save_local(folder) writes index.faiss + index.pkl.
        # PGVectorStore.save_local ignores its argument.
        if _is_pg_backend():
            store.save_local(None)
        else:
            path = _collection_path(collection)
            path.mkdir(parents=True, exist_ok=True)
            store.save_local(folder_path=str(path))
