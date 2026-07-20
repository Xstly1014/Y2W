"""Pydantic request/response schemas for the API layer.

Security: tenant_id and thread_id flow into filesystem paths (e.g.
``data/traces/<thread_id>.jsonl`` and ``data/vectorstore/<collection>``).
A malicious value like ``../../etc/passwd`` would escape the traces dir
via the ``..`` segments. ``validate_safe_id`` rejects any ID that isn't a
tight alphanumeric / dash / underscore / dot token, and is applied to
every externally-supplied tenant_id / thread_id field below.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Safe ID pattern: ASCII letters, digits, dash, underscore, dot.
# Rejects path separators (/ \), parent-dir segments (..), and any
# whitespace / control chars. Bounded length to prevent abuse.
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def validate_safe_id(value: str | None, field_name: str = "id") -> str | None:
    """Return ``value`` if it is a safe identifier, else raise ValueError.

    Used to guard every externally-supplied string that ends up in a
    filesystem path (tenant_id, thread_id, trace_id). Returns None
    unchanged so callers can keep treating "not supplied" as None.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not _SAFE_ID_PATTERN.match(value):
        raise ValueError(
            f"{field_name} contains illegal characters or is too long "
            "(allowed: A-Z a-z 0-9 . _ -, max 64 chars)"
        )
    return value


class ChatRequest(BaseModel):
    message: str = Field(..., description="User's chat message")
    thread_id: str | None = Field(
        default=None,
        description="Conversation thread id. Server picks one if omitted.",
    )
    tenant_id: str | None = Field(default=None, description="Seller tenant id.")

    @field_validator("thread_id")
    @classmethod
    def _validate_thread_id(cls, v: str | None) -> str | None:
        return validate_safe_id(v, "thread_id")

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str | None) -> str | None:
        return validate_safe_id(v, "tenant_id")


class FeedbackRequest(BaseModel):
    user_input: str
    prediction: str
    passed: bool = Field(..., description="True=good, False=bad")
    trace_id: str | None = None
    thread_id: str | None = None
    tenant_id: str | None = None
    expected: str | None = None
    score: float | None = None

    @field_validator("thread_id")
    @classmethod
    def _validate_thread_id(cls, v: str | None) -> str | None:
        return validate_safe_id(v, "thread_id")

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str | None) -> str | None:
        return validate_safe_id(v, "tenant_id")


class IngestResponse(BaseModel):
    path: str
    chunks: int


class TraceQuery(BaseModel):
    thread_id: str | None = None
    limit: int = 20

    @field_validator("thread_id")
    @classmethod
    def _validate_thread_id(cls, v: str | None) -> str | None:
        return validate_safe_id(v, "thread_id")


class DashboardStats(BaseModel):
    tenant_id: str
    flywheel: dict[str, int]
    traces: dict[str, Any]
    refunds_today: int
    avg_latency_ms: float
    total_cost_usd: float


# --------------------------------------------------------------------------- #
# KB CRUD / versioning / stats responses
# --------------------------------------------------------------------------- #
class DocumentDeleteResponse(BaseModel):
    deleted: bool
    doc_id: str
    collection: str


class CollectionDeleteResponse(BaseModel):
    deleted_count: int
    collection: str


class DocumentListResponse(BaseModel):
    tenant_id: str
    collection: str
    total: int
    offset: int
    limit: int
    documents: list[dict[str, Any]]


class DocumentVersionsResponse(BaseModel):
    source: str
    total_chunks: int
    chunks: list[dict[str, Any]]


class KBStatsResponse(BaseModel):
    tenant_id: str
    collection: str
    total_docs: int
    total_chunks: int
    by_source: list[dict[str, Any]]
    backend: str
    avg_chunk_size: float


class IncrementalUploadResponse(BaseModel):
    results: list[dict[str, Any]]
    total_added: int
    total_skipped: int
