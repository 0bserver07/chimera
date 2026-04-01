"""Built-in bridge transports.

Provides :class:`InMemoryTransport` — an in-process transport useful for
testing that uses an :class:`asyncio.Queue`.

Also provides :class:`StdioBridgeTransport` for subprocess communication
via stdin/stdout and :class:`WebSocketTransport` (stub) for WebSocket-based
communication.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from typing import Any

from chimera.bridge.protocol import BridgeTransport

__all__ = ["InMemoryTransport", "StdioBridgeTransport", "WebSocketTransport"]


class InMemoryTransport(BridgeTransport):
    """In-memory transport backed by an :class:`asyncio.Queue`.

    Use :meth:`inject` to push messages that :meth:`receive` will yield.
    Messages sent via :meth:`send` are also placed on the queue so they
    can be received back (useful for loopback testing).
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def inject(self, message: dict[str, Any]) -> None:
        """Push a message directly onto the queue (for testing)."""
        self._queue.put_nowait(message)

    async def send(self, message: dict[str, Any]) -> None:
        """Place *message* on the internal queue."""
        self._queue.put_nowait(message)

    async def receive(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield messages from the internal queue."""
        while True:
            message = await self._queue.get()
            yield message


class StdioBridgeTransport(BridgeTransport):
    """Stdio-based transport for subprocess communication.

    Sends JSON messages as newline-delimited lines on stdout and reads
    them from stdin.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(self, message: dict[str, Any]) -> None:
        """Write *message* as a JSON line to stdout."""
        data = json.dumps(message) + "\n"
        sys.stdout.write(data)
        sys.stdout.flush()

    async def receive(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield parsed JSON objects read line-by-line from stdin."""
        while True:
            line = await asyncio.get_event_loop().run_in_executor(
                None, sys.stdin.readline,
            )
            if not line:
                break
            try:
                yield json.loads(line.strip())
            except json.JSONDecodeError:
                continue


class WebSocketTransport(BridgeTransport):
    """WebSocket-based transport for IDE integration.

    Requires the ``websockets`` package (optional dependency).
    Call :meth:`connect` before sending or receiving messages.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: Any = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to the WebSocket server."""
        try:
            import websockets
            self._ws = await websockets.connect(self._url)
            self._connected = True
        except ImportError:
            raise ImportError(
                "WebSocket transport requires the 'websockets' package. "
                "Install it with: pip install websockets"
            )

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message over WebSocket."""
        if not self._ws or not self._connected:
            raise ConnectionError("WebSocket not connected. Call connect() first.")
        await self._ws.send(json.dumps(message))

    async def receive(self) -> AsyncGenerator[dict[str, Any], None]:
        """Receive messages from WebSocket."""
        if not self._ws:
            raise ConnectionError("WebSocket not connected. Call connect() first.")
        try:
            async for raw in self._ws:
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue
        except Exception:
            self._connected = False

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        """Whether the WebSocket is currently connected."""
        return self._connected
