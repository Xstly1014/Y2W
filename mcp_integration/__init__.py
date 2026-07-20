"""MCP (Model Context Protocol) integration package.

MCP standardises how an LLM agent talks to external tool servers
(filesystem, databases, browser, Slack, ...). This package provides:

  * `protocol`  — pydantic models for MCP tools / resources / prompts /
    capabilities, independent of the official `mcp` SDK.
  * `client`    — a single-server `MCPClient` that connects to an MCP
    server (stdio or HTTP), lists its tools, and wraps them as LangChain
    `BaseTool` instances. Stub-only for now; the real `mcp` SDK plugs in
    here.
  * `registry`  — a multi-server `MCPServerRegistry` that owns the
    lifecycle of every connected server, plus YAML config loading.

The package is named `mcp_integration` (not `mcp`) on purpose so it does
not shadow the official `mcp` PyPI SDK when we wire in real transport.

Future expansion hooks:
  * real transport (replace stubs in `client.py`)
  * authentication / OAuth
  * streaming tool results
"""
from mcp_integration.client import MCPClient
from mcp_integration.protocol import (
    MCPCapability,
    MCPPrompt,
    MCPResource,
    MCPToolCallResult,
    MCPToolSpec,
)
from mcp_integration.registry import (
    MCPServerConfig,
    MCPServerRegistry,
    MCPServerState,
)

__all__ = [
    "MCPClient",
    "MCPServerConfig",
    "MCPServerState",
    "MCPServerRegistry",
    "MCPToolSpec",
    "MCPResource",
    "MCPPrompt",
    "MCPToolCallResult",
    "MCPCapability",
]
