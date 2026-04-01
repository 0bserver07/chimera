from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any

@dataclass
class MCPServerLifecycle:
    """Manage MCP server connections with memoization and lifecycle control."""

    _connections: dict[str, Any] = field(default_factory=dict)
    _agent_owned: dict[str, set[str]] = field(default_factory=dict)

    async def connect(self, config: dict) -> Any:
        """Connect to MCP server. Memoized — same config reuses connection."""
        key = self._cache_key(config)
        if key not in self._connections:
            # Placeholder: actual MCP client creation would go here
            self._connections[key] = {"config": config, "connected": True}
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
                del self._connections[key]

    async def cleanup_all(self) -> None:
        """Disconnect all. Called at session end."""
        self._connections.clear()
        self._agent_owned.clear()

    def _cache_key(self, config: dict) -> str:
        return json.dumps(config, sort_keys=True)
