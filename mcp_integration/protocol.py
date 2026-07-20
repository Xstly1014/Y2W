"""MCP protocol data models.

These dataclasses / pydantic models describe the *shape* of MCP protocol
messages without depending on the official `mcp` PyPI SDK. Keeping them
local lets the project compile and run in stub mode, and gives future
real-transport code a stable target to (de)serialise against.

Naming follows the MCP spec:
  * tools      — callable functions the LLM can invoke
  * resources  — file-like, URI-addressable blobs (text or binary)
  * prompts    — server-side prompt templates the LLM can render
  * capabilities — what a server advertises it supports
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MCPToolSpec(BaseModel):
    """MCP tool definition (server -> client advertisement).

    Mirrors the `tools/list` response item in the MCP spec. `input_schema`
    is a JSON Schema dict that becomes the LangChain tool's `args_schema`.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPResource(BaseModel):
    """MCP resource (file-like, addressable by URI).

    Resources let a server expose data the LLM can read by URI — e.g.
    `file:///repo/README.md`, `git://repo/log`, `slack://channel/C123`.
    """

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None


class MCPPrompt(BaseModel):
    """MCP prompt template.

    A server-defined prompt with optional arguments. The LLM requests a
    rendered prompt by name + arguments; the server returns text.
    """

    name: str
    description: str | None = None
    arguments: list[dict[str, Any]] = Field(default_factory=list)


class MCPToolCallResult(BaseModel):
    """Result of invoking an MCP tool.

    Exactly one of `output` / `error` is populated on a real call. The
    stub client always sets `success=True` and `output="[mcp stub] ..."`.
    """

    tool_name: str
    success: bool
    output: str | None = None
    error: str | None = None
    latency_ms: float = 0.0


class MCPCapability(BaseModel):
    """Server capability advertisement.

    Matches the `capabilities` field of an `initialize` response. Each
    boolean flag indicates whether the server implements that part of
    the MCP spec; `experimental` carries vendor-specific extensions.
    """

    tools: bool = False
    resources: bool = False
    prompts: bool = False
    logging: bool = False
    experimental: dict[str, Any] = Field(default_factory=dict)
