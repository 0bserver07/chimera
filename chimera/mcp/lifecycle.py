from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any

from chimera.mcp.client import MCPClient


@dataclass
class MCPServerLifecycle:
    """Manage MCP server connections with memoization and lifecycle control.

    Returns real :class:`MCPClient` instances, keyed by config hash. Same
    config → same client (memoized). Ownership tracking lets cleanup release
    clients when all owning agents are done.

    The returned client is *registered* (has transport info) but not yet
    connected — call ``client.connect_all()`` when you need live tools.
    Connection is deferred so that lifecycle bookkeeping works in tests and
    offline scenarios.
    """

    _connections: dict[str, MCPClient] = field(default_factory=dict)
    _agent_owned: dict[str, set[str]] = field(default_factory=dict)

    async def connect(self, config: dict) -> MCPClient:
        """Register an MCP server client. Memoized — same config reuses client.

        Config shape::

            {"name": str, "url": str}            # HTTP server
            {"name": str, "command": str,        # stdio server
             "args": [str], "env": {str: str}}
        """
        key = self._cache_key(config)
        if key not in self._connections:
            client = MCPClient()
            name = config.get("name") or key[:16]
            if "url" in config:
                client.add_http(name, config["url"], auth=config.get("auth"))
            elif "command" in config:
                client.add_stdio(
                    name,
                    config["command"],
                    args=config.get("args"),
                    env=config.get("env"),
                )
            else:
                raise ValueError(
                    f"MCP config needs 'url' or 'command': {config!r}"
                )
            self._connections[key] = client
        return self._connections[key]

    async def connect_for_agent(self, config: dict, agent_id: str) -> Any:
        """Connect for a specific agent. Tracks ownership for cleanup."""
        key = self._cache_key(config)
        client = await self.connect(config)
        self._agent_owned.setdefault(agent_id, set()).add(key)
        return client

    async def cleanup_agent(self, agent_id: str) -> None:
        """Disconnect servers owned by agent (if no other agent uses them)."""
        owned_keys = self._agent_owned.pop(agent_id, set())
        for key in owned_keys:
            other_owners = any(key in keys for keys in self._agent_owned.values())
            if not other_owners and key in self._connections:
                client = self._connections.pop(key)
                try:
                    client.disconnect_all()
                except Exception:  # best-effort cleanup
                    pass

    async def cleanup_all(self) -> None:
        """Disconnect all. Called at session end."""
        for client in self._connections.values():
            try:
                client.disconnect_all()
            except Exception:  # best-effort cleanup
                pass
        self._connections.clear()
        self._agent_owned.clear()

    def _cache_key(self, config: dict) -> str:
        return json.dumps(config, sort_keys=True)
