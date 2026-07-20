"""Retriever factory — wraps the indexer in a LangChain retriever."""
from __future__ import annotations

from langchain_core.retrievers import BaseRetriever

from config import settings
from rag.indexer import Indexer


def build_retriever(
    indexer: Indexer,
    collection: str = "documents",
    k: int | None = None,
) -> BaseRetriever:
    # `k or settings.retrieval_top_k` previously swallowed k=0 (falsy) and
    # replaced it with the default — silently breaking callers that
    # intentionally requested "no results". Use an explicit None check.
    effective_k = k if k is not None else settings.retrieval_top_k
    return indexer.as_retriever(
        collection=collection,
        k=effective_k,
    )
