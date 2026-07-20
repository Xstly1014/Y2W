"""Memory package.

Two layers, mirroring how human memory works:
  * `short_term`  — the rolling conversation history passed back to the LLM.
  * `long_term`   — facts / preferences persisted to a vector store and
                    recalled by semantic similarity when relevant.

Long-term memory is enhanced with importance scoring, a forgetting curve,
structured fact extraction, per-user namespacing, and LangChain tool
bindings — see `LongTermMemory` and `build_memory_tools`.
"""
from memory.short_term import ShortTermMemory
from memory.long_term import (
    IMPORTANCE_HIGH,
    IMPORTANCE_LOW,
    IMPORTANCE_NORMAL,
    LongTermMemory,
)
from memory.memory_tool import build_memory_tools

__all__ = [
    "ShortTermMemory",
    "LongTermMemory",
    "IMPORTANCE_HIGH",
    "IMPORTANCE_NORMAL",
    "IMPORTANCE_LOW",
    "build_memory_tools",
]
