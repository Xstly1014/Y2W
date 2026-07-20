"""Migrate FAISS indices to the PostgreSQL vector backend (PGVectorStore).

Walks each subdirectory under ``--vector-dir`` (default ``data/vectorstore/``),
loads the FAISS index, and extracts ``(document, embedding)`` pairs directly
from the FAISS internal structures — **without** calling the OpenAI embedding
API again. The pairs are bulk-inserted into the PG ``vector_store`` table;
the FAISS directory name becomes the PG ``collection`` name.

The FAISS files are kept by default as a fallback. Pass ``--no-keep-faiss``
to delete them after a successful migration.

Run with::

    python -m scripts.migrate_faiss_to_pg --dry-run
    python -m scripts.migrate_faiss_to_pg
    python -m scripts.migrate_faiss_to_pg --collections docs kb_demo --overwrite
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import psycopg2
import psycopg2.extras
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from rag.pg_vectorstore import (
    PGVectorStore,
    _SCHEMA_SQL,
    ensure_agent_vectors_db,
)

logger = logging.getLogger("migrate_faiss_to_pg")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VECTOR_DIR = PROJECT_ROOT / "data" / "vectorstore"
BATCH_SIZE = 500  # rows per execute_values round-trip


# --------------------------------------------------------------------------- #
# Dummy embeddings — we never call the OpenAI API
# --------------------------------------------------------------------------- #
class _DummyEmbeddings(Embeddings):
    """Stub embeddings object.

    FAISS.load_local requires an ``Embeddings`` instance, but we never
    actually call ``embed_query`` / ``embed_documents`` — every vector is
    reconstructed straight from the FAISS index. Returning a zero vector
    keeps the constructor happy if anything pokes at it.
    """

    def embed_query(self, text: str) -> list[float]:
        return [0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


# --------------------------------------------------------------------------- #
# Result record
# --------------------------------------------------------------------------- #
@dataclass
class CollectionResult:
    collection: str
    docs: int = 0
    skipped: int = 0
    status: str = "OK"
    error: str = ""


# --------------------------------------------------------------------------- #
# FAISS extraction
# --------------------------------------------------------------------------- #
def _is_sentinel(doc: Document) -> bool:
    """True for the empty placeholder created on FAISS init (see rag/vectorstore.py)."""
    return bool(doc.metadata.get("_init"))


def _extract_faiss_records(
    folder: Path,
) -> tuple[list[tuple[str, dict, np.ndarray]], int]:
    """Load a FAISS index and return ``(records, n_sentinels_skipped)``.

    Each record is ``(content, metadata, embedding_ndarray)``. Embeddings are
    reconstructed straight from the FAISS index via ``index.reconstruct(idx)``
    — no embedding API call is made.
    """
    store = FAISS.load_local(
        folder_path=str(folder),
        embeddings=_DummyEmbeddings(),
        allow_dangerous_deserialization=True,
    )
    docstore_dict = store.docstore._dict  # {docstore_id: Document}
    index = store.index
    # index_to_docstore_id is Dict[int, str] (faiss_idx -> docstore_id)
    index_to_docstore_id = store.index_to_docstore_id

    records: list[tuple[str, dict, np.ndarray]] = []
    skipped = 0
    for faiss_idx, docstore_id in index_to_docstore_id.items():
        doc = docstore_dict.get(docstore_id)
        if doc is None:
            skipped += 1
            continue
        if _is_sentinel(doc):
            skipped += 1
            continue
        # Reconstruct embedding directly from the FAISS index.
        try:
            emb = index.reconstruct(int(faiss_idx))  # np.ndarray(dim,)
        except Exception:
            # Some non-flat indexes (IVFPQ etc.) need reconstruct_n.
            emb = index.reconstruct_n(int(faiss_idx), 1)[0]
        records.append(
            (
                doc.page_content,
                dict(doc.metadata or {}),
                np.asarray(emb, dtype=np.float32),
            )
        )
    return records, skipped


# --------------------------------------------------------------------------- #
# PG helpers
# --------------------------------------------------------------------------- #
def _connect_pg():
    """Open a psycopg2 connection to the agent_vectors DB.

    Reuses ``PGVectorStore._conn_from_settings`` so we share the exact same
    URL parsing logic as the production backend.
    """
    return PGVectorStore._conn_from_settings()


def _ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)
    conn.commit()


def _delete_collection(conn, collection: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM vector_store WHERE collection = %s", (collection,)
        )
        n = cur.rowcount
    conn.commit()
    return n


def _insert_batch(
    conn, collection: str, rows: list[tuple[str, dict, np.ndarray]]
) -> int:
    """Bulk-insert rows into ``vector_store``. Returns number inserted."""
    if not rows:
        return 0
    values = [
        (
            str(uuid.uuid4()),
            collection,
            content,
            json.dumps(meta, ensure_ascii=False),
            np.asarray(emb, dtype=np.float32).tobytes(),
        )
        for content, meta, emb in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO vector_store (id, collection, content, metadata, embedding)
            VALUES %s
            """,
            values,
            template="(%s, %s, %s, %s::jsonb, %s)",
            page_size=BATCH_SIZE,
        )
    conn.commit()
    return len(values)


# --------------------------------------------------------------------------- #
# Discovery + migration
# --------------------------------------------------------------------------- #
def _discover_collections(vector_dir: Path) -> list[str]:
    """Subdirectories that contain both ``index.faiss`` and ``index.pkl``."""
    if not vector_dir.exists():
        return []
    out: list[str] = []
    for sub in sorted(p for p in vector_dir.iterdir() if p.is_dir()):
        if (sub / "index.faiss").exists() and (sub / "index.pkl").exists():
            out.append(sub.name)
    return out


def _migrate_one(
    conn,
    collection: str,
    folder: Path,
    *,
    overwrite: bool,
    dry_run: bool,
) -> CollectionResult:
    result = CollectionResult(collection=collection)
    try:
        records, skipped = _extract_faiss_records(folder)
    except Exception as exc:  # noqa: BLE001
        result.status = "FAIL"
        result.error = f"FAISS load failed: {exc}"
        return result

    result.skipped = skipped
    result.docs = len(records)

    if dry_run:
        print(
            f"  [dry-run] collection={collection}: "
            f"would migrate {len(records)} doc(s), skip {skipped} sentinel(s)"
        )
        return result

    if overwrite:
        try:
            deleted = _delete_collection(conn, collection)
            if deleted:
                print(
                    f"  [overwrite] deleted {deleted} old row(s) "
                    f"for collection={collection}"
                )
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            result.status = "FAIL"
            result.error = f"DELETE failed: {exc}"
            return result

    try:
        inserted = _insert_batch(conn, collection, records)
        result.docs = inserted
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        result.status = "FAIL"
        result.error = f"INSERT failed: {exc}"
        return result

    print(
        f"  [collection={collection}] migrated {inserted} docs, "
        f"skipped {skipped}"
    )
    return result


def _print_summary(results: list[CollectionResult]) -> None:
    print()
    print("Migration summary:")
    header = (
        f"  {'collection':<20} {'docs':>6} {'skipped':>8} "
        f"{'status':<8} {'error'}"
    )
    print(header)
    print(
        f"  {'-'*20} {'-'*6} {'-'*8} {'-'*8} {'-'*30}"
    )
    total_docs = 0
    total_skipped = 0
    for r in results:
        print(
            f"  {r.collection:<20} {r.docs:>6} {r.skipped:>8} "
            f"{r.status:<8} {r.error}"
        )
        total_docs += r.docs
        total_skipped += r.skipped
    print(f"  {'TOTAL':<20} {total_docs:>6} {total_skipped:>8}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate FAISS indices to the PG vector_store table.",
    )
    p.add_argument(
        "--collections",
        nargs="+",
        default=None,
        help="Specific collection names to migrate (default: auto-discover).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated without writing to PG.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="DELETE existing rows for each collection before inserting.",
    )
    p.add_argument(
        "--vector-dir",
        type=Path,
        default=DEFAULT_VECTOR_DIR,
        help=f"FAISS root directory (default: {DEFAULT_VECTOR_DIR}).",
    )
    p.add_argument(
        "--keep-faiss",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep FAISS files after migration (default). "
        "Use --no-keep-faiss to delete them on success.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    vector_dir: Path = args.vector_dir

    if args.collections:
        collections = list(args.collections)
    else:
        collections = _discover_collections(vector_dir)

    if not collections:
        print(f"No FAISS collections found under {vector_dir}")
        return 0

    print(f"Vector dir : {vector_dir}")
    print(f"Collections: {collections}")
    print(f"Dry run    : {args.dry_run}")
    print(f"Overwrite  : {args.overwrite}")
    print(f"Keep FAISS : {args.keep_faiss}")
    print()

    # Dry-run path: do not touch PG at all.
    if args.dry_run:
        results: list[CollectionResult] = []
        for name in collections:
            folder = vector_dir / name
            if not folder.exists():
                print(
                    f"  [skip] collection={name}: folder not found ({folder})"
                )
                results.append(
                    CollectionResult(
                        collection=name,
                        status="FAIL",
                        error="folder not found",
                    )
                )
                continue
            r = _migrate_one(
                conn=None,
                collection=name,
                folder=folder,
                overwrite=args.overwrite,
                dry_run=True,
            )
            results.append(r)
        _print_summary(results)
        return 0

    # Real run: ensure DB + schema, then migrate.
    print("Ensuring agent_vectors database exists...")
    if not ensure_agent_vectors_db():
        print(
            "ERROR: could not connect to PostgreSQL or create the agent_vectors DB.\n"
            "       Is PostgreSQL running on 127.0.0.1:5432?\n"
            "       Check PG_VECTOR_DATABASE_URL in your .env."
        )
        return 2

    try:
        conn = _connect_pg()
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: failed to connect to PG ({exc}).\n"
            "       Verify PostgreSQL is running and the agent_vectors DB exists."
        )
        return 2

    try:
        _ensure_schema(conn)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to ensure schema: {exc}")
        conn.close()
        return 2

    results: list[CollectionResult] = []
    for name in collections:
        folder = vector_dir / name
        if not folder.exists():
            print(f"  [skip] collection={name}: folder not found ({folder})")
            results.append(
                CollectionResult(
                    collection=name,
                    status="FAIL",
                    error="folder not found",
                )
            )
            continue
        r = _migrate_one(
            conn=conn,
            collection=name,
            folder=folder,
            overwrite=args.overwrite,
            dry_run=False,
        )
        results.append(r)

    _print_summary(results)

    # Optionally delete FAISS files (default: keep as fallback).
    if args.keep_faiss:
        print("\nFAISS files retained as fallback (use --no-keep-faiss to delete).")
    else:
        print("\n--no-keep-faiss: removing FAISS files for migrated collections...")
        for r in results:
            if r.status != "OK":
                continue
            folder = vector_dir / r.collection
            for fname in ("index.faiss", "index.pkl"):
                f = folder / fname
                if f.exists():
                    try:
                        f.unlink()
                        print(f"  deleted {f}")
                    except OSError as exc:  # noqa: BLE001
                        print(f"  WARN: could not delete {f}: {exc}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
