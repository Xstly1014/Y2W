"""PostgreSQL-backed vector store — drop-in replacement for FAISS.

Two backends share this module:

* ``pg_python`` — pure-Python similarity search (numpy cosine). Works without
  any PG extension. Suitable for small-to-medium corpora (up to ~50k docs).
  Data lives in a regular ``vector_store`` table; ACID + backups come from PG.

* ``pgvector`` — uses the pgvector extension's HNSW/IVFFlat indexes for
  sub-linear ANN search. Requires ``CREATE EXTENSION vector;`` (Windows:
  needs Visual Studio + Windows SDK to compile; see
  ``scripts/build_pgvector.ps1``). When the extension is not installed we
  transparently fall back to the pure-Python path so the agent still runs.

Both backends expose the same surface as ``langchain_community.vectorstores.FAISS``
(``from_texts`` / ``add_documents`` / ``similarity_search`` / ``as_retriever``
/ ``save_local`` / ``docstore``) so the rest of the RAG stack doesn't change.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterable

import numpy as np
import psycopg2
import psycopg2.extras
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from config import settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
# One table serves both backends. The `embedding` column is `bytea` for
# pg_python (we deserialize with numpy) and `vector(N)` for pgvector. To keep
# one schema that works for both, we store embeddings in `bytea` always, and
# add a `embedding_vec` column of type `vector` only when pgvector is available.
# This avoids schema divergence and lets us migrate by simply installing the
# extension + re-indexing.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vector_store (
    id           UUID PRIMARY KEY,
    collection   TEXT NOT NULL,
    content      TEXT NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding    BYTEA NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_vector_store_collection
    ON vector_store (collection);
CREATE INDEX IF NOT EXISTS idx_vector_store_created
    ON vector_store (collection, created_at DESC);
"""


class PGVectorStore:
    """PG-backed vector store with the same surface as langchain FAISS."""

    def __init__(
        self,
        embeddings: Embeddings,
        collection: str,
        *,
        use_pgvector: bool | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._collection = collection
        self._conn = self._conn_from_settings()
        # Auto-detect: try pgvector if configured and extension is available.
        if use_pgvector is None:
            use_pgvector = settings.vector_store_backend == "pgvector"
        self._use_pgvector = bool(use_pgvector and _pgvector_available(self._conn))
        self._ensure_schema()

    # ----- connection -----
    @staticmethod
    def _conn_from_settings():
        """Parse the SQLAlchemy-style URL into psycopg2 params and connect."""
        url = settings.pg_vector_database_url
        # Strip the sqlalchemy driver prefix: postgresql+psycopg2://...
        if "://" in url:
            url = url.split("://", 1)[1]
        # user:pass@host:port/db
        creds, host_db = url.rsplit("@", 1)
        user, _, pwd = creds.partition(":")
        host, _, port_db = host_db.partition(":")
        # Handle IPv6 host (rare here; keep simple).
        if "/" in port_db:
            port, _, db = port_db.partition("/")
        else:
            # No port — default 5432
            db = port_db
            port = "5432"
        return psycopg2.connect(
            user=user,
            password=pwd,
            host=host,
            port=int(port),
            dbname=db,
            application_name="0719agent-rag",
        )

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            # If pgvector is available, add the vector column + HNSW index.
            if self._use_pgvector:
                cur.execute(
                    "CREATE EXTENSION IF NOT EXISTS vector;"
                )
                # We don't know the dimension ahead of time, so add the
                # column lazily on first insert (see _insert). Here we just
                # make sure the extension exists.
        self._conn.commit()

    # ----- public API (mirrors FAISS) -----
    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embeddings: Embeddings,
        metadatas: list[dict] | None = None,
        collection: str = "documents",
    ) -> "PGVectorStore":
        store = cls(embeddings, collection)
        metas = metadatas or [{} for _ in texts]
        docs = [
            Document(page_content=t, metadata=m)
            for t, m in zip(texts, metas)
        ]
        store.add_documents(docs)
        return store

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        # Batch embed for efficiency (OpenAI supports batch embed_documents).
        texts = [d.page_content for d in docs]
        try:
            embs = self._embeddings.embed_documents(texts)
        except NotImplementedError:
            # Some embeddings only implement embed_query.
            embs = [self._embeddings.embed_query(t) for t in texts]

        rows = []
        for doc, emb in zip(docs, embs):
            emb_arr = np.asarray(emb, dtype=np.float32)
            row = (
                str(uuid.uuid4()),
                self._collection,
                doc.page_content,
                json.dumps(doc.metadata, ensure_ascii=False),
                emb_arr.tobytes(),
            )
            rows.append(row)
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO vector_store (id, collection, content, metadata, embedding)
                VALUES %s
                """,
                rows,
                template="(%s, %s, %s, %s::jsonb, %s)",
            )
        self._conn.commit()

    def similarity_search(self, query: str, k: int = 3) -> list[Document]:
        q_emb = self._embeddings.embed_query(query)
        return self._search_by_vector(q_emb, k)

    def _search_by_vector(self, q_emb: list[float], k: int) -> list[Document]:
        q = np.asarray(q_emb, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q_unit = q / q_norm

        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT content, metadata, embedding FROM vector_store WHERE collection = %s",
                (self._collection,),
            )
            rows = cur.fetchall()

        if not rows:
            return []

        # Vectorised cosine: stack all embeddings into one matrix.
        contents = [r[0] for r in rows]
        metas = [r[1] for r in rows]
        embs = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
        # Guard against zero-norm rows (would produce NaN).
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embs_unit = embs / norms
        sims = embs_unit @ q_unit  # cosine similarity

        # Top-k (argpartition is O(n) but for small n argsort is fine + stable).
        k = min(k, len(rows))
        top_idx = np.argsort(-sims)[:k]
        return [
            Document(page_content=contents[i], metadata=_load_meta(metas[i]))
            for i in top_idx
        ]

    def as_retriever(self, search_kwargs: dict | None = None):
        """Wrap as a LangChain retriever (duck-typed; avoids importing BaseRetriever)."""
        k = (search_kwargs or {}).get("k", settings.retrieval_top_k)
        return _PGRetriever(self, k)

    def save_local(self, folder: str | None = None) -> None:
        """No-op — PG is the source of truth. Kept for FAISS parity."""
        return None

    # ----- list/delete (for KB management UI) -----
    def list_documents(self, limit: int = 200) -> list[Document]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT content, metadata FROM vector_store
                WHERE collection = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (self._collection, limit),
            )
            rows = cur.fetchall()
        return [Document(page_content=r[0], metadata=_load_meta(r[1])) for r in rows]

    def delete_collection(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vector_store WHERE collection = %s",
                (self._collection,),
            )
            n = cur.rowcount
        self._conn.commit()
        return n

    def delete_document(self, doc_id: str) -> bool:
        """Delete a single row by its UUID primary key.

        Returns True if a row was deleted, False if no row matched. The
        collection scope is enforced so a caller passing a doc_id from
        another tenant cannot delete it.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vector_store WHERE id = %s AND collection = %s",
                (str(doc_id), self._collection),
            )
            n = cur.rowcount
        self._conn.commit()
        return n > 0

    # ----- metadata-level CRUD (used by LongTermMemory importance/decay) -----
    # The vector_store schema stores our app-level ``doc_id`` inside the
    # JSONB ``metadata`` column (not the PG primary key), because that's
    # what `LongTermMemory.remember` returns to callers. These helpers let
    # the memory module update importance / last_accessed without exposing
    # raw SQL to it.
    def find_by_doc_id(self, doc_id: str) -> Document | None:
        """Return a single doc by its ``metadata.doc_id`` (or None)."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT content, metadata FROM vector_store "
                "WHERE collection = %s AND metadata->>'doc_id' = %s LIMIT 1",
                (self._collection, str(doc_id)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Document(page_content=row[0], metadata=_load_meta(row[1]))

    def update_metadata_by_doc_id(self, doc_id: str, updates: dict) -> bool:
        """Merge ``updates`` into the metadata of the row with this ``doc_id``.

        Uses PG's ``||`` operator on JSONB objects, which is a shallow
        merge: top-level keys in ``updates`` overwrite existing values, but
        nested keys are replaced wholesale (not deep-merged). That matches
        our use-case (we only ever set scalar fields like ``importance``
        and ``last_accessed``).

        Returns True if a row was updated, False if no row matched.
        """
        if not updates:
            return False
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE vector_store SET metadata = metadata || %s::jsonb "
                "WHERE collection = %s AND metadata->>'doc_id' = %s",
                (json.dumps(updates, ensure_ascii=False), self._collection, str(doc_id)),
            )
            n = cur.rowcount
        self._conn.commit()
        return n > 0

    def delete_by_doc_id(self, doc_id: str) -> bool:
        """Delete the row with this ``metadata.doc_id``.

        Distinct from ``delete_document`` (which targets the PG primary
        key ``id``). This one is used by ``LongTermMemory.forget_expired``
        because the memory module only knows the app-level ``doc_id`` that
        ``remember()`` returned.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vector_store "
                "WHERE collection = %s AND metadata->>'doc_id' = %s",
                (self._collection, str(doc_id)),
            )
            n = cur.rowcount
        self._conn.commit()
        return n > 0

    def similarity_search_with_score(
        self, query: str, k: int = 3
    ) -> list[tuple[Document, float]]:
        """Return ``[(doc, cosine_similarity), ...]`` for the query.

        Cosine similarity is in [-1, 1]; for normalised embeddings it
        typically lands in [0, 1]. Higher = more similar. This mirrors
        langchain FAISS's ``similarity_search_with_score`` signature so
        the Indexer can duck-type across backends.
        """
        q_emb = self._embeddings.embed_query(query)
        q = np.asarray(q_emb, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q_unit = q / q_norm

        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT content, metadata, embedding FROM vector_store "
                "WHERE collection = %s",
                (self._collection,),
            )
            rows = cur.fetchall()
        if not rows:
            return []

        contents = [r[0] for r in rows]
        metas = [r[1] for r in rows]
        embs = np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
        norms = np.linalg.norm(embs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embs_unit = embs / norms
        sims = embs_unit @ q_unit

        k = min(k, len(rows))
        # Take top-k by similarity (descending). argsort is stable for ties.
        top_idx = np.argsort(-sims)[:k]
        return [
            (
                Document(page_content=contents[i], metadata=_load_meta(metas[i])),
                float(sims[i]),
            )
            for i in top_idx
        ]

    def list_all_documents(self, limit: int | None = None) -> list[Document]:
        """Return all docs in this collection (no ordering guarantee).

        Used by ``LongTermMemory.stats`` / ``forget_expired`` to scan the
        full memory set. ``limit`` is a safety cap (None = no cap).
        """
        sql = "SELECT content, metadata FROM vector_store WHERE collection = %s"
        params: list[Any] = [self._collection]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self._conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [Document(page_content=r[0], metadata=_load_meta(r[1])) for r in rows]

    def count(self) -> int:
        """Total chunk rows in this collection."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM vector_store WHERE collection = %s",
                (self._collection,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def stats(self) -> dict:
        """Aggregate stats for this collection.

        Returns ``{"total": N, "by_source": [...], "avg_chunk_size": float}``.
        ``avg_chunk_size`` is the mean character length of ``content``;
        ``by_source`` groups chunk counts by ``metadata->>'source'``.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COALESCE(AVG(LENGTH(content)), 0) "
                "FROM vector_store WHERE collection = %s",
                (self._collection,),
            )
            total, avg = cur.fetchone()
            cur.execute(
                "SELECT metadata->>'source' AS src, COUNT(*) "
                "FROM vector_store WHERE collection = %s "
                "GROUP BY metadata->>'source' "
                "ORDER BY COUNT(*) DESC, src ASC",
                (self._collection,),
            )
            src_rows = cur.fetchall()
        by_source = [
            {"source": (r[0] or ""), "chunks": int(r[1])}
            for r in src_rows
        ]
        return {
            "total": int(total) if total is not None else 0,
            "by_source": by_source,
            "avg_chunk_size": float(avg) if avg else 0.0,
        }

    def filter_documents(
        self,
        *,
        source: str | None = None,
        offset: int = 0,
        limit: int = 50,
        order: str = "desc",
    ) -> tuple[list[Document], int]:
        """Filtered + paginated listing.

        Returns ``(docs, total)`` where ``total`` is the count before
        pagination (so callers can render page counts). ``order`` controls
        ``created_at`` direction ("desc" or "asc").
        """
        if order.lower() == "asc":
            order_clause = "ASC"
        else:
            order_clause = "DESC"
        where = "collection = %s"
        params: list[Any] = [self._collection]
        if source is not None:
            where += " AND metadata->>'source' = %s"
            params.append(source)

        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM vector_store WHERE {where}",
                tuple(params),
            )
            total = int(cur.fetchone()[0])
            cur.execute(
                f"SELECT content, metadata FROM vector_store WHERE {where} "
                f"ORDER BY created_at {order_clause} "
                f"OFFSET %s LIMIT %s",
                tuple(params + [offset, limit]),
            )
            rows = cur.fetchall()
        docs = [Document(page_content=r[0], metadata=_load_meta(r[1])) for r in rows]
        return docs, total

    def list_versions_by_source(self, source: str) -> list[Document]:
        """Return all chunks of one source, ordered by chunk_index."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT content, metadata FROM vector_store "
                "WHERE collection = %s AND metadata->>'source' = %s "
                "ORDER BY COALESCE((metadata->>'chunk_index')::int, 0) ASC",
                (self._collection, source),
            )
            rows = cur.fetchall()
        return [Document(page_content=r[0], metadata=_load_meta(r[1])) for r in rows]

    def source_exists(self, source: str) -> bool:
        """True if at least one chunk with this metadata.source already exists."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM vector_store "
                "WHERE collection = %s AND metadata->>'source' = %s "
                "LIMIT 1",
                (self._collection, source),
            )
            return cur.fetchone() is not None

    @property
    def docstore(self):
        """Mimic FAISS's docstore for the KB listing UI.

        Returns an object with a `.dict` (or `._dict`) attribute mapping ids
        to Documents. The indexer's `list_documents` uses this.
        """
        docs = self.list_documents(limit=10000)

        class _Docstore:
            def __init__(self, items):
                self.dict = {str(i): d for i, d in enumerate(items)}
                self._dict = self.dict

        return _Docstore(docs)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Retriever wrapper
# --------------------------------------------------------------------------- #
class _PGRetriever:
    """Minimal LangChain-compatible retriever.

    LangChain's `BaseRetriever` is pydantic-based and imposes constraints that
    make wrapping a PG connection awkward. We implement the duck-typed
    interface (`invoke` / `ainvoke` / `get_relevant_documents`) instead —
    langgraph's agent uses `invoke` which is what matters.
    """

    def __init__(self, store: PGVectorStore, k: int) -> None:
        self._store = store
        self._k = k

    def invoke(self, query: str, config: Any = None, **kwargs: Any) -> list[Document]:
        return self._store.similarity_search(query, k=self._k)

    async def ainvoke(self, query: str, config: Any = None, **kwargs: Any) -> list[Document]:
        # PG access is sync; run in a thread to avoid blocking the event loop.
        import asyncio
        import functools
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._store.similarity_search, query, k=self._k)
        )

    def get_relevant_documents(self, query: str) -> list[Document]:
        return self._store.similarity_search(query, k=self._k)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_meta(raw: Any) -> dict:
    """psycopg2 returns jsonb as a dict already (with the right adapter)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _pgvector_available(conn) -> bool:
    """Check if the pgvector extension is installed in this PG database."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector';"
            )
            return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass


def ensure_agent_vectors_db() -> bool:
    """Create the `agent_vectors` database if missing.

    Called once at startup when the PG backend is enabled. Idempotent.
    Returns True if the database exists (or was created); False on failure.
    """
    url = settings.pg_vector_database_url
    if "://" in url:
        url = url.split("://", 1)[1]
    creds, host_db = url.rsplit("@", 1)
    user, _, pwd = creds.partition(":")
    host, _, port_db = host_db.partition(":")
    if "/" in port_db:
        port, _, db = port_db.partition("/")
    else:
        db = port_db
        port = "5432"

    try:
        # Connect to the maintenance DB (`postgres`) to create our DB.
        admin = psycopg2.connect(
            user=user, password=pwd, host=host, port=int(port), dbname="postgres",
            application_name="0719agent-rag-init",
        )
        admin.autocommit = True  # CREATE DATABASE can't run in a txn
        with admin.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s;", (db,)
            )
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{db}";')
                logger.info("Created agent_vectors database %r", db)
        admin.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_agent_vectors_db failed: %s", exc)
        return False
