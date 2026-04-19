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

    By default the returned client is *registered* (transport configured)
    but not yet connected. Pass ``eager_connect=True`` to have
    :meth:`connect` (and :meth:`connect_for_agent`) actually spin up the
    transport via :meth:`MCPClient.connect_all` before returning. That
    second mode is what real users want; the default keeps existing
    offline/unit tests working without having to stand up a live server.
    """

    _connections: dict[str, MCPClient] = field(default_factory=dict)
    _agent_owned: dict[str, set[str]] = field(default_factory=dict)
    # Tracks which cache keys have already had connect_all() called, so
    # we don't double-connect when the same config is requested twice.
    _connected_keys: set[str] = field(default_factory=set)

    async def connect(
        self, config: dict, *, eager_connect: bool = False
    ) -> MCPClient:
        """Register an MCP server client. Memoized — same config reuses client.

        Args:
            config: MCP server config. Shape::

                {"name": str, "url": str}            # HTTP server
                {"name": str, "command": str,        # stdio server
                 "args": [str], "env": {str: str}}

            eager_connect: If True, call ``client.connect_all()`` to
                actually bring the transport up before returning. When
                connect_all() raises, we surface the failure as a
                :class:`ConnectionError` so callers can react. Defaults
                to False to preserve prior behaviour (used heavily by
                offline tests).

        Returns:
            The registered (and optionally connected) MCPClient.

        Raises:
            ValueError: If the config lacks both ``url`` and ``command``.
            ConnectionError: If ``eager_connect=True`` and
                ``connect_all()`` fails.
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

        client = self._connections[key]
        if eager_connect and key not in self._connected_keys:
            try:
                client.connect_all()
            except Exception as exc:
                # Roll back registration so the caller can retry with a
                # fixed config without tripping the memoization cache.
                self._connections.pop(key, None)
                raise ConnectionError(
                    f"MCP connect_all() failed for {config!r}: {exc}"
                ) from exc
            self._connected_keys.add(key)
        return client

    async def connect_for_agent(
        self, config: dict, agent_id: str, *, eager_connect: bool = False
    ) -> Any:
        """Connect for a specific agent. Tracks ownership for cleanup.

        Args:
            config: Same shape as :meth:`connect`.
            agent_id: Identifier used to track which agent owns the
                connection (for :meth:`cleanup_agent`).
            eager_connect: Forwarded to :meth:`connect`.
        """
        key = self._cache_key(config)
        client = await self.connect(config, eager_connect=eager_connect)
        self._agent_owned.setdefault(agent_id, set()).add(key)
        return client

    async def cleanup_agent(self, agent_id: str) -> None:
        """Disconnect servers owned by agent (if no other agent uses them)."""
        owned_keys = self._agent_owned.pop(agent_id, set())
        for key in owned_keys:
            other_owners = any(key in keys for keys in self._agent_owned.values())
            if not other_owners and key in self._connections:
                client = self._connections.pop(key)
                self._connected_keys.discard(key)
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
        self._connected_keys.clear()

    def _cache_key(self, config: dict) -> str:
        return json.dumps(config, sort_keys=True)
