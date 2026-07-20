"""MCP client.

The MCP SDK is still evolving, so this file deliberately keeps the surface
area small. Two responsibilities:
  1. `MCPClient.connect()` — establish a session with an MCP server.
  2. `MCPClient.as_tools()` — return LangChain tools wrapping the server's
     MCP tools, ready to be passed to the agent.

When `MCP_SERVER_URL` is empty (and no `config` is supplied), the client
stays disconnected and `as_tools()` returns an empty list — so the rest
of the project runs fine without an MCP server configured.

Extension (multi-server): the constructor accepts an optional
`MCPServerConfig` so the registry can spin up one client per registered
server. The legacy `server_url` argument is preserved for backwards
compatibility — `main.py` still calls `MCPClient()` with no args.

All real transport (stdio/sse/http) is stubbed: `connect()` logs a
warning, `list_resources()` / `list_prompts()` return empty lists, and
`read_resource()` / `get_prompt()` return stub strings. The interface is
stable; wiring in the real `mcp` SDK only changes this file's internals.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool, StructuredTool

from config import settings
from mcp_integration.protocol import MCPToolSpec

if TYPE_CHECKING:
    # Avoid a runtime circular import: registry.py imports MCPClient, so we
    # only need the type at annotation-checking time. `from __future__ import
    # annotations` keeps the annotation as a string at runtime.
    from mcp_integration.registry import MCPServerConfig

logger = logging.getLogger(__name__)

# Backwards-compat alias: the original module exposed a private `_MCPToolSpec`
# with the same shape as the protocol's `MCPToolSpec`. Keep the name so any
# existing `from mcp_integration.client import _MCPToolSpec` keeps working.
_MCPToolSpec = MCPToolSpec


class MCPClient:
    """Thin wrapper around an MCP server connection.

    Two construction modes:
      * Legacy:   `MCPClient(server_url="http://...")`  — single server via URL.
      * Registry: `MCPClient(config=MCPServerConfig(...))` — full per-server config.

    Both modes coexist; `config` wins over `server_url` when both are given.
    """

    def __init__(
        self,
        server_url: str | None = None,
        *,
        config: MCPServerConfig | None = None,
    ) -> None:
        self._config: MCPServerConfig | None = config
        # `config.url` (sse/http) takes precedence when no explicit server_url
        # is passed. For stdio servers there is no URL — server_url stays "".
        if config and config.url and not server_url:
            self.server_url: str = config.url
        else:
            self.server_url = server_url or settings.mcp_server_url
        self._session: Any | None = None
        self._tools: list[MCPToolSpec] = []
        self._connected: bool = False

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def _has_endpoint(self) -> bool:
        """True if there's anything to connect to (URL or stdio command)."""
        if self.server_url:
            return True
        if self._config is not None and (self._config.url or self._config.command):
            return True
        return False

    def _endpoint_label(self) -> str:
        if self.server_url:
            return self.server_url
        if self._config is not None:
            return f"{self._config.transport}:{self._config.name}"
        return "<no endpoint>"

    async def connect(self) -> None:
        """Connect to the configured MCP server and discover its tools.

        Left as a no-op when no endpoint is configured, so the project is
        runnable without an MCP server. Plug the real `mcp` SDK client here.
        """
        if not self._has_endpoint():
            logger.info("MCP server URL not set — skipping connect.")
            return
        # TODO: real connection via `mcp` SDK, e.g.
        #   from mcp import ClientSession, StdioServerParameters
        #   ... establish session, call list_tools(), populate self._tools
        logger.warning(
            "MCPClient.connect() is a stub. Implement real transport for %s",
            self._endpoint_label(),
        )
        # Mark connected so server_info() / registry can report state. The
        # real implementation would set this only after a successful handshake.
        self._connected = True

    async def close(self) -> None:
        if self._session is not None:
            # TODO: await self._session.aclose()
            self._session = None
        self._connected = False

    # ------------------------------------------------------------------ #
    # Tools (existing API — preserved)
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[MCPToolSpec]:
        return list(self._tools)

    def as_tools(self) -> list[BaseTool]:
        """Wrap each discovered MCP tool as a LangChain `StructuredTool`.

        Each tool's invocation will (eventually) call the MCP server and
        return its result. For now this returns an empty list — the agent
        simply runs without MCP tools until a real server is wired in.
        """
        tools: list[BaseTool] = []
        for spec in self._tools:
            tools.append(self._wrap(spec))
        return tools

    def _wrap(self, spec: MCPToolSpec) -> BaseTool:
        async def _arun(**kwargs: Any) -> str:
            # TODO: return await self._session.call_tool(spec.name, kwargs)
            return f"[mcp stub] {spec.name} called with {kwargs}"

        def _run(**kwargs: Any) -> str:
            """Sync entry point — safe to call from a sync or async context.

            `asyncio.run()` raises if a loop is already running (e.g. when
            LangChain invokes tools from inside an event loop). Detect that
            case and reuse the running loop instead.

            A timeout is enforced on the cross-thread bridge — without it a
            hung MCP call would block the calling thread (and thus the
            entire event loop) forever.
            """
            import asyncio
            import threading

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            if running is None:
                return asyncio.run(_arun(**kwargs))

            # We're inside a running loop (e.g. FastAPI). Schedule the coroutine
            # on a separate thread + loop and block until it completes or the
            # timeout fires. Without the timeout, a hung MCP call would
            # deadlock the parent loop.
            result_box: dict[str, Any] = {}
            loop_event = threading.Event()

            def _runner() -> None:
                new_loop = asyncio.new_event_loop()
                try:
                    result_box["value"] = new_loop.run_until_complete(_arun(**kwargs))
                except Exception as exc:  # noqa: BLE001
                    result_box["error"] = exc
                finally:
                    new_loop.close()
                    loop_event.set()

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            # 30s is generous for stub calls; real MCP servers should be
            # similarly bounded. Use a daemon thread so a hung call never
            # blocks process shutdown.
            if not loop_event.wait(timeout=30.0):
                raise TimeoutError(
                    f"MCP tool {spec.name!r} did not return within 30s"
                )
            if "error" in result_box:
                raise result_box["error"]
            return result_box.get("value", "")

        return StructuredTool.from_function(
            name=spec.name,
            description=spec.description,
            func=_run,
            coroutine=_arun,
            args_schema=spec.input_schema,  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------ #
    # Resources (new — protocol layer)
    # ------------------------------------------------------------------ #
    async def list_resources(self) -> list[dict[str, Any]]:
        """List MCP resources advertised by the server.

        Stub returns an empty list. Real implementation should call
        `session.list_resources()` and map each to the `MCPResource` schema.
        """
        if not self._connected:
            return []
        # TODO: return [r.model_dump() for r in await self._session.list_resources()]
        return []

    async def read_resource(self, uri: str) -> str:
        """Read a resource by URI.

        Stub returns a placeholder string so callers can verify the wiring
        without a real server. Real implementation should delegate to
        `session.read_resource(uri)` and return the decoded text.
        """
        if not self._connected:
            return f"[mcp stub] resource {uri}"
        # TODO: return await self._session.read_resource(uri)
        return f"[mcp stub] resource {uri}"

    # ------------------------------------------------------------------ #
    # Prompts (new — protocol layer)
    # ------------------------------------------------------------------ #
    async def list_prompts(self) -> list[dict[str, Any]]:
        """List MCP prompt templates advertised by the server.

        Stub returns an empty list. Real implementation should call
        `session.list_prompts()` and map each to the `MCPPrompt` schema.
        """
        if not self._connected:
            return []
        # TODO: return [p.model_dump() for p in await self._session.list_prompts()]
        return []

    async def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        """Render a server-side prompt by name with optional arguments.

        Stub returns a placeholder string. Real implementation should call
        `session.get_prompt(name, arguments)` and return the rendered text.
        """
        # Stub behaviour is identical whether connected or not — the
        # placeholder lets callers test the call path without a server.
        # TODO: return await self._session.get_prompt(name, arguments)
        return f"[mcp stub] prompt {name}"

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #
    def server_info(self) -> dict[str, Any]:
        """Return server metadata for diagnostics.

        Shape: ``{"url": ..., "transport": ..., "connected": ..., "tools_count": ...}``.
        Used by the registry's `status_summary()` and by `/health`-style
        endpoints. `transport` is inferred from the config if present,
        otherwise from whether a URL is set (URL => "http", else None).
        """
        if self._config is not None:
            transport = self._config.transport
            url = self._config.url or self.server_url or None
        else:
            transport = "http" if self.server_url else None
            url = self.server_url or None
        return {
            "url": url,
            "transport": transport,
            "connected": self._connected,
            "tools_count": len(self._tools),
        }
