# tests/test_search_mcp.py
"""Tests for the Chimera search MCP server."""
import json
import os
import tempfile

import pytest

from chimera.mcp_servers.search_server import SearchMCPServer, TOOL_DEFINITIONS
from chimera.tools.codebase_index import CodebaseIndex
from chimera.tools.definition_lookup import DefinitionFinder


class TestSearchMCPServer:
    """Tests for SearchMCPServer message handling."""

    def _make_server(self, tmp_path):
        """Create a server with a small indexed workspace."""
        # Create sample files
        (tmp_path / "main.py").write_text(
            "class Application:\n    def run(self):\n        pass\n"
        )
        (tmp_path / "utils.py").write_text(
            "def helper_function():\n    return 42\n"
        )
        (tmp_path / "config.py").write_text(
            "DATABASE_URL = 'sqlite:///db.sqlite'\n"
        )

        server = SearchMCPServer(workdir=str(tmp_path))
        server.index_workspace()
        return server

    def test_initialize(self, tmp_path):
        """Server responds to initialize with capabilities and server info."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        })

        assert response is not None
        assert response["id"] == 1
        result = response["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "chimera-search"

    def test_tools_list(self, tmp_path):
        """Server lists chimera_search and chimera_symbols tools."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })

        assert response is not None
        tools = response["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "chimera_search" in names
        assert "chimera_symbols" in names

        # Verify each tool has inputSchema
        for tool in tools:
            assert "inputSchema" in tool
            assert "properties" in tool["inputSchema"]

    def test_chimera_search(self, tmp_path):
        """chimera_search returns ranked results for a query."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "chimera_search",
                "arguments": {"query": "application run", "max_results": 5},
            },
        })

        assert response is not None
        result = response["result"]
        assert "content" in result
        assert len(result["content"]) > 0
        text = result["content"][0]["text"]
        assert "main.py" in text

    def test_chimera_symbols(self, tmp_path):
        """chimera_symbols finds class and function definitions."""
        server = self._make_server(tmp_path)

        # Search for a class
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "chimera_symbols",
                "arguments": {"name": "Application"},
            },
        })

        assert response is not None
        result = response["result"]
        text = result["content"][0]["text"]
        assert "Application" in text
        assert "class" in text
        assert "main.py" in text

    def test_chimera_symbols_not_found(self, tmp_path):
        """chimera_symbols returns a clear message when symbol is not found."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "chimera_symbols",
                "arguments": {"name": "NonExistentClass"},
            },
        })

        assert response is not None
        text = response["result"]["content"][0]["text"]
        assert "No definition found" in text

    def test_notification_returns_none(self, tmp_path):
        """Notifications (no id) return None."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert response is None

    def test_unknown_method_returns_error(self, tmp_path):
        """Unknown methods return a JSON-RPC error."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "unknown/method",
            "params": {},
        })

        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32601

    def test_ping(self, tmp_path):
        """Server responds to ping."""
        server = self._make_server(tmp_path)

        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "ping",
            "params": {},
        })

        assert response is not None
        assert "result" in response
        assert response["id"] == 7
