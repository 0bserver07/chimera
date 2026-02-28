"""LSP session -- single language server connection over stdio."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from chimera.lsp.base import Diagnostic, Severity


class LSPSession:
    """A single LSP server connection over stdin/stdout.

    Handles JSON-RPC communication with Content-Length framing.
    Supports: initialize, didOpen, didChange, didSave,
    definition, references, hover, documentSymbol.
    """

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False

    def start(self, root_path: str) -> None:
        """Start the language server and initialize it."""
        self._process = subprocess.Popen(
            self._command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._send_request("initialize", {
            "processId": None,
            "rootUri": Path(root_path).as_uri(),
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False},
                    "documentSymbol": {"dynamicRegistration": False},
                },
            },
        })
        self._send_notification("initialized", {})
        self._initialized = True

    def stop(self) -> None:
        """Shut down the language server."""
        if self._process is not None:
            try:
                self._send_request("shutdown", None)
                self._send_notification("exit", None)
            except (BrokenPipeError, OSError):
                pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            self._initialized = False

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Notify server that a file was opened."""
        self._send_notification("textDocument/didOpen", {
            "textDocument": {"uri": uri, "languageId": language_id, "version": 1, "text": text},
        })

    def did_change(self, uri: str, text: str, version: int = 2) -> None:
        """Notify server of file content change."""
        self._send_notification("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}],
        })

    def definition(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Go to definition."""
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return []
        locations = result.get("result", [])
        if isinstance(locations, dict):
            return [locations]
        return locations or []

    def references(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Find all references."""
        result = self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        })
        if result is None:
            return []
        return result.get("result", []) or []

    def hover(self, uri: str, line: int, character: int) -> str | None:
        """Get hover information."""
        result = self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return None
        hover_result = result.get("result")
        if hover_result is None:
            return None
        contents = hover_result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", str(contents))
        if isinstance(contents, list):
            return "\n".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
        return str(contents)

    def document_symbols(self, uri: str) -> list[dict[str, Any]]:
        """Get document symbols."""
        result = self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        if result is None:
            return []
        return result.get("result", []) or []

    # ---- JSON-RPC helpers ----

    def _send_request(self, method: str, params: Any) -> dict[str, Any] | None:
        with self._lock:
            self._request_id += 1
            msg = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
            self._write(msg)
            return self._read()

    def _send_notification(self, method: str, params: Any) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write(msg)

    def _write(self, msg: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    def _read(self) -> dict[str, Any] | None:
        assert self._process is not None and self._process.stdout is not None
        content_length = None
        while True:
            line = self._process.stdout.readline()
            if not line or line.strip() == b"":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
        if content_length is None:
            return None
        body = self._process.stdout.read(content_length)
        return json.loads(body)
