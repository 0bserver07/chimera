#!/usr/bin/env python3
"""MCP server exposing Chimera's CodebaseIndex as tools for Claude Code.

Implements the MCP stdio protocol (JSON-RPC 2.0 over stdin/stdout) and
exposes two tools:

- ``chimera_search(query, max_results)`` — TF-IDF ranked file search
- ``chimera_symbols(name)`` — find classes/functions/methods by name

The server auto-indexes the current working directory on startup.

Usage::

    python -m chimera.mcp_servers.search_server
    # or
    python chimera/mcp_servers/search_server.py

Configure in ``.mcp.json`` for Claude Code::

    {
      "mcpServers": {
        "chimera-search": {
          "command": "python3",
          "args": ["chimera/mcp_servers/search_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from chimera.tools.codebase_index import CodebaseIndex
from chimera.tools.definition_lookup import DefinitionFinder

# ── Server metadata ──────────────────────────────────────────────────

SERVER_NAME = "chimera-search"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


# ── Tool definitions ─────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "chimera_search",
        "description": (
            "Search the codebase for files related to a concept or keyword. "
            "Returns ranked file paths with TF-IDF relevance scores."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (natural language or keywords).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "chimera_symbols",
        "description": (
            "Find where a function, class, method, or variable is defined "
            "in the codebase. Returns file path, line number, kind, and "
            "source snippet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name to search for (function, class, variable).",
                },
            },
            "required": ["name"],
        },
    },
]


# ── MCP Stdio Server ─────────────────────────────────────────────────

class SearchMCPServer:
    """MCP server that exposes codebase search and symbol lookup.

    Reads JSON-RPC messages from stdin (newline-delimited) and writes
    responses to stdout.

    Args:
        workdir: Directory to index. Defaults to cwd.
        index: Optional pre-built CodebaseIndex (useful for testing).
        finder: Optional pre-built DefinitionFinder (useful for testing).
    """

    def __init__(
        self,
        workdir: str | None = None,
        index: CodebaseIndex | None = None,
        finder: DefinitionFinder | None = None,
    ) -> None:
        self._workdir = workdir or os.getcwd()
        self._index = index or CodebaseIndex()
        self._finder = finder or DefinitionFinder(self._workdir)
        self._initialized = False
        self._indexed = index is not None  # skip indexing if pre-built

    def index_workspace(self) -> int:
        """Index the workspace directory.

        Returns:
            Number of files indexed.
        """
        if self._indexed:
            return self._index.file_count
        count = self._index.index_directory(self._workdir)
        self._indexed = True
        return count

    # ── JSON-RPC dispatch ─────────────────────────────────────────────

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC message.

        Args:
            message: Parsed JSON-RPC request or notification.

        Returns:
            JSON-RPC response dict, or None for notifications.
        """
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        # Notifications (no id) — no response required
        if msg_id is None:
            return None

        handler = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }.get(method)

        if handler is None:
            return self._error_response(msg_id, -32601, f"Method not found: {method}")

        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            return self._error_response(msg_id, -32603, str(e))

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle the initialize request."""
        self._initialized = True
        # Index workspace on first connection
        if not self._indexed:
            self.index_workspace()
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request."""
        return {"tools": TOOL_DEFINITIONS}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "chimera_search":
            return self._call_search(arguments)
        elif tool_name == "chimera_symbols":
            return self._call_symbols(arguments)
        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ping request."""
        return {}

    # ── Tool implementations ──────────────────────────────────────────

    def _call_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_search tool.

        Args:
            arguments: Must contain ``query``; may contain ``max_results``.

        Returns:
            MCP content response with ranked results.
        """
        query = arguments.get("query", "")
        max_results = arguments.get("max_results", 10)

        if not query:
            return {
                "content": [{"type": "text", "text": "Error: query is required"}],
                "isError": True,
            }

        # Ensure index is built
        if not self._indexed:
            self.index_workspace()

        results = self._index.search(query, max_results=max_results)

        if not results:
            return {
                "content": [{"type": "text", "text": f"No files found matching: {query}"}],
            }

        lines: list[str] = [f"Found {len(results)} result(s) for '{query}':\n"]
        for r in results:
            lines.append(f"  {r.score:.3f}  {r.path}")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _call_symbols(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_symbols tool.

        Args:
            arguments: Must contain ``name``.

        Returns:
            MCP content response with symbol definitions.
        """
        name = arguments.get("name", "")

        if not name:
            return {
                "content": [{"type": "text", "text": "Error: name is required"}],
                "isError": True,
            }

        definitions = self._finder.find(name)

        if not definitions:
            return {
                "content": [{"type": "text", "text": f"No definition found for '{name}'"}],
            }

        lines = [f"Found {len(definitions)} definition(s) for '{name}':\n"]
        for d in definitions[:20]:  # Limit output
            lines.append(f"  {d.file}:{d.line} ({d.kind})")
            lines.append(f"    {d.source[:200]}")
            lines.append("")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _error_response(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
        """Build a JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    # ── Stdio loop ────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the MCP server, reading from stdin and writing to stdout.

        Reads newline-delimited JSON messages from stdin and writes
        responses as newline-delimited JSON to stdout.  Runs until
        stdin is closed.
        """
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Invalid JSON — send parse error
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
    """Entry point for the MCP search server."""
    server = SearchMCPServer()
    server.run()


if __name__ == "__main__":
    main()
