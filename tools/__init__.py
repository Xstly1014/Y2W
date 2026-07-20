"""Tools package.

`get_builtin_tools()` returns the list of always-available tools. Other
modules (skills, mcp, rag) expose their own `as_tools()` to plug into the
agent at runtime — see `main.py` for assembly.
"""
from tools.base import ToolRegistry
from tools.builtin.calculator import calculator_tool
from tools.builtin.time_tool import current_time_tool
from tools.builtin.search import search_tool


def get_builtin_tools() -> list:
    """Return the default built-in tool set."""
    return [calculator_tool, current_time_tool, search_tool]


__all__ = ["ToolRegistry", "get_builtin_tools"]
