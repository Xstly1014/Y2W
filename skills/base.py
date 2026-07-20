"""Skill base class.

A skill produces a list of LangChain tools. Subclasses implement
`build_tools()` to declare their tools — the base class handles
registration plumbing.

Metadata fields (version / tags / permissions / dependencies /
enabled_by_default) let the registry and UI inspect a skill without
instantiating heavy dependencies. Tuple defaults are used for collection
fields to avoid the classic Python "mutable class attribute shared across
subclasses" trap; `metadata()` converts them to plain lists for JSON
friendliness.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from langchain_core.tools import BaseTool


class Skill(ABC):
    """Abstract skill. Subclasses set `name` / `description` and implement `build_tools`."""

    name: str = "skill"
    description: str = "A skill."
    # ----- Metadata (new) -----
    version: str = "0.1.0"
    # Tuple defaults avoid the mutable-class-attribute trap; callers get
    # lists via `metadata()` or by `list(skill.tags)` themselves.
    tags: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    enabled_by_default: bool = True

    def __init__(self) -> None:
        self._tools: list[BaseTool] | None = None

    @abstractmethod
    def build_tools(self) -> list[BaseTool]:
        """Return the LangChain tools this skill contributes to the agent."""

    def get_tools(self) -> list[BaseTool]:
        if self._tools is None:
            self._tools = self.build_tools()
        return self._tools

    def as_tools(self) -> Iterable[BaseTool]:
        return self.get_tools()

    def metadata(self) -> dict:
        """Return skill metadata for registry/UI display."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "tags": list(self.tags),
            "permissions": list(self.permissions),
            "dependencies": list(self.dependencies),
            "enabled_by_default": self.enabled_by_default,
            "tools": [t.name for t in self.get_tools()],
        }
