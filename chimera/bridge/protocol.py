"""Bridge protocol for inter-process agent communication.

Provides :class:`BridgeTransport` (ABC) and :class:`BridgeProtocol`
which adds message-type routing and handler registration on top of a
transport.
"""
from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, Callable

__all__ = ["BridgeTransport", "BridgeProtocol"]


class BridgeTransport(ABC):
    """Abstract transport layer for the bridge protocol."""

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Send a message over the transport."""

    @abstractmethod
    async def receive(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield incoming messages from the transport."""
        yield {}  # pragma: no cover


class BridgeProtocol:
    """Message-type router built on a :class:`BridgeTransport`.

    Args:
        transport: The underlying transport to send/receive messages.
    """

    def __init__(self, transport: BridgeTransport) -> None:
        self._transport = transport
        self._handlers: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}

    def on_message(self, msg_type: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        """Register *handler* to fire when a message of *msg_type* arrives."""
        self._handlers.setdefault(msg_type, []).append(handler)

    async def send(self, msg_type: str, data: dict[str, Any]) -> None:
        """Send a typed message via the transport."""
        await self._transport.send({"type": msg_type, "data": data})

    async def listen(self) -> None:
        """Listen for messages and dispatch to registered handlers.

        Runs until the transport's receive generator is exhausted or the
        task is cancelled.  Both sync and async handlers are supported:
        async handlers (or sync handlers that return a coroutine) are
        awaited so their side effects actually happen.  Without this,
        registering an ``async def`` handler would silently do nothing.
        """
        async for message in self._transport.receive():
            msg_type = message.get("type")
            if msg_type and msg_type in self._handlers:
                data = message.get("data", {})
                for handler in self._handlers[msg_type]:
                    result = handler(data)
                    if inspect.isawaitable(result):
                        await result
