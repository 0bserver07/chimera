"""SSE (Server-Sent Events) transport for MCP servers.

Implements the MCP "sse" transport: the client opens a long-lived GET to
an SSE endpoint and reads server-pushed JSON-RPC frames as ``data:`` lines.
JSON-RPC requests from the client are sent over a companion HTTP POST to
the same URL (or to the URL advertised in the SSE ``endpoint`` event, when
the server emits one, per the MCP 2024-11-05 spec).

Only the standard library is required. Network I/O is blocking (matching
the existing :class:`~chimera.mcp.transport.HTTPTransport` style); a
background reader thread parses SSE frames and routes responses by
JSON-RPC id.
"""
from __future__ import annotations

import json
import queue
import threading
import urllib.parse
import urllib.request
from typing import Any

from chimera.mcp.transport import MCPTransport


class SSETransport(MCPTransport):
    """JSON-RPC over Server-Sent Events.

    Args:
        url: Base URL of the SSE endpoint (e.g. ``https://host/mcp/sse``).
        headers: Optional HTTP headers (auth, etc.).
        timeout: Per-request timeout in seconds for POSTs.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._url = url
        self._post_url: str | None = None
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._stream: Any = None
        self._reader_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pending: dict[int | str, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._endpoint_ready = threading.Event()

    # ---- lifecycle -------------------------------------------------

    def start(self) -> None:
        """Open the SSE stream and begin reading events in a background thread."""
        req = urllib.request.Request(
            self._url,
            headers={"Accept": "text/event-stream", **self._headers},
            method="GET",
        )
        self._stream = urllib.request.urlopen(req, timeout=self._timeout)
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True
        )
        self._reader_thread.start()
        # Wait briefly for the server to advertise its POST endpoint via
        # an ``event: endpoint`` frame. If none arrives, fall back to the
        # original URL.
        self._endpoint_ready.wait(timeout=2.0)
        if self._post_url is None:
            self._post_url = self._url

    def close(self) -> None:
        """Stop the reader thread and close the stream."""
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    # ---- send/recv -------------------------------------------------

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC message over POST; return the matched response.

        Notifications (no ``id``) return ``None`` immediately.
        Requests block until the SSE stream delivers a frame whose
        ``id`` matches.
        """
        msg_id = message.get("id")
        post_url = self._post_url or self._url
        body = json.dumps(message).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._headers}

        q: queue.Queue[dict[str, Any]] | None = None
        if msg_id is not None:
            q = queue.Queue(maxsize=1)
            with self._pending_lock:
                self._pending[msg_id] = q

        req = urllib.request.Request(
            post_url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                # Some servers reply inline (HTTP 200 with JSON body);
                # others ack with 202 and stream the response on SSE.
                ctype = resp.headers.get("Content-Type", "")
                payload = resp.read()
                if payload and "json" in ctype:
                    parsed: dict[str, Any] = json.loads(payload.decode("utf-8"))
                    if msg_id is not None:
                        with self._pending_lock:
                            self._pending.pop(msg_id, None)
                    return parsed
        except Exception as exc:
            if msg_id is not None:
                with self._pending_lock:
                    self._pending.pop(msg_id, None)
            raise ConnectionError(f"MCP SSE POST failed: {exc}") from exc

        if msg_id is None or q is None:
            return None

        try:
            return q.get(timeout=self._timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            raise TimeoutError(
                f"No SSE response for request id={msg_id} within {self._timeout}s"
            )

    # ---- internal --------------------------------------------------

    def _read_loop(self) -> None:
        """Iterate SSE frames; dispatch JSON-RPC payloads to waiters."""
        event = ""
        data_buf: list[str] = []
        try:
            for raw in self._stream:
                if self._stop.is_set():
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    if data_buf:
                        self._dispatch(event, "\n".join(data_buf))
                    event = ""
                    data_buf = []
                    continue
                if line.startswith(":"):
                    continue  # comment / heartbeat
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_buf.append(line[5:].lstrip())
        except Exception:
            return

    def _dispatch(self, event: str, data: str) -> None:
        """Route an SSE frame: ``endpoint`` updates POST URL; others are JSON-RPC."""
        if event == "endpoint":
            # Server tells us where to POST. Resolve relative to base.
            self._post_url = urllib.parse.urljoin(self._url, data.strip())
            self._endpoint_ready.set()
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        msg_id = payload.get("id")
        if msg_id is None:
            return  # server-initiated notification; ignored for now
        with self._pending_lock:
            q = self._pending.pop(msg_id, None)
        if q is not None:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass
