"""Tests for WebSocketTransport: roundtrip against an in-process echo server."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

websockets = pytest.importorskip("websockets")

from chimera.mcp.ws_transport import WebSocketTransport  # noqa: E402


class _EchoServer:
    """Spin up a websockets server in a background thread that echoes JSON-RPC.

    Each incoming JSON-RPC request is replied to with a frame of the form
    ``{"jsonrpc": "2.0", "id": <id>, "result": {"echoed": <params>}}``.
    Notifications (no id) are silently dropped.
    """

    def __init__(self) -> None:
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    async def _handler(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" not in msg:
                continue
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"echoed": msg.get("params", {})},
            }))

    def _run(self) -> None:
        async def main() -> None:
            self._stop_event = asyncio.Event()
            async with websockets.serve(self._handler, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop_event.wait()

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        finally:
            loop.close()

    def stop(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        self._thread.join(timeout=2.0)


@pytest.fixture
def echo_server():
    srv = _EchoServer()
    try:
        yield srv
    finally:
        srv.stop()


def test_ws_roundtrip(echo_server):
    transport = WebSocketTransport(
        f"ws://127.0.0.1:{echo_server.port}", timeout=5.0
    )
    transport.start()
    try:
        response = transport.send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
            "params": {"k": "v"},
        })
        assert response is not None
        assert response["id"] == 1
        assert response["result"]["echoed"] == {"k": "v"}
    finally:
        transport.close()


def test_ws_notification_returns_none(echo_server):
    transport = WebSocketTransport(
        f"ws://127.0.0.1:{echo_server.port}", timeout=5.0
    )
    transport.start()
    try:
        out = transport.send({"jsonrpc": "2.0", "method": "notify"})
        assert out is None
    finally:
        transport.close()


def test_ws_send_before_start_raises():
    transport = WebSocketTransport("ws://127.0.0.1:1", timeout=1.0)
    with pytest.raises(ConnectionError):
        transport.send({"jsonrpc": "2.0", "id": 1, "method": "x"})
