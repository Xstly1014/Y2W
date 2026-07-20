"""Agent builder.

Minimal ReAct agent built on top of `langgraph.prebuilt.create_react_agent`.
The agent:
  * receives a list of tools (from tools/, skills/, MCP, RAG retriever)
  * keeps conversation memory via a checkpointer
  * exposes a simple `invoke` / `stream` interface

This is intentionally thin — all richness lives in the pluggable components
(tools, memory, RAG, MCP, skills). Future expansion of any of them does not
require changes here.
"""
from __future__ import annotations

from typing import Any, Iterable

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent


def build_agent(
    llm: BaseChatModel,
    tools: Iterable[BaseTool],
    *,
    system_prompt: str | None = None,
) -> Any:
    """Build a ReAct agent with in-process memory.

    Args:
        llm: The chat model to drive the agent.
        tools: Tools the agent can call (calculator, retriever, MCP, ...).
        system_prompt: Optional system message to shape agent behaviour.

    Returns:
        A compiled langgraph agent. Use ``agent.invoke({"messages": [...]},
        config={"configurable": {"thread_id": thread_id}})`` to run it —
        the thread_id is supplied at call time, not at build time, so
        multiple threads can share one compiled agent.
    """
    checkpointer = MemorySaver()

    agent = create_react_agent(
        model=llm,
        tools=list(tools),
        prompt=system_prompt,
        checkpointer=checkpointer,
    )
    return agent
