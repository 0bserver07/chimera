"""Tests for MCP config loading and lifetime fix."""
from __future__ import annotations

from chimera.mcp.tools import MCPToolSource, _ACTIVE_CLIENTS


class TestMCPToolSource:
    def test_from_config_stdio(self):
        # Just verify the method exists and accepts config format
        # Can't test actual connection without an MCP server
        config = {"servers": {}}
        client, tools = MCPToolSource.from_config(config)
        assert tools == []
        assert client is not None

    def test_from_config_empty(self):
        config = {}
        client, tools = MCPToolSource.from_config(config)
        assert tools == []

    def test_active_clients_list_exists(self):
        assert isinstance(_ACTIVE_CLIENTS, list)

    def test_from_config_with_server_defs(self):
        # Verify config parsing without connecting
        config = {
            "servers": {
                "test-stdio": {
                    "command": "echo",
                    "args": ["hello"],
                },
                "test-http": {
                    "url": "http://localhost:9999",
                },
            }
        }
        # This will fail to connect but should parse config correctly
        from chimera.mcp.client import MCPClient
        client = MCPClient()
        servers = config.get("servers", {})
        for name, sc in servers.items():
            if "command" in sc:
                client.add_stdio(name, sc["command"], sc.get("args"))
            elif "url" in sc:
                client.add_http(name, sc["url"])
        # Verify transports were registered
        assert "test-stdio" in client._transports
        assert "test-http" in client._transports
