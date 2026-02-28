"""MCP transport implementations -- stdio and HTTP."""
from __future__ import annotations

import json
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Any


class MCPTransport(ABC):
    """Abstract transport for MCP communication.

    Implements JSON-RPC 2.0 message exchange with an MCP server.
    """

    @abstractmethod
    def start(self) -> None:
        """Start the transport connection."""

    @abstractmethod
    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC message and return the response.

        Args:
            message: JSON-RPC request or notification.

        Returns:
            JSON-RPC response dict, or None for notifications.
        """

    @abstractmethod
    def close(self) -> None:
        """Close the transport connection."""

    def __enter__(self) -> MCPTransport:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class StdioTransport(MCPTransport):
    """Stdio transport -- communicates via stdin/stdout of a subprocess.

    Uses newline-delimited JSON (one JSON message per line), as specified
    by the MCP protocol for stdio transport.
    """

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._process = subprocess.Popen(
            [self._command] + self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
        )

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self._write_message(message)
            if "id" in message:
                return self._read_message()
            return None

    def close(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    def _write_message(self, msg: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        line = json.dumps(msg).encode("utf-8") + b"\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    def _read_message(self) -> dict[str, Any] | None:
        assert self._process is not None and self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            return None
        return json.loads(line)


class HTTPTransport(MCPTransport):
    """HTTP transport -- communicates via POST requests to an MCP endpoint."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        self._session_id: str | None = None

    def start(self) -> None:
        pass  # No persistent connection needed

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        import urllib.request
        import urllib.error

        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        data = json.dumps(message).encode("utf-8")
        req = urllib.request.Request(self._url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Capture session ID if present
                session_id = resp.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                response_data = resp.read().decode("utf-8")
                if response_data:
                    return json.loads(response_data)
        except urllib.error.HTTPError as e:
            raise ConnectionError(f"MCP HTTP error {e.code}: {e.reason}") from e

        return None

    def close(self) -> None:
        self._session_id = None
