"""Tool registry base.

A thin wrapper around a list of LangChain tools so modules can register /
look up tools by name. Each tool just needs to be a `langchain_core.tools.BaseTool`
instance (typically created via the `@tool` decorator).
"""
from __future__ import annotations

from typing import Iterable

from langchain_core.tools import BaseTool


class ToolRegistry:
    """In-memory registry of tools keyed by name."""

    def __init__(self, tools: Iterable[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
