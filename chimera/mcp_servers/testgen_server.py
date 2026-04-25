#!/usr/bin/env python3
"""MCP server wrapping Chimera's TestGenerator.

Exposes two tools:

- ``chimera_testgen(file_path)`` -- analyze a Python source file and return
  test case skeletons.
- ``chimera_coverage_gaps(file_path)`` -- identify public functions/methods
  in a source file that do not have corresponding test functions.

Usage::

    python -m chimera.mcp_servers.testgen_server
    # or
    python chimera/mcp_servers/testgen_server.py

Configure in ``.mcp.json`` for any compatible MCP host::

    {
      "mcpServers": {
        "chimera-testgen": {
          "command": "python3",
          "args": ["chimera/mcp_servers/testgen_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

from chimera.testgen.generator import TestGenerator

__all__ = ["TestgenMCPServer", "find_coverage_gaps"]


# -- Server metadata -------------------------------------------------------

SERVER_NAME = "chimera-testgen"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


# -- Tool definitions ------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "chimera_testgen",
        "description": (
            "Analyze a Python source file and generate test case skeletons "
            "for all public functions and methods."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to a Python source file.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "chimera_coverage_gaps",
        "description": (
            "Identify public functions and methods in a Python source file "
            "that do not have corresponding test functions in the project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to a Python source file.",
                },
            },
            "required": ["file_path"],
        },
    },
]


# -- Coverage gap analysis --------------------------------------------------

def find_coverage_gaps(
    source: str,
    test_source: str | None = None,
    filepath: str = "<unknown>",
) -> list[dict[str, Any]]:
    """Identify public functions/methods without test coverage.

    Scans the source code for public function and method definitions,
    then checks whether ``test_source`` contains a corresponding
    ``test_<name>`` function.

    Args:
        source: Python source code to analyse.
        test_source: Content of the related test file.  If ``None``,
            all public functions are reported as untested.
        filepath: File path for display purposes.

    Returns:
        List of dicts with keys ``name``, ``line``, ``kind``, ``file``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    test_names: set[str] = set()
    if test_source:
        try:
            test_tree = ast.parse(test_source)
            for node in ast.walk(test_tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    test_names.add(node.name)
        except SyntaxError:
            pass

    gaps: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            expected_test = f"test_{node.name}"
            if expected_test not in test_names:
                gaps.append({
                    "name": node.name,
                    "line": node.lineno,
                    "kind": "function",
                    "file": filepath,
                })
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    expected_test = f"test_{node.name}_{item.name}"
                    if expected_test not in test_names:
                        gaps.append({
                            "name": f"{node.name}.{item.name}",
                            "line": item.lineno,
                            "kind": "method",
                            "file": filepath,
                        })

    return gaps


def _find_test_file(file_path: str) -> str | None:
    """Attempt to locate the test file for a given source file.

    Args:
        file_path: Path to the source file.

    Returns:
        Path to the test file if found, else ``None``.
    """
    p = Path(file_path)
    stem = p.stem

    candidates = [
        p.parent / f"test_{stem}.py",
        p.parent / "tests" / f"test_{stem}.py",
        p.parent.parent / "tests" / f"test_{stem}.py",
    ]

    # Also look from project root
    cwd = Path.cwd()
    candidates.extend([
        cwd / "tests" / f"test_{stem}.py",
        cwd / f"test_{stem}.py",
    ])

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return None


# -- MCP server --------------------------------------------------------------

class TestgenMCPServer:
    """MCP server wrapping Chimera's TestGenerator.

    Reads JSON-RPC messages from stdin (newline-delimited) and writes
    responses to stdout.

    Args:
        generator: Optional :class:`~chimera.testgen.generator.TestGenerator`
            instance.  A new one is created if not provided.
    """

    # Prevent pytest from collecting this class as a test class.
    __test__ = False

    def __init__(
        self,
        generator: TestGenerator | None = None,
    ) -> None:
        self._generator = generator or TestGenerator()
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
        arguments = params.get("arguments", {})

        if tool_name == "chimera_testgen":
            return self._call_testgen(arguments)
        elif tool_name == "chimera_coverage_gaps":
            return self._call_coverage_gaps(arguments)
        else:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True,
            }

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    # -- Tool implementations ------------------------------------------------

    def _call_testgen(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_testgen tool.

        Args:
            arguments: Must contain ``file_path``.

        Returns:
            MCP content response with test skeletons.
        """
        file_path = arguments.get("file_path", "")

        if not file_path:
            return {
                "content": [{"type": "text", "text": "Error: file_path is required"}],
                "isError": True,
            }

        try:
            source = Path(file_path).read_text()
        except (FileNotFoundError, OSError) as e:
            return {
                "content": [{"type": "text", "text": f"Error reading file: {e}"}],
                "isError": True,
            }

        cases = self._generator.analyze_source(source, file_path)

        if not cases:
            return {
                "content": [{"type": "text", "text": f"No testable functions found in {file_path}"}],
            }

        lines = [f"Generated {len(cases)} test case(s) for {file_path}:\n"]
        for case in cases:
            lines.append(f"# {case.name} ({case.category})")
            lines.append(case.test_code)
            lines.append("")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
        }

    def _call_coverage_gaps(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute chimera_coverage_gaps tool.

        Args:
            arguments: Must contain ``file_path``.

        Returns:
            MCP content response with untested function list.
        """
        file_path = arguments.get("file_path", "")

        if not file_path:
            return {
                "content": [{"type": "text", "text": "Error: file_path is required"}],
                "isError": True,
            }

        try:
            source = Path(file_path).read_text()
        except (FileNotFoundError, OSError) as e:
            return {
                "content": [{"type": "text", "text": f"Error reading file: {e}"}],
                "isError": True,
            }

        # Try to find the test file
        test_file = _find_test_file(file_path)
        test_source = None
        if test_file:
            try:
                test_source = Path(test_file).read_text()
            except OSError:
                pass

        gaps = find_coverage_gaps(source, test_source, file_path)

        if not gaps:
            return {
                "content": [{"type": "text", "text": f"All public functions in {file_path} have test coverage."}],
            }

        lines = [f"Found {len(gaps)} function(s) without test coverage in {file_path}:\n"]
        for gap in gaps:
            lines.append(f"  line {gap['line']}: {gap['name']} ({gap['kind']})")

        if test_file:
            lines.append(f"\nTest file: {test_file}")
        else:
            lines.append(f"\nNo test file found for {Path(file_path).name}")

        return {
            "content": [{"type": "text", "text": "\n".join(lines)}],
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
    """Entry point for the MCP testgen server."""
    server = TestgenMCPServer()
    server.run()


if __name__ == "__main__":
    main()
