"""Search tool.

A deliberately small in-memory knowledge base so the project runs without
external API keys. Replace the body of `search_tool` with a real web search
(Tavily, Bing, SerpAPI etc.) later — the function signature stays the same.
"""
from __future__ import annotations

from langchain_core.tools import tool

# Tiny demo knowledge base — swap for real search later.
_KB: dict[str, str] = {
    "langchain": "LangChain is a framework for building applications powered by LLMs.",
    "react": "ReAct is an agent reasoning pattern interleaving Thought / Action / Observation.",
    "rag": "RAG (Retrieval-Augmented Generation) grounds LLM answers in retrieved documents.",
    "mcp": "MCP (Model Context Protocol) standardises how models access external tools & data.",
}


@tool
def search_tool(query: str) -> str:
    """Look up a short piece of knowledge by keyword.

    Args:
        query: A keyword such as 'langchain', 'react', 'rag', or 'mcp'.
    """
    q = query.strip().lower()
    for key, value in _KB.items():
        if key in q:
            return value
    return f"[no result] nothing matched '{query}'."
