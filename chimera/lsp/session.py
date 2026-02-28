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
    A background reader thread processes all incoming messages,
    routing responses to pending requests and caching notifications
    (e.g. ``textDocument/publishDiagnostics``).
    """

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False
        # Background reader state
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, dict[str, Any]] = {}
        self._reader_thread: threading.Thread | None = None
        self._diagnostics_cache: dict[str, list[Diagnostic]] = {}

    def start(self, root_path: str) -> None:
        """Start the language server and initialize it."""
        self._process = subprocess.Popen(
            self._command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # Start background reader before sending any requests
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True,
        )
        self._reader_thread.start()

        self._send_request("initialize", {
            "processId": None,
            "rootUri": Path(root_path).as_uri(),
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False},
                    "documentSymbol": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
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

    @property
    def cached_diagnostics(self) -> dict[str, list[Diagnostic]]:
        """Return diagnostics cache (URI -> diagnostics)."""
        return dict(self._diagnostics_cache)

    def get_diagnostics(self, uri: str) -> list[Diagnostic]:
        """Return cached diagnostics for a given URI."""
        return list(self._diagnostics_cache.get(uri, []))

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

    def completion(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Get completions at a position."""
        result = self._send_request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return []
        items = result.get("result", [])
        if isinstance(items, dict):
            items = items.get("items", [])
        return items or []

    def rename(self, uri: str, line: int, character: int, new_name: str) -> dict[str, Any] | None:
        """Rename a symbol at a position."""
        result = self._send_request("textDocument/rename", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "newName": new_name,
        })
        if result is None:
            return None
        return result.get("result")

    def code_action(self, uri: str, start_line: int, start_char: int,
                    end_line: int, end_char: int) -> list[dict[str, Any]]:
        """Get code actions for a range."""
        result = self._send_request("textDocument/codeAction", {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": start_line, "character": start_char},
                "end": {"line": end_line, "character": end_char},
            },
            "context": {"diagnostics": []},
        })
        if result is None:
            return []
        return result.get("result", []) or []

    # ---- JSON-RPC helpers ----

    def _send_request(self, method: str, params: Any) -> dict[str, Any] | None:
        self._request_id += 1
        req_id = self._request_id
        event = threading.Event()
        self._pending[req_id] = event
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self._write(msg)
        # Wait for response (background reader will set the event)
        event.wait(timeout=30)
        self._pending.pop(req_id, None)
        return self._responses.pop(req_id, None)

    def _send_notification(self, method: str, params: Any) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write(msg)

    def _write(self, msg: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        self._process.stdin.flush()

    def _read_one(self) -> dict[str, Any] | None:
        """Read a single Content-Length framed message from stdout."""
        assert self._process is not None and self._process.stdout is not None
        content_length = None
        while True:
            line = self._process.stdout.readline()
            if not line:
                return None  # EOF
            if line.strip() == b"":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
        if content_length is None:
            return None
        body = self._process.stdout.read(content_length)
        return json.loads(body)

    def _read_loop(self) -> None:
        """Background thread: read messages, route responses and notifications."""
        while self._process is not None:
            try:
                msg = self._read_one()
            except (ValueError, OSError):
                break
            if msg is None:
                break

            if "id" in msg and "method" not in msg:
                # Response to a request
                req_id = msg["id"]
                self._responses[req_id] = msg
                event = self._pending.get(req_id)
                if event:
                    event.set()
            elif msg.get("method") == "textDocument/publishDiagnostics":
                self._handle_diagnostics(msg.get("params", {}))
            # Other notifications silently ignored

    def _handle_diagnostics(self, params: dict[str, Any]) -> None:
        """Cache diagnostics from a publishDiagnostics notification."""
        uri = params.get("uri", "")
        raw_diags = params.get("diagnostics", [])
        parsed: list[Diagnostic] = []
        for d in raw_diags:
            rng = d.get("range", {}).get("start", {})
            sev_num = d.get("severity", 1)
            try:
                sev = Severity(sev_num)
            except ValueError:
                sev = Severity.ERROR
            parsed.append(Diagnostic(
                file=uri,
                line=rng.get("line", 0),
                column=rng.get("character", 0),
                severity=sev,
                message=d.get("message", ""),
                source=d.get("source"),
                code=str(d["code"]) if "code" in d else None,
            ))
        self._diagnostics_cache[uri] = parsed
