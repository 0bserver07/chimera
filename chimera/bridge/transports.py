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
    """WebSocket transport (requires ``websockets`` package).

    This is currently a stub — calling :meth:`send` or :meth:`receive`
    raises :exc:`NotImplementedError`.  Install ``websockets`` to use
    this transport.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        try:
            import websockets  # noqa: F401
        except ImportError:
            raise ImportError(
                "WebSocket transport requires 'pip install websockets'"
            )

    async def send(self, message: dict[str, Any]) -> None:
        """Not yet implemented."""
        raise NotImplementedError("WebSocket transport not yet implemented")

    async def receive(self) -> AsyncGenerator[dict[str, Any], None]:
        """Not yet implemented."""
        raise NotImplementedError("WebSocket transport not yet implemented")
        yield  # Make it a generator
