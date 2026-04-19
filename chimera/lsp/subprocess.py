"""LSP client that communicates with a language server over stdin/stdout."""
from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from chimera.lsp.base import Diagnostic, LSPClient, Severity


class SubprocessLSPClient(LSPClient):
    """LSP client using JSON-RPC over stdin/stdout of a subprocess.

    Parameters
    ----------
    command:
        The command to start the language server (e.g. ``["pyright-langserver", "--stdio"]``).
    """

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()

    def initialize(self, root_path: str) -> None:
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._send_request("initialize", {
            "processId": None,
            "rootUri": f"file://{root_path}",
            "capabilities": {},
        })
        self._send_notification("initialized", {})

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        # Request diagnostics by opening the file
        uri = f"file://{file_path}"
        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": "python",
                "version": 1,
                "text": "",  # Server should read from disk
            },
        })
        # Read notifications until we get publishDiagnostics
        result: list[Diagnostic] = []
        response = self._read_response()
        if response and response.get("method") == "textDocument/publishDiagnostics":
            for diag in response.get("params", {}).get("diagnostics", []):
                severity_val = diag.get("severity", 1)
                try:
                    severity = Severity(severity_val)
                except ValueError:
                    severity = Severity.ERROR
                rng = diag.get("range", {}).get("start", {})
                result.append(Diagnostic(
                    file=file_path,
                    line=rng.get("line", 0) + 1,  # LSP is 0-indexed
                    column=rng.get("character", 0) + 1,
                    severity=severity,
                    message=diag.get("message", ""),
                    source=diag.get("source"),
                    code=diag.get("code"),
                ))
        return result

    def shutdown(self) -> None:
        if self._process is not None:
            try:
                self._send_request("shutdown", None)
                self._send_notification("exit", None)
            except (BrokenPipeError, OSError):
                pass
            self._process.terminate()
            self._process.wait(timeout=5)
            self._process = None

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: Any) -> dict[str, Any]:
        with self._lock:
            self._request_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            self._write_message(msg)
            return self._read_response() or {}

    def _send_notification(self, method: str, params: Any) -> None:
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_message(msg)

    def _write_message(self, msg: dict[str, Any]) -> None:
        assert self._process is not None
        assert self._process.stdin is not None
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    def _read_response(self) -> dict[str, Any] | None:
        assert self._process is not None
        assert self._process.stdout is not None
        # Read Content-Length header
        header_line = self._process.stdout.readline()
        if not header_line:
            return None
        # Skip until empty line
        while True:
            line = self._process.stdout.readline()
            if line.strip() == b"":
                break
        # Parse Content-Length
        try:
            length = int(header_line.split(b":")[1].strip())
        except (IndexError, ValueError):
            return None
        body = self._process.stdout.read(length)
        parsed: dict[str, Any] = json.loads(body)
        return parsed
