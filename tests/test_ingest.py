"""Tests for the RAG ingestion pipeline.

We do NOT touch FAISS here — that requires loading the BGE embedding
model (~500MB) and would make tests slow. We test the pure-string
parts: loaders + chunker. The indexer is mocked.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rag.ingest import (
    chunk_text, load_file, load_markdown_file, load_text_file,
    ingest_file, ingest_paths,
)


# --------------------------------------------------------------------------- #
# chunk_text
# --------------------------------------------------------------------------- #
def test_chunk_text_basic_split() -> None:
    text = "abcdefghij" * 100  # 1000 chars
    chunks = chunk_text(text, chunk_size=100, overlap=0)
    assert len(chunks) == 10
    assert all(len(c) == 100 for c in chunks)


def test_chunk_text_with_overlap() -> None:
    text = "0123456789" * 10  # 100 chars
    chunks = chunk_text(text, chunk_size=50, overlap=10)
    # step = 50 - 10 = 40; positions 0, 40, 80 -> 3 chunks (80+50=130 > 100, last is 80:100)
    assert len(chunks) == 3
    assert chunks[0] == "0123456789" * 5
    # Overlap: chunk[1] starts 40 chars in, so first 10 chars == chunk[0] last 10 chars.
    assert chunks[1][:10] == chunks[0][-10:]


def test_chunk_text_short_text_single_chunk() -> None:
    chunks = chunk_text("hello", chunk_size=500, overlap=50)
    assert chunks == ["hello"]


def test_chunk_text_empty_string() -> None:
    assert chunk_text("") == []


def test_chunk_text_strips_whitespace() -> None:
    chunks = chunk_text("  hello  world  ", chunk_size=20, overlap=0)
    assert chunks == ["hello  world"]


def test_chunk_text_rejects_bad_chunk_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=0)
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=-1)


def test_chunk_text_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, overlap=-1)
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, overlap=10)  # overlap == chunk_size
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, overlap=11)  # overlap > chunk_size


def test_chunk_text_defaults_from_settings() -> None:
    """No explicit chunk_size -> uses settings.chunk_size (500 by default)."""
    text = "x" * 600
    chunks = chunk_text(text)
    # 500 chars + overlap 50 -> step 450; positions 0, 450 -> 2 chunks
    assert len(chunks) == 2


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def test_load_text_file(tmp_path: Path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text("hello world", encoding="utf-8")
    assert load_text_file(p) == "hello world"


def test_load_markdown_file_strips_code_fences(tmp_path: Path) -> None:
    p = tmp_path / "doc.md"
    p.write_text(
        "# Title\n\n"
        "Some prose.\n\n"
        "```python\nprint('hello')\n```\n\n"
        "More prose.\n",
        encoding="utf-8",
    )
    text = load_markdown_file(p)
    assert "print('hello')" not in text
    assert "# Title" in text
    assert "Some prose." in text
    assert "More prose." in text


def test_load_file_dispatches_by_extension(tmp_path: Path) -> None:
    txt = tmp_path / "a.txt"
    md = tmp_path / "b.md"
    txt.write_text("plain", encoding="utf-8")
    md.write_text("# md", encoding="utf-8")
    assert load_file(txt) == "plain"
    assert load_file(md) == "# md"


def test_load_file_rejects_unknown_extension(tmp_path: Path) -> None:
    p = tmp_path / "x.pdf"
    p.write_text("not really pdf", encoding="utf-8")
    with pytest.raises(ValueError, match="No loader"):
        load_file(p)


# --------------------------------------------------------------------------- #
# ingest_file / ingest_paths (with mocked Indexer)
# --------------------------------------------------------------------------- #
def test_ingest_file_returns_chunk_count(tmp_path: Path) -> None:
    p = tmp_path / "doc.txt"
    p.write_text("x" * 600, encoding="utf-8")
    fake_indexer = MagicMock()
    n = ingest_file(p, fake_indexer, collection="c")
    assert n == 2  # 600 chars / chunk_size 500, overlap 50 -> 2 chunks
    fake_indexer.add_documents.assert_called_once()
    added_docs = fake_indexer.add_documents.call_args[0][0]
    assert len(added_docs) == 2
    # Metadata includes source and chunk_index.
    assert added_docs[0].metadata["source"] == str(p)
    assert added_docs[0].metadata["chunk_index"] == 0
    assert added_docs[0].metadata["total_chunks"] == 2


def test_ingest_file_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ingest_file(tmp_path / "nope.txt", MagicMock())


def test_ingest_paths_handles_file_and_dir(tmp_path: Path) -> None:
    # Build a small tree:
    #   tmp/a.txt
    #   tmp/sub/b.md
    (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("bbb", encoding="utf-8")
    # An unsupported extension should be silently skipped by rglob filter.
    (tmp_path / "sub" / "c.bin").write_text("binary", encoding="utf-8")

    fake_indexer = MagicMock()
    results = ingest_paths([tmp_path / "a.txt", tmp_path], fake_indexer, collection="c")
    # a.txt -> 1 chunk (3 chars). Directory -> 2 files * 1 chunk each = 2 chunks.
    assert results[str(tmp_path / "a.txt")] == 1
    assert results[str(tmp_path)] == 2


def test_ingest_paths_missing_path_returns_zero(tmp_path: Path) -> None:
    fake_indexer = MagicMock()
    results = ingest_paths([tmp_path / "ghost"], fake_indexer, collection="c")
    assert results[str(tmp_path / "ghost")] == 0
