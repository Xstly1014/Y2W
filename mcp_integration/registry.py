"""Multi-server MCP registry.

`MCPServerRegistry` owns the lifecycle of every MCP server the agent talks
to. Each server is described by an `MCPServerConfig` (transport, command/URL,
env, expected capabilities) and tracked at runtime by an `MCPServerState`
(connected? last error? tool count?).

The registry is intentionally transport-agnostic: it delegates the actual
connection to one `MCPClient` per server, and `MCPClient` is stub-only for
now. So the registry works with zero external MCP servers running — calling
`connect_all()` simply flips each server's state to "connected" (stub) and
reports `tools_count=0`.

Loading multiple servers from YAML at startup is supported via
`MCPServerRegistry.load_from_yaml(path)`. The schema is documented on the
method. A missing file yields an empty registry (no exception) so the
agent can boot even before the user has written a config file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from mcp_integration.client import MCPClient

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server connection.

    Fields are grouped by transport:
      * stdio:  `command` + `args` + `env` (subprocess spawned by the client)
      * sse/http: `url` (remote endpoint)

    `capabilities` is the *expected* set the caller wants to use; the real
    server may advertise more or fewer. Stub mode ignores it but the field
    is kept so YAML configs stay forward-compatible.
    """

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: float = 30.0
    capabilities: list[str] = field(default_factory=list)


@dataclass
class MCPServerState:
    """Runtime state of a connected server.

    Mirrored from `MCPClient.server_info()` plus a timestamp. Kept as a
    dataclass (not pydantic) because it's mutated in place by the registry
    as connections come and go.
    """

    config: MCPServerConfig
    connected: bool = False
    last_error: str | None = None
    tools_count: int = 0
    connected_at: str | None = None


class MCPServerRegistry:
    """Multi-server MCP registry with lifecycle management.

    Threading model: not thread-safe. The agent builds one registry at
    startup and calls `connect_all()` once; subsequent `all_tools()` /
    `status_summary()` calls are read-only and safe from any thread. If
    you need hot-reload, take a lock around the mutator methods.
    """

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._states: dict[str, MCPServerState] = {}
        self._clients: dict[str, MCPClient] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, config: MCPServerConfig) -> None:
        """Register a server config. Raises `ValueError` if the name exists."""
        if config.name in self._configs:
            raise ValueError(f"MCP server {config.name!r} already registered")
        self._configs[config.name] = config
        self._states[config.name] = MCPServerState(config=config)

    def unregister(self, name: str) -> bool:
        """Remove a server. Disconnects first if connected.

        Returns `True` if a server was removed, `False` if it wasn't
        registered.
        """
        if name not in self._configs:
            return False
        # Best-effort disconnect — swallow errors so unregister always succeeds.
        if self._states.get(name) and self._states[name].connected:
            try:
                import asyncio

                client = self._clients.get(name)
                if client is not None:
                    try:
                        asyncio.get_running_loop()
                        # Already in a loop — schedule close without blocking.
                        asyncio.ensure_future(client.close())
                    except RuntimeError:
                        asyncio.run(client.close())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error disconnecting %s during unregister: %s", name, exc)
        self._configs.pop(name, None)
        self._states.pop(name, None)
        self._clients.pop(name, None)
        return True

    def get_config(self, name: str) -> MCPServerConfig | None:
        return self._configs.get(name)

    def list_configs(self) -> list[MCPServerConfig]:
        return list(self._configs.values())

    def list_states(self) -> list[MCPServerState]:
        return list(self._states.values())

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    async def connect_all(self) -> dict[str, str]:
        """Connect to all enabled servers.

        Returns a mapping ``{name: "ok" | "error: <msg>"}``. Disabled
        servers are skipped (not present in the result). A failure on one
        server does not stop the others from connecting.
        """
        results: dict[str, str] = {}
        for name, config in self._configs.items():
            if not config.enabled:
                continue
            results[name] = await self._connect_one(name)
        return results

    async def connect(self, name: str) -> bool:
        """Connect to a single server. Returns `True` on success."""
        if name not in self._configs:
            logger.warning("connect(%s): not registered", name)
            return False
        result = await self._connect_one(name)
        return result == "ok"

    async def _connect_one(self, name: str) -> str:
        config = self._configs[name]
        state = self._states[name]
        client = MCPClient(config=config)
        try:
            await client.connect()
            self._clients[name] = client
            state.connected = True
            state.last_error = None
            state.tools_count = len(client.list_tools())
            state.connected_at = datetime.now(timezone.utc).isoformat()
            return "ok"
        except Exception as exc:  # noqa: BLE001
            state.connected = False
            state.last_error = str(exc)
            state.tools_count = 0
            state.connected_at = None
            logger.warning("MCP server %s failed to connect: %s", name, exc)
            return f"error: {exc}"

    async def disconnect_all(self) -> None:
        """Disconnect every connected server. Best-effort; errors are logged."""
        for name in list(self._clients.keys()):
            await self.disconnect(name)

    async def disconnect(self, name: str) -> bool:
        """Disconnect a single server. Returns `True` if it was connected."""
        client = self._clients.get(name)
        state = self._states.get(name)
        if client is None or state is None:
            return False
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error closing MCP server %s: %s", name, exc)
            state.last_error = str(exc)
        state.connected = False
        state.tools_count = 0
        state.connected_at = None
        self._clients.pop(name, None)
        return True

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def all_tools(self) -> list[BaseTool]:
        """Flatten tools from all connected servers.

        In stub mode this is always empty — `MCPClient.connect()` doesn't
        populate `_tools` until a real transport is wired in. The method is
        here so the agent's tool assembly stays unchanged when real servers
        arrive.
        """
        tools: list[BaseTool] = []
        for name, client in self._clients.items():
            if self._states[name].connected:
                tools.extend(client.as_tools())
        return tools

    def tools_for_server(self, name: str) -> list[BaseTool]:
        """Tools from a single connected server. Empty if not connected."""
        if name not in self._clients:
            return []
        if not self._states[name].connected:
            return []
        return self._clients[name].as_tools()

    # ------------------------------------------------------------------ #
    # Capability discovery
    # ------------------------------------------------------------------ #
    def discover_capabilities(self, name: str) -> dict[str, Any]:
        """Return a capabilities dict for a server.

        Stub returns ``{"tools": [...], "resources": [], "prompts": []}``
        where `tools` is the list of tool *names* the client has discovered
        (empty in stub mode). Real implementation should call
        `client.list_resources()` / `client.list_prompts()` and merge.
        """
        if name not in self._configs:
            return {"tools": [], "resources": [], "prompts": []}
        client = self._clients.get(name)
        tools = [t.name for t in client.list_tools()] if client is not None else []
        # Resources / prompts are async on the client; in stub mode they're
        # empty anyway, so we report [] without an await. When real transport
        # lands, swap this for an async version or schedule the coroutines.
        return {
            "tools": tools,
            "resources": [],
            "prompts": [],
        }

    def status_summary(self) -> list[dict[str, Any]]:
        """Return one status dict per registered server.

        Each dict has: ``name``, ``connected``, ``tools_count``, ``error``
        (mirrors `MCPServerState.last_error`, or `None`).
        """
        summary: list[dict[str, Any]] = []
        for name, state in self._states.items():
            summary.append(
                {
                    "name": name,
                    "connected": state.connected,
                    "tools_count": state.tools_count,
                    "error": state.last_error,
                }
            )
        return summary

    # ------------------------------------------------------------------ #
    # YAML loading
    # ------------------------------------------------------------------ #
    @classmethod
    def load_from_yaml(cls, path: Path) -> MCPServerRegistry:
        """Build a registry from a YAML config file.

        Schema::

            servers:
              - name: filesystem
                transport: stdio          # "stdio" | "sse" | "http"
                command: npx
                args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
                url: null                 # set for sse/http
                env: {}                   # optional env vars
                enabled: true
                timeout: 30.0
                capabilities: ["tools", "resources"]

        A missing file returns an empty registry (no exception) so the
        agent can boot before the user has written a config. Malformed
        YAML raises `ValueError` with the underlying parse error.
        """
        registry = cls()
        if not Path(path).exists():
            logger.info("MCP servers config %s not found — starting with empty registry.", path)
            return registry

        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - pyyaml is in requirements
            raise RuntimeError(
                "pyyaml is required to load MCP server configs from YAML"
            ) from exc

        raw_text = Path(path).read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Failed to parse MCP config {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"MCP config {path} must be a YAML mapping, got {type(data).__name__}")

        servers = data.get("servers") or []
        if not isinstance(servers, list):
            raise ValueError(
                f"MCP config {path}: 'servers' must be a list, got {type(servers).__name__}"
            )

        for entry in servers:
            if not isinstance(entry, dict):
                logger.warning("Skipping non-mapping server entry in %s: %r", path, entry)
                continue
            name = entry.get("name")
            if not name:
                logger.warning("Skipping server entry without a name in %s", path)
                continue
            config = MCPServerConfig(
                name=name,
                transport=entry.get("transport", "stdio"),
                command=entry.get("command"),
                args=list(entry.get("args") or []),
                url=entry.get("url"),
                env=dict(entry.get("env") or {}),
                enabled=bool(entry.get("enabled", True)),
                timeout=float(entry.get("timeout", 30.0)),
                capabilities=list(entry.get("capabilities") or []),
            )
            try:
                registry.register(config)
            except ValueError:
                logger.warning("Duplicate MCP server name %r in %s — skipping.", name, path)
        return registry
