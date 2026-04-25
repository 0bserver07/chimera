"""Tests for SSETransport: SSE event parsing + JSON-RPC framing."""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from chimera.mcp.sse_transport import SSETransport


class _StubSSEServer:
    """Minimal HTTP/1.1 server: accepts a GET (SSE) and a POST (JSON-RPC).

    On GET, emits an ``endpoint`` event (pointing at ``/post``), then
    keeps the stream open. When the test calls ``push_response``, the
    server sends an SSE ``data:`` frame containing the JSON-RPC response.
    On POST to ``/post``, the server replies 202 and pushes the
    canned response keyed by ``id``.
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/sse"
        self._stream_conn: socket.socket | None = None
        self._stream_lock = threading.Lock()
        self._stop = threading.Event()
        self.received_posts: list[dict] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            data = b""
            conn.settimeout(2.0)
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            request_line = head.split(b"\r\n", 1)[0].decode()
            method, path, _ = request_line.split(" ", 2)
            if method == "GET" and path == "/sse":
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/event-stream\r\n"
                    b"Cache-Control: no-cache\r\n"
                    b"Connection: keep-alive\r\n\r\n"
                )
                # Tell the client where to POST.
                conn.sendall(b"event: endpoint\r\ndata: /post\r\n\r\n")
                with self._stream_lock:
                    self._stream_conn = conn
                # Keep the connection open until shutdown.
                while not self._stop.is_set():
                    time.sleep(0.05)
                return
            if method == "POST" and path == "/post":
                # Read body length from headers.
                length = 0
                for line in head.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1].strip())
                body = rest
                while len(body) < length:
                    body += conn.recv(length - len(body))
                msg = json.loads(body.decode())
                self.received_posts.append(msg)
                conn.sendall(b"HTTP/1.1 202 Accepted\r\n"
                             b"Content-Length: 0\r\n\r\n")
                # Push response on the SSE stream.
                response = {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"echoed": msg.get("params", {})},
                }
                self._send_sse(json.dumps(response))
                return
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        except Exception:
            pass
        finally:
            try:
                if conn is not self._stream_conn:
                    conn.close()
            except Exception:
                pass

    def _send_sse(self, data: str) -> None:
        with self._stream_lock:
            stream = self._stream_conn
        if stream is None:
            return
        frame = f"data: {data}\r\n\r\n".encode()
        try:
            stream.sendall(frame)
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        with self._stream_lock:
            if self._stream_conn is not None:
                try:
                    self._stream_conn.close()
                except Exception:
                    pass
                self._stream_conn = None
        try:
            self.sock.close()
        except Exception:
            pass


@pytest.fixture
def sse_server():
    srv = _StubSSEServer()
    try:
        yield srv
    finally:
        srv.stop()


def test_sse_transport_roundtrip(sse_server):
    transport = SSETransport(sse_server.url, timeout=5.0)
    transport.start()
    try:
        assert transport._post_url is not None
        assert transport._post_url.endswith("/post")
        response = transport.send({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "ping",
            "params": {"hello": "world"},
        })
        assert response is not None
        assert response["id"] == 7
        assert response["result"]["echoed"] == {"hello": "world"}
    finally:
        transport.close()


def test_sse_transport_notification_returns_none(sse_server):
    transport = SSETransport(sse_server.url, timeout=5.0)
    transport.start()
    try:
        out = transport.send({"jsonrpc": "2.0", "method": "notify"})
        assert out is None
    finally:
        transport.close()


def test_sse_dispatch_parses_data_frame_directly():
    transport = SSETransport("http://unused", timeout=1.0)
    import queue as _q
    q: _q.Queue = _q.Queue(maxsize=1)
    transport._pending[42] = q
    transport._dispatch("", json.dumps({"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}))
    payload = q.get_nowait()
    assert payload["result"] == {"ok": True}
