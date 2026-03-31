"""Built-in bridge transports.

Provides :class:`InMemoryTransport` — an in-process transport useful for
testing that uses an :class:`asyncio.Queue`.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from chimera.bridge.protocol import BridgeTransport

__all__ = ["InMemoryTransport"]


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
