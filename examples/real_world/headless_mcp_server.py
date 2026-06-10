#!/usr/bin/env python3
"""Unified Headless MCP Server Gateway for Chimera.

This script aggregates Chimera's built-in MCP servers (Testgen, Migration,
Search, and Review) into a single standard-compliant JSON-RPC stdio gateway.
It allows external IDEs (like Cursor, VS Code Cline, or Claude Desktop) to
connect to a single endpoint and access all of Chimera's specialized tools.

Usage:
    python examples/real_world/headless_mcp_server.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

# Ensure chimera is in the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from chimera.mcp_servers.testgen_server import TestgenMCPServer, TOOL_DEFINITIONS as TESTGEN_TOOLS
from chimera.mcp_servers.migration_server import MigrationMCPServer, TOOL_DEFINITIONS as MIGRATION_TOOLS
from chimera.mcp_servers.search_server import SearchMCPServer, TOOL_DEFINITIONS as SEARCH_TOOLS
from chimera.mcp_servers.review_server import ReviewMCPServer, TOOL_DEFINITIONS as REVIEW_TOOLS

class UnifiedMCPServer:
    def __init__(self) -> None:
        self.testgen_server = TestgenMCPServer()
        self.migration_server = MigrationMCPServer()
        self.search_server = SearchMCPServer()
        self.review_server = ReviewMCPServer()
        
        # Build tool routing map
        self._tool_handlers: dict[str, Any] = {}
        for tool in TESTGEN_TOOLS:
            self._tool_handlers[tool["name"]] = self.testgen_server
        for tool in MIGRATION_TOOLS:
            self._tool_handlers[tool["name"]] = self.migration_server
        for tool in SEARCH_TOOLS:
            self._tool_handlers[tool["name"]] = self.search_server
        for tool in REVIEW_TOOLS:
            self._tool_handlers[tool["name"]] = self.review_server
            
        self._all_tools = TESTGEN_TOOLS + MIGRATION_TOOLS + SEARCH_TOOLS + REVIEW_TOOLS
        self._search_indexed = False

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        if msg_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "chimera-unified-mcp", "version": "0.1.0"},
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": self._all_tools}
            }

        elif method == "tools/call":
            tool_name = params.get("name", "")
            
            # Auto-index search if a search tool is called
            if tool_name in ("chimera_search", "chimera_symbols") and not self._search_indexed:
                self.search_server.index_workspace()
                self._search_indexed = True

            handler_server = self._tool_handlers.get(tool_name)
            if handler_server:
                # Delegate the entire JSON-RPC message to the sub-server
                return handler_server.handle_message(message)  # type: ignore[no-any-return]
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
                }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }

    def run(self) -> None:
        """Run the MCP server, reading from stdin and writing to stdout."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                error = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
                sys.stdout.write(json.dumps(error) + "\n")
                sys.stdout.flush()
                continue

            response = self.handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

def main() -> None:
    server = UnifiedMCPServer()
    server.run()

if __name__ == "__main__":
    main()
