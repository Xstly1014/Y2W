"""LangChain tools exposing long-term memory to the agent.

Two tools, both bound to a single ``LongTermMemory`` instance via closure
(so the agent doesn't need to know about the memory object itself):

- ``save_memory(text, importance=0.5, category="fact")`` — persist a memory.
- ``recall_memory(query, k=3)`` — retrieve the most relevant memories.

Both tools are defensive: any error is caught and returned as a string so
the agent never crashes mid-conversation. The agent sees the error in
plain text, which it can surface to the user or work around.

Usage (future integration in ``api/deps.py``)::

    from memory.memory_tool import build_memory_tools
    memory_tools = build_memory_tools(long_term_memory)
    # ... then add to the agent's tool list.

The integration itself is intentionally NOT done here — wiring memory tools
into ``api/deps.py`` is left as a separate change to keep this PR reviewable.
"""
from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, tool

from memory.long_term import IMPORTANCE_NORMAL, LongTermMemory

logger = logging.getLogger(__name__)


def build_memory_tools(ltm: LongTermMemory) -> list[BaseTool]:
    """Build tools bound to a specific ``LongTermMemory`` instance.

    Each tool closure captures ``ltm`` so the agent doesn't need to know
    about the memory instance directly. Re-calling this builder for a
    different ``ltm`` (e.g. per-tenant) returns a fresh tool set.
    """

    @tool
    def save_memory(
        text: str,
        importance: float = IMPORTANCE_NORMAL,
        category: str = "fact",
    ) -> str:
        """Persist a long-term memory.

        Args:
            text: the memory content (a fact, preference, or decision).
            importance: 0.0-1.0, higher = more likely to recall later.
                0.9 = critical (user preference, irreversible decision);
                0.5 = normal (general fact); 0.2 = trivia.
            category: one of fact|preference|decision|event|skill.

        Returns the memory doc_id on success (prefixed with ``"saved memory "``),
        or an error message on failure.
        """
        if not text or not text.strip():
            return "save_memory error: empty text"
        try:
            doc_id = ltm.remember_with_importance(
                text, importance=importance, category=category
            )
            return f"saved memory {doc_id}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("save_memory tool failed")
            return f"save_memory error: {exc}"

    @tool
    def recall_memory(query: str, k: int = 3) -> str:
        """Recall relevant memories for a query.

        Args:
            query: natural-language query (e.g. "user's refund preference").
            k: number of memories to recall (default 3, max 10).

        Returns a newline-separated list of memories, each prefixed with
        ``[category|imp=X]`` so the agent can see the importance tier at a
        glance. Returns ``"no memories found"`` if the store is empty.
        """
        if not query or not query.strip():
            return "recall_memory error: empty query"
        try:
            k = max(1, min(int(k), 10))
            docs = ltm.recall(query, k=k)
            if not docs:
                return "no memories found"
            lines = []
            for d in docs:
                meta = d.metadata if isinstance(d.metadata, dict) else {}
                imp = meta.get("importance", "?")
                cat = meta.get("category", "?")
                lines.append(f"[{cat}|imp={imp}] {d.page_content}")
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            logger.exception("recall_memory tool failed")
            return f"recall_memory error: {exc}"

    return [save_memory, recall_memory]
