"""``chimera ferret mcp-server`` — run ferret as an MCP server on stdio.

Other agents (and IDEs that speak MCP) can connect to this server to
drive ferret as a tool host: every ``chimera ferret -p PROMPT`` flow
becomes one MCP ``tools/call`` invocation. The protocol is the standard
JSON-RPC 2.0 over newline-delimited stdin/stdout that Chimera's other
MCP servers (``chimera/mcp_servers/{search,review,testgen}_server.py``)
implement.

Tool surface
------------

* ``ferret_run`` — fire one ferret turn against ``prompt`` and return
  the assistant text. Mirrors ``chimera ferret -p`` but lives behind
  the MCP ``tools/call`` envelope.
* ``ferret_apply`` — apply the latest agent diff via ``git apply``.
  Mirrors ``chimera ferret apply``.

The handler is intentionally light: heavy lifting (provider resolution,
sandbox wrapping, approval policy) is reused via late-bound imports
from :mod:`chimera.ferret.cli` so the MCP path inherits every
ferret-flavored guarantee the ``-p`` path enforces.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = [
    "FerretMCPServer",
    "run_mcp_server",
]


SERVER_NAME = "chimera-ferret"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "ferret_run",
        "description": (
            "Run one ferret agent turn against PROMPT in the current "
            "working directory. Returns the final assistant text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Prompt to send to the ferret agent.",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Override the model id (default: ferret's own "
                        "default chain — gpt-5 → gpt-4o → ...)."
                    ),
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Max agent steps per turn (default: 50).",
                    "default": 50,
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "ferret_apply",
        "description": (
            "Apply the latest ferret-emitted unified diff via git apply. "
            "Set 'last' to true to restrict to the most-recent session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "last": {
                    "type": "boolean",
                    "description": "Search only the newest ferret session.",
                    "default": False,
                },
                "cwd": {
                    "type": "string",
                    "description": "Git working tree (default: process cwd).",
                },
            },
        },
    },
]


class FerretMCPServer:
    """MCP server exposing ferret as tools over stdio.

    Reads newline-delimited JSON-RPC 2.0 messages from ``stdin`` and
    writes responses to ``stdout``. The lifecycle mirrors the other
    Chimera MCP servers — initialize → tools/list → tools/call → ping.

    Args:
        args: The parsed ferret namespace. Used by ``ferret_run`` to
            inherit ``--cwd``, ``--max-steps``, ``--sandbox``,
            ``--approval``, ``--allowed-tools``, and ``--model``.
        stdin: Optional override for tests.
        stdout: Optional override for tests.
    """

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        stdin: Any = None,
        stdout: Any = None,
    ) -> None:
        self._args = args
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._initialized = False

    # ── JSON-RPC dispatch ──────────────────────────────────────────────

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Process one JSON-RPC message; return the response or ``None``."""
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        # Notifications carry no id — they never produce a response.
        if msg_id is None:
            return None

        handler = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }.get(method)

        if handler is None:
            return self._error_response(
                msg_id, -32601, f"Method not found: {method}"
            )

        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:  # noqa: BLE001
            return self._error_response(msg_id, -32603, str(exc))

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params  # MCP includes capabilities here; we don't need them.
        self._initialized = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {"tools": TOOL_DEFINITIONS}

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {}

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        if name == "ferret_run":
            return self._call_ferret_run(arguments)
        if name == "ferret_apply":
            return self._call_ferret_apply(arguments)
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }

    # ── Tool implementations ──────────────────────────────────────────

    def _call_ferret_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            return {
                "content": [
                    {"type": "text", "text": "Error: 'prompt' is required"}
                ],
                "isError": True,
            }
        # Build a per-call namespace that overlays MCP arguments onto
        # the cli args. Every ``-p`` knob the user already passed
        # (sandbox, approval, allowed-tools) is preserved.
        from chimera.ferret import cli as _cli

        ns = argparse.Namespace(**vars(self._args))
        ns.print_mode = prompt
        if "model" in arguments:
            ns.model = str(arguments["model"])
        if "max_steps" in arguments:
            try:
                ns.max_steps = int(arguments["max_steps"])
            except (TypeError, ValueError):
                pass
        ns.output_format = "json"
        ns.no_save = True

        # Capture the JSON envelope written by ``_run_print_mode``.
        import io

        buf = io.StringIO()
        prev_stdout = sys.stdout
        sys.stdout = buf
        try:
            rc = int(_cli._run_print_mode(ns))  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            sys.stdout = prev_stdout
            return {
                "content": [
                    {"type": "text", "text": f"Error: {exc}"},
                ],
                "isError": True,
            }
        finally:
            sys.stdout = prev_stdout

        raw = buf.getvalue().strip()
        text = raw or f"(no output, rc={rc})"
        return {
            "content": [{"type": "text", "text": text}],
            "isError": rc != 0,
        }

    def _call_ferret_apply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from chimera.ferret.subcommands.apply import run_apply

        ns = argparse.Namespace(**vars(self._args))
        ns.last = bool(arguments.get("last", False))
        if "cwd" in arguments:
            ns.cwd = str(arguments["cwd"])

        # Capture stderr where run_apply writes its summary line.
        import io

        prev_stderr = sys.stderr
        buf = io.StringIO()
        sys.stderr = buf
        try:
            rc = int(run_apply(ns))
        finally:
            sys.stderr = prev_stderr
        return {
            "content": [{"type": "text", "text": buf.getvalue() or f"rc={rc}"}],
            "isError": rc != 0,
        }

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _error_response(
        msg_id: int | str | None,
        code: int,
        message: str,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    # ── Stdio loop ────────────────────────────────────────────────────

    def run(self) -> int:
        """Run the JSON-RPC stdio loop until stdin closes.

        Returns:
            ``0`` on graceful EOF, ``1`` on an unrecoverable transport
            error.
        """
        for raw_line in self._stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                err = self._error_response(None, -32700, "Parse error")
                self._stdout.write(json.dumps(err) + "\n")
                self._stdout.flush()
                continue
            response = self.handle_message(message)
            if response is not None:
                self._stdout.write(json.dumps(response) + "\n")
                self._stdout.flush()
        return 0


def run_mcp_server(args: argparse.Namespace) -> int:
    """Entry point for ``chimera ferret mcp-server``."""
    server = FerretMCPServer(args)
    return int(server.run())
