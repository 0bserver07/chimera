"""Tests for chimera.bridge — BridgeProtocol with InMemoryTransport."""
from __future__ import annotations

import asyncio

import pytest

from chimera.bridge.protocol import BridgeProtocol
from chimera.bridge.transports import InMemoryTransport


class TestBridgeProtocol:

    @pytest.mark.asyncio
    async def test_send_and_receive(self) -> None:
        """Messages sent via the protocol are receivable from the transport."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)

        await protocol.send("greeting", {"text": "hello"})

        messages: list[dict] = []
        async for msg in transport.receive():
            messages.append(msg)
            break  # Only need the first

        assert len(messages) == 1
        assert messages[0]["type"] == "greeting"
        assert messages[0]["data"]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_on_message_handler(self) -> None:
        """Registered handlers fire when listen() processes matching messages."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)

        received: list[dict] = []
        protocol.on_message("ping", lambda data: received.append(data))

        # Inject a message into the transport
        transport.inject({"type": "ping", "data": {"seq": 1}})

        # Listen processes one message then we stop
        listen_task = asyncio.create_task(protocol.listen())
        await asyncio.sleep(0.05)
        listen_task.cancel()
        try:
            await listen_task
        except asyncio.CancelledError:
            pass

        assert len(received) == 1
        assert received[0]["seq"] == 1

    @pytest.mark.asyncio
    async def test_async_handler_is_awaited(self) -> None:
        """Async handlers registered via on_message must actually run.

        Regression test: listen() previously called the handler but did
        not await the returned coroutine, so async handlers (like the
        ones REPLBridge registers) silently did nothing.
        """
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)

        received: list[dict] = []

        async def handler(data: dict) -> None:
            # force a suspension so the coroutine can't complete synchronously
            await asyncio.sleep(0)
            received.append(data)

        protocol.on_message("tick", handler)
        transport.inject({"type": "tick", "data": {"seq": 1}})

        listen_task = asyncio.create_task(protocol.listen())
        await asyncio.sleep(0.05)
        listen_task.cancel()
        try:
            await listen_task
        except asyncio.CancelledError:
            pass

        assert received == [{"seq": 1}]
