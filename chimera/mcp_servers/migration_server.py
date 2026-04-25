#!/usr/bin/env python3
"""MCP server wrapping Chimera's MigrationPlanner.

Exposes three tools:

- ``chimera_migration_scan(files, preset)`` -- scan files for migration
  opportunities using a preset.
- ``chimera_migration_apply(files, preset)`` -- apply a preset migration
  to files and return the transformed results.
- ``chimera_migration_presets()`` -- list available migration presets with
  descriptions.

Usage::

    python -m chimera.mcp_servers.migration_server
    # or
    python chimera/mcp_servers/migration_server.py

Configure in ``.mcp.json`` for any compatible MCP host::

    {
      "mcpServers": {
        "chimera-migration": {
          "command": "python3",
          "args": ["chimera/mcp_servers/migration_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import sys
from typing import Any

from chimera.migration.planner import MigrationPlanner

__all__ = ["MigrationMCPServer"]


# -- Server metadata -------------------------------------------------------

SERVER_NAME = "chimera-migration"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


# -- Preset descriptions ---------------------------------------------------

PRESET_DESCRIPTIONS: dict[str, str] = {
    "python2-to-3": (
        "Convert Python 2 idioms to Python 3: print statements to "
        "functions, raw_input to input, xrange to range."
    ),
    "commonjs-to-esm": (
        "Convert CommonJS modules to ES modules: require() to import, "
        "module.exports to export default."
    ),
}


# -- Tool definitions ------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "chimera_migration_scan",
        "description": (
            "Scan files for migration opportunities using a preset rule set. "
            "Returns which rules match which files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "object",
                    "description": (
                        "Mapping of file paths to file contents, "
                        "e.g. {\"app.py\": \"print 'hello'\"}."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "preset": {
                    "type": "string",
                    "description": (
                        "Migration preset name, e.g. \"python2-to-3\" "
                        "or \"commonjs-to-esm\"."
                    ),
                },
            },
            "required": ["files", "preset"],
        },
    },
    {
        "name": "chimera_migration_apply",
        "description": (
            "Apply a preset migration to files and return the transformed "
            "file contents."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "object",
                    "description": (
                        "Mapping of file paths to file contents, "
                        "e.g. {\"app.py\": \"print 'hello'\"}."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "preset": {
                    "type": "string",
                    "description": (
                        "Migration preset name, e.g. \"python2-to-3\" "
                        "or \"commonjs-to-esm\"."
                    ),
                },
            },
            "required": ["files", "preset"],
        },
    },
    {
        "name": "chimera_migration_presets",
        "description": "List available migration presets with descriptions.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# -- MCP server --------------------------------------------------------------

class MigrationMCPServer:
    """MCP server wrapping Chimera's MigrationPlanner.

    Reads JSON-RPC messages from stdin (newline-delimited) and writes
    responses to stdout.
    """

    def __init__(self) -> None:
        self._initialized = False

    # -- JSON-RPC dispatch ---------------------------------------------------

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
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": TOOL_DEFINITIONS}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments: dict[str, Any] = params.get("arguments", {})

        if tool_name == "chimera_migration_scan":
            return self._call_scan(arguments)
        elif tool_name == "chimera_migration_apply":
            return self._call_apply(arguments)
        elif tool_name == "chimera_migration_presets":
            return self._call_presets(arguments)
        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    # -- Tool implementations ------------------------------------------------

    def _call_scan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        files = arguments.get("files")
        preset = arguments.get("preset", "")

        if not files:
            return {
                "content": [{"type": "text", "text": "Error: files is required"}],
                "isError": True,
            }

        if not preset:
            return {
                "content": [{"type": "text", "text": "Error: preset is required"}],
                "isError": True,
            }

        try:
            planner = MigrationPlanner.from_preset(preset)
        except ValueError as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            }

        scan_results = planner.scan(files)

        if not scan_results:
            return {
                "content": [{"type": "text", "text": f"No migration opportunities found for preset '{preset}'."}],
            }

        lines = [f"Migration scan results for preset '{preset}':\n"]
        for path, matches in scan_results.items():
            lines.append(f"  {path}:")
            for match in matches:
                lines.append(f"    - {match}")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _call_apply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        files = arguments.get("files")
        preset = arguments.get("preset", "")

        if not files:
            return {
                "content": [{"type": "text", "text": "Error: files is required"}],
                "isError": True,
            }

        if not preset:
            return {
                "content": [{"type": "text", "text": "Error: preset is required"}],
                "isError": True,
            }

        try:
            planner = MigrationPlanner.from_preset(preset)
        except ValueError as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            }

        transformed = planner.apply(files)

        lines = [f"Migration applied using preset '{preset}':\n"]
        for path, content in transformed.items():
            lines.append(f"--- {path} ---")
            lines.append(content)

        return {
            "content": [
                {"type": "text", "text": "\n".join(lines)},
                {"type": "text", "text": json.dumps(transformed)},
            ],
        }

    def _call_presets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        presets = []
        for name in sorted(MigrationPlanner._PRESETS):
            desc = PRESET_DESCRIPTIONS.get(name, "No description available.")
            rule_count = len(MigrationPlanner._PRESETS[name])
            presets.append(f"  {name} ({rule_count} rules): {desc}")

        text = "Available migration presets:\n\n" + "\n".join(presets)
        return {
            "content": [{"type": "text", "text": text}],
        }

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _error_response(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    # -- Stdio loop ----------------------------------------------------------

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
    """Entry point for the MCP migration server."""
    server = MigrationMCPServer()
    server.run()


if __name__ == "__main__":
    main()
