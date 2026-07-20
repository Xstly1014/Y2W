"""RAG tool — exposes document retrieval to the agent."""
from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from config import settings
from rag.indexer import Indexer


def build_rag_tool(indexer: Indexer, collection: str = "documents") -> BaseTool:
    """Build an agent-callable tool that retrieves documents from a collection."""

    @tool
    def rag_search(query: str) -> str:
        """Search the project's knowledge base for relevant context.

        Use this when the user asks about information that may live in the
        indexed documents (policies, FAQs, notes, past conversations...).
        Returns the top matched snippets concatenated, or a no-match notice.
        """
        docs = indexer.search(
            query,
            k=settings.retrieval_top_k,
            collection=collection,
        )
        if not docs:
            return f"[rag] no documents matched '{query}'."
        # Filter out the init placeholder doc.
        docs = [d for d in docs if not d.metadata.get("_init")]
        if not docs:
            return f"[rag] no documents matched '{query}'."
        chunks = [f"[{i}] {d.page_content}" for i, d in enumerate(docs, 1)]
        return "\n".join(chunks)

    return rag_search
