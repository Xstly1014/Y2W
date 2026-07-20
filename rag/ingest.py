"""RAG ingestion pipeline.

The missing half of RAG: a way to actually put documents INTO the index.
Without this, `rag_search` always returns "no documents matched".

Pipeline:
    file ──> loader ──> chunks ──> indexer.add_documents()

Currently supports:
  * .txt   — raw text, split by char count with overlap
  * .md    — strip code fences, then split like .txt

Future expansion hooks:
  * PDF / docx / HTML loaders (pypdf, python-docx, beautifulsoup)
  * semantic chunking (by sentence / paragraph / heading)
  * metadata extraction (title, source, page)
  * batch ingestion from a directory watcher
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document

from config import settings
from rag.indexer import Indexer


# --------------------------------------------------------------------------- #
# Loaders — turn a file on disk into a single string
# --------------------------------------------------------------------------- #
def load_text_file(path: Path) -> str:
    """Load a plain text file."""
    return path.read_text(encoding="utf-8")


def load_markdown_file(path: Path) -> str:
    """Load a Markdown file, stripping code fences for cleaner chunking.

    We keep headings and prose but drop the ``` blocks because they often
    contain code that doesn't embed well.
    """
    import re

    text = path.read_text(encoding="utf-8")
    # Strip fenced code blocks (```...```)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return text


_LOADERS = {
    ".txt": load_text_file,
    ".md": load_markdown_file,
}


def load_file(path: Path) -> str:
    """Dispatch to the right loader by file extension. Raises on unknown."""
    suffix = path.suffix.lower()
    loader = _LOADERS.get(suffix)
    if loader is None:
        raise ValueError(
            f"No loader for {suffix!r} (supported: {sorted(_LOADERS)})"
        )
    return loader(path)


# --------------------------------------------------------------------------- #
# Chunking — turn one long string into a list of chunk strings
# --------------------------------------------------------------------------- #
def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """Naive char-based chunking with overlap.

    Args:
        text: The raw text to chunk.
        chunk_size: Max chars per chunk (default from settings).
        overlap: How many chars to overlap between consecutive chunks
            (default from settings).
    """
    chunk_size = chunk_size if chunk_size is not None else settings.chunk_size
    overlap = overlap if overlap is not None else settings.chunk_overlap
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be in [0, chunk_size)")

    chunks: list[str] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start += chunk_size - overlap
    return chunks


# --------------------------------------------------------------------------- #
# Orchestrator — turn a file into Documents and add to the index
# --------------------------------------------------------------------------- #
def ingest_file(
    path: Path | str,
    indexer: Indexer,
    collection: str = "documents",
) -> int:
    """Load, chunk, and index a single file. Returns the number of chunks added."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    text = load_file(path)
    chunks = chunk_text(text)
    docs = [
        Document(
            page_content=chunk,
            metadata={
                "source": str(path),
                "chunk_index": i,
                "total_chunks": len(chunks),
            },
        )
        for i, chunk in enumerate(chunks)
    ]
    # Short-circuit empty doc lists: FAISS / vectorstore backends raise
    # dimension-mismatch exceptions when called with `add_documents([])`
    # because there's no embedding to infer the index dimensionality from.
    if not docs:
        return 0
    indexer.add_documents(docs, collection=collection)
    return len(docs)


def ingest_paths(
    paths: Iterable[Path | str],
    indexer: Indexer,
    collection: str = "directories",
) -> dict[str, int]:
    """Ingest a mix of files and directories. Returns {path: chunks_added}."""
    results: dict[str, int] = {}
    for p in paths:
        path = Path(p)
        if path.is_file():
            results[str(path)] = ingest_file(path, indexer, collection=collection)
        elif path.is_dir():
            total = 0
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in _LOADERS:
                    total += ingest_file(child, indexer, collection=collection)
            results[str(path)] = total
        else:
            results[str(path)] = 0
    return results
