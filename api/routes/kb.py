"""Knowledge-base routes: upload documents, search the index, list collections."""
from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile

from api.deps import get_indexer, tenant_collection
from api.schemas import (
    CollectionDeleteResponse,
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentVersionsResponse,
    IncrementalUploadResponse,
    IngestResponse,
    KBStatsResponse,
    validate_safe_id,
)
from config import settings
from rag.ingest import ingest_file
from skills.commerce import current_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


def _tenant(x_tenant_id: str | None) -> str:
    """Resolve and validate the tenant id from the request header.

    The tenant id flows into a FAISS collection name and from there into
    a filesystem path (``<vector_store_dir>/<prefix>_<tenant_id>``). A
    crafted header like ``X-Tenant-Id: ../../etc`` would escape the
    vector store directory — validate_safe_id rejects it.
    """
    tenant_id = x_tenant_id or settings.default_tenant_id
    try:
        return validate_safe_id(tenant_id, "tenant_id")  # type: ignore[return-value]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload", response_model=list[IngestResponse])
async def upload_documents(
    files: list[UploadFile] = File(...),
    x_tenant_id: str | None = Header(default=None),
) -> list[IngestResponse]:
    """Upload one or more .txt/.md files into the tenant's KB collection."""
    tenant_id = _tenant(x_tenant_id)
    collection = f"{settings.kb_collection_prefix}_{tenant_id}"
    indexer = get_indexer()
    results: list[IngestResponse] = []

    # Persist uploads to a temp dir, then route through ingest_file so the
    # same code path serves the CLI and the API.
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for upload in files:
            if not upload.filename:
                continue
            # Defend against path traversal: only keep the basename, never the
            # user-supplied path (an attacker could send "../../etc/passwd").
            safe_name = Path(upload.filename).name
            if not safe_name:
                results.append(IngestResponse(path=upload.filename, chunks=0))
                continue
            suffix = Path(safe_name).suffix.lower()
            if suffix not in {".txt", ".md"}:
                results.append(IngestResponse(path=safe_name, chunks=0))
                continue
            dest = tmpdir / safe_name
            content = await upload.read()
            dest.write_bytes(content)
            try:
                # Set tenant context so any (future) tenant-aware logic works.
                token = current_tenant_id.set(tenant_id)
                try:
                    n = ingest_file(dest, indexer, collection=collection)
                finally:
                    current_tenant_id.reset(token)
                results.append(IngestResponse(path=safe_name, chunks=n))
            except Exception as exc:  # noqa: BLE001
                logger.exception("ingest failed for %s", safe_name)
                raise HTTPException(status_code=500, detail=f"ingest failed: {exc}")
    return results


@router.get("/search")
def search(
    q: str,
    k: int = 3,
    x_tenant_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Debug endpoint: run a similarity search against the tenant's KB."""
    tenant_id = _tenant(x_tenant_id)
    collection = f"{settings.kb_collection_prefix}_{tenant_id}"
    indexer = get_indexer()
    docs = indexer.search(q, k=k, collection=collection)
    return {
        "tenant_id": tenant_id,
        "collection": collection,
        "query": q,
        "hits": [
            {
                "content": d.page_content[:300],
                "metadata": d.metadata,
            }
            for d in docs
        ],
    }


@router.get("/documents")
def list_documents(
    x_tenant_id: str | None = Header(default=None),
    limit: int = 200,
) -> dict[str, Any]:
    """List all chunks currently stored in the tenant's KB collection."""
    tenant_id = _tenant(x_tenant_id)
    collection = f"{settings.kb_collection_prefix}_{tenant_id}"
    indexer = get_indexer()
    docs = indexer.list_documents(collection=collection, limit=limit)
    return {
        "tenant_id": tenant_id,
        "collection": collection,
        "count": len(docs),
        "documents": [
            {
                "content": d.page_content[:200],
                "metadata": d.metadata,
            }
            for d in docs
        ],
    }


@router.post("/ingest-samples")
def ingest_samples(x_tenant_id: str | None = Header(default=None)) -> dict[str, Any]:
    """One-click ingest of the bundled samples/ directory into the tenant's KB.

    Used by the demo script and the dashboard "load sample data" button.
    """
    from rag.ingest import ingest_paths

    tenant_id = _tenant(x_tenant_id)
    collection = f"{settings.kb_collection_prefix}_{tenant_id}"
    indexer = get_indexer()
    samples_dir = Path(settings.vector_store_dir).parent.parent / "samples"
    if not samples_dir.exists():
        raise HTTPException(status_code=404, detail=f"samples dir not found: {samples_dir}")
    results = ingest_paths([samples_dir], indexer, collection=collection)
    return {
        "tenant_id": tenant_id,
        "collection": collection,
        "results": results,
        "total_chunks": sum(results.values()),
    }


# --------------------------------------------------------------------------- #
# New CRUD / versioning / stats endpoints (task 1.1 - 1.6).
#
# The original 4 endpoints above (upload / search / documents / ingest-samples)
# are intentionally left untouched per the task constraints. The enhanced
# `/documents` listing below is registered as a separate route — FastAPI
# dispatches to the first matching route, so the original still handles
# runtime requests; this enhanced declaration documents the new query params
# and response shape and is enabled by removing/reordering the original.
# --------------------------------------------------------------------------- #
def _tenant_collection(tenant_id: str) -> str:
    """Build the tenant-scoped collection name."""
    return tenant_collection(tenant_id)


def _validate_uuid(value: str, field_name: str = "doc_id") -> str:
    """Return ``value`` if it parses as a UUID, else raise 400."""
    try:
        uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be a valid UUID",
        ) from exc
    return value


def _basename(path_str: str) -> str:
    """Basename of a stored ``metadata.source`` path (handles / and \\)."""
    if not path_str:
        return ""
    # Normalize backslashes (Windows) so Path.name works on either separator.
    return Path(path_str.replace("\\", "/")).name


@router.delete(
    "/documents/{doc_id}",
    response_model=DocumentDeleteResponse,
)
def delete_document(
    doc_id: str,
    x_tenant_id: str | None = Header(default=None),
) -> DocumentDeleteResponse:
    """Delete a single chunk by its PG row UUID.

    FAISS backend does not support per-document deletion; returns 501.
    """
    _validate_uuid(doc_id, "doc_id")
    tenant_id = _tenant(x_tenant_id)
    collection = _tenant_collection(tenant_id)
    indexer = get_indexer()
    try:
        ok = indexer.delete_document(doc_id, collection=collection)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "FAISS backend does not support per-doc delete; "
                "use PG backend or delete the whole collection"
            ),
        ) from exc
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"doc_id {doc_id} not found in collection {collection}",
        )
    return DocumentDeleteResponse(deleted=True, doc_id=doc_id, collection=collection)


@router.delete(
    "/collections/{collection}",
    response_model=CollectionDeleteResponse,
)
def delete_collection(
    collection: str,
    x_tenant_id: str | None = Header(default=None),
) -> CollectionDeleteResponse:
    """Clear all documents in a collection.

    The collection name must (a) pass ``validate_safe_id`` and (b) match the
    caller's tenant-derived collection name — preventing cross-tenant
    deletion via a crafted URL.
    """
    try:
        safe = validate_safe_id(collection, "collection")  # type: ignore[return-value]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if safe is None:
        raise HTTPException(status_code=400, detail="collection is required")

    tenant_id = _tenant(x_tenant_id)
    expected = _tenant_collection(tenant_id)
    if collection != expected:
        # Don't echo the expected name back — minimises info leakage.
        raise HTTPException(
            status_code=403,
            detail="collection does not belong to this tenant",
        )

    indexer = get_indexer()
    try:
        n = indexer.delete_collection(collection)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail=str(exc),
        ) from exc
    return CollectionDeleteResponse(deleted_count=int(n), collection=collection)


@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="Enhanced document listing (pagination + source filter)",
)
def list_documents_paged(
    x_tenant_id: str | None = Header(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=1000),
    source: str | None = Query(default=None),
    order: str = Query(default="desc", pattern="^(desc|asc)$"),
) -> DocumentListResponse:
    """Enhanced listing: ``offset``/``limit``/``source``/``order`` query params.

    Returns ``total`` (precise for PG, ``len(docs)`` for FAISS), ``offset``,
    ``limit`` and the page of documents. Note: the original ``GET /documents``
    endpoint above remains registered and handles runtime requests by default;
    this declaration provides the enhanced surface and response model.
    """
    tenant_id = _tenant(x_tenant_id)
    collection = _tenant_collection(tenant_id)
    indexer = get_indexer()
    docs, total = indexer.filter_documents(
        collection,
        source=source,
        offset=offset,
        limit=limit,
        order=order,
    )
    return DocumentListResponse(
        tenant_id=tenant_id,
        collection=collection,
        total=int(total),
        offset=offset,
        limit=limit,
        documents=[
            {"content": d.page_content[:200], "metadata": d.metadata}
            for d in docs
        ],
    )


@router.get(
    "/documents/{doc_id}/versions",
    response_model=DocumentVersionsResponse,
)
def document_versions(
    doc_id: str,
    x_tenant_id: str | None = Header(default=None),
) -> DocumentVersionsResponse:
    """Return all chunks of one source, ordered by ``chunk_index``.

    ``doc_id`` here is the file's ``metadata.source`` (or its basename) —
    not the PG row id. We match first by exact ``metadata.source`` and fall
    back to basename comparison so callers can pass either form.
    """
    tenant_id = _tenant(x_tenant_id)
    collection = _tenant_collection(tenant_id)
    indexer = get_indexer()

    # Try exact source match first (PG path is SQL-indexed).
    docs = indexer.list_versions_by_source(collection, doc_id)
    if not docs:
        # Fall back to basename match: load all, filter Path(source).name == doc_id.
        all_docs, _ = indexer.filter_documents(
            collection, offset=0, limit=100000, order="asc"
        )
        docs = [
            d for d in all_docs
            if isinstance(d.metadata, dict)
            and _basename(str(d.metadata.get("source") or "")) == doc_id
        ]

        def _ci(d: Any) -> int:
            if isinstance(d.metadata, dict):
                try:
                    return int(d.metadata.get("chunk_index") or 0)
                except (TypeError, ValueError):
                    return 0
            return 0

        docs = sorted(docs, key=_ci)

    chunks = [
        {
            "chunk_index": int(d.metadata.get("chunk_index", 0)) if isinstance(d.metadata, dict) else 0,
            "content": d.page_content,
            "metadata": d.metadata,
        }
        for d in docs
    ]
    return DocumentVersionsResponse(
        source=doc_id,
        total_chunks=len(chunks),
        chunks=chunks,
    )


@router.get("/stats", response_model=KBStatsResponse)
def kb_stats(
    x_tenant_id: str | None = Header(default=None),
) -> KBStatsResponse:
    """Aggregate stats for the tenant's KB collection."""
    tenant_id = _tenant(x_tenant_id)
    collection = _tenant_collection(tenant_id)
    indexer = get_indexer()
    s = indexer.stats(collection)
    return KBStatsResponse(
        tenant_id=tenant_id,
        collection=collection,
        total_docs=int(s.get("total", 0)),
        total_chunks=int(s.get("total", 0)),
        by_source=s.get("by_source", []),
        backend=settings.vector_store_backend,
        avg_chunk_size=float(s.get("avg_chunk_size", 0.0)),
    )


@router.post(
    "/upload-incremental",
    response_model=IncrementalUploadResponse,
)
async def upload_incremental(
    files: list[UploadFile] = File(...),
    x_tenant_id: str | None = Header(default=None),
) -> IncrementalUploadResponse:
    """Upload files, skipping any whose filename is already indexed.

    Dedup key: the basename of ``metadata.source``. A file is "skipped" if
    any chunk in the tenant's collection has a ``metadata.source`` whose
    basename matches the uploaded filename.
    """
    tenant_id = _tenant(x_tenant_id)
    collection = _tenant_collection(tenant_id)
    indexer = get_indexer()

    results: list[dict[str, Any]] = []
    total_added = 0
    total_skipped = 0

    # Snapshot existing source basenames once — avoids re-scanning the index
    # for every file. 100k cap is well above any demo corpus size.
    existing_sources: set[str] = set()
    existing_docs, _ = indexer.filter_documents(
        collection, offset=0, limit=100000, order="asc"
    )
    for d in existing_docs:
        if isinstance(d.metadata, dict):
            existing_sources.add(
                _basename(str(d.metadata.get("source") or ""))
            )

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for upload in files:
            if not upload.filename:
                continue
            safe_name = Path(upload.filename).name
            if not safe_name:
                results.append({
                    "path": upload.filename,
                    "chunks": 0,
                    "status": "skipped_invalid_name",
                })
                total_skipped += 1
                continue
            suffix = Path(safe_name).suffix.lower()
            if suffix not in {".txt", ".md"}:
                results.append({
                    "path": safe_name,
                    "chunks": 0,
                    "status": "skipped_unsupported",
                })
                total_skipped += 1
                continue
            if safe_name in existing_sources:
                results.append({
                    "path": safe_name,
                    "chunks": 0,
                    "status": "skipped_exists",
                })
                total_skipped += 1
                continue

            dest = tmpdir / safe_name
            content = await upload.read()
            dest.write_bytes(content)
            try:
                token = current_tenant_id.set(tenant_id)
                try:
                    n = ingest_file(dest, indexer, collection=collection)
                finally:
                    current_tenant_id.reset(token)
            except Exception as exc:  # noqa: BLE001
                logger.exception("incremental ingest failed for %s", safe_name)
                raise HTTPException(
                    status_code=500,
                    detail=f"ingest failed for {safe_name}: {exc}",
                )
            existing_sources.add(safe_name)
            results.append({
                "path": safe_name,
                "chunks": n,
                "status": "added",
            })
            total_added += n

    return IncrementalUploadResponse(
        results=results,
        total_added=total_added,
        total_skipped=total_skipped,
    )
