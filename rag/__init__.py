"""RAG (Retrieval-Augmented Generation) package.

Components:
  * `embeddings`  — embedding model factory (OpenAI or local sentence-transformers).
  * `vectorstore` — FAISS-backed vector store, persisted on disk.
  * `indexer`     — high-level add/search API over the vector store, with
                    multi-collection support (one collection per namespace,
                    e.g. documents vs. long-term memory).
  * `retriever`   — thin LangChain retriever wrapper around the indexer.
  * `rag_tool`    — exposes retrieval as an agent-callable tool.

Future expansion hooks:
  * hybrid search (BM25 + dense)
  * reranking model
  * per-document metadata filtering
  * ingestion pipeline (PDF / Markdown / web loaders)
"""
from rag.embeddings import build_embeddings
from rag.vectorstore import build_vectorstore, load_vectorstore
from rag.indexer import Indexer
from rag.retriever import build_retriever
from rag.rag_tool import build_rag_tool
from rag.ingest import ingest_file, ingest_paths, chunk_text

__all__ = [
    "build_embeddings",
    "build_vectorstore",
    "load_vectorstore",
    "Indexer",
    "build_retriever",
    "build_rag_tool",
    "ingest_file",
    "ingest_paths",
    "chunk_text",
]
