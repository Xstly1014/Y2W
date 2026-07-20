"""Smoke test for the PG vector backend.

Run with:
    VECTOR_STORE_BACKEND=pg_python OPENAI_API_KEY=sk-dummy \
    .venv/Scripts/python -m scripts.smoke_pg_vector
"""
from __future__ import annotations

import hashlib

from langchain_core.documents import Document

from rag.pg_vectorstore import PGVectorStore, ensure_agent_vectors_db


class FakeEmb:
    """Deterministic embedding for smoke test — no OpenAI call."""

    def embed_query(self, t: str) -> list[float]:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        return [b / 255.0 for b in h[:32]]

    def embed_documents(self, ts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in ts]


def main() -> None:
    ok = ensure_agent_vectors_db()
    print("db ensure:", ok)
    assert ok, "failed to ensure agent_vectors DB"

    store = PGVectorStore(FakeEmb(), "smoke_test")
    store.delete_collection()  # clean slate

    docs = [
        Document(page_content="hello world", metadata={"i": 0}),
        Document(page_content="refund policy", metadata={"i": 1}),
        Document(page_content="order tracking", metadata={"i": 2}),
        Document(page_content="shipping cost", metadata={"i": 3}),
    ]
    store.add_documents(docs)
    print("added:", len(docs))
    assert store.list_documents() and len(store.list_documents()) == 4

    res1 = store.similarity_search("refund", k=2)
    print("search 'refund' k=2:", [(d.page_content, d.metadata) for d in res1])
    assert len(res1) == 2

    res2 = store.similarity_search("order", k=2)
    print("search 'order' k=2:", [(d.page_content, d.metadata) for d in res2])
    assert len(res2) == 2

    # Retriever interface
    retr = store.as_retriever(search_kwargs={"k": 1})
    res3 = retr.invoke("shipping")
    print("retriever.invoke('shipping') k=1:", [d.page_content for d in res3])
    assert len(res3) == 1

    n = store.delete_collection()
    print("deleted:", n)
    assert n == 4
    store.close()
    print("ALL OK")


if __name__ == "__main__":
    main()
