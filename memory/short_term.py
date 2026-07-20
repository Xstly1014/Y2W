"""Short-term conversation memory.

A simple bounded message buffer. The agent itself uses langgraph's
`MemorySaver` checkpointer for multi-turn state; this class is the
*application-side* view used to:
  * display recent history to the user
  * export context for evaluation / badcase collection
  * feed into prompt templates when a custom (non-langgraph) flow is used
"""
from __future__ import annotations

from collections import deque
from typing import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


class ShortTermMemory:
    """Bounded deque of recent messages (human / ai pairs)."""

    def __init__(self, max_messages: int) -> None:
        # Guard against non-positive bounds — ``deque(maxlen=0)`` would
        # silently discard every message, and ``deque(maxlen=-1)`` raises
        # a confusing ValueError deep in the stdlib. Fail fast instead.
        if not isinstance(max_messages, int) or max_messages <= 0:
            raise ValueError(
                f"max_messages must be a positive int, got {max_messages!r}"
            )
        self._buffer: deque[BaseMessage] = deque(maxlen=max_messages)

    def add_user(self, content: str) -> None:
        self._buffer.append(HumanMessage(content=content))

    def add_ai(self, content: str) -> None:
        self._buffer.append(AIMessage(content=content))

    def add_many(self, messages: Iterable[BaseMessage]) -> None:
        for m in messages:
            self._buffer.append(m)

    def messages(self) -> list[BaseMessage]:
        return list(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    def as_dicts(self) -> list[dict[str, str]]:
        """Compact serialisable form for logging / badcase capture."""
        out: list[dict[str, str]] = []
        for m in self._buffer:
            role = "human" if isinstance(m, HumanMessage) else (
                "ai" if isinstance(m, AIMessage) else m.type
            )
            out.append({"role": role, "content": m.content})
        return out
