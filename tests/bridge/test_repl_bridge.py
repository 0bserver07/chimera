"""Tests for chimera.bridge.repl_bridge — REPLBridge."""
from __future__ import annotations

from typing import AsyncGenerator
from unittest.mock import MagicMock

import pytest

from chimera.bridge.protocol import BridgeProtocol
from chimera.bridge.repl_bridge import REPLBridge
from chimera.bridge.transports import InMemoryTransport
from chimera.core.loop_events import LoopEvent, LoopEventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _event_stream(
    events: list[LoopEvent],
) -> AsyncGenerator[LoopEvent, None]:
    """Yield pre-built LoopEvents."""
    for event in events:
        yield event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestREPLBridge:

    @pytest.mark.asyncio
    async def test_forward_events_sends_session_start_and_end(self):
        """forward_events should send session_start at the beginning and session_end at the end."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        # Forward an empty event stream
        await bridge.forward_events(_event_stream([]))

        # Collect all messages sent to the transport
        messages = []
        while not transport._queue.empty():
            messages.append(transport._queue.get_nowait())

        types = [m["type"] for m in messages]
        assert types[0] == "session_start"
        assert types[-1] == "session_end"

    @pytest.mark.asyncio
    async def test_assistant_message_forwarded(self):
        """ASSISTANT_CHUNK events should be forwarded as 'assistant_message'."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        events = [
            LoopEvent(
                type=LoopEventType.assistant_chunk,
                data="Hello world",
                turn=1,
            ),
        ]

        await bridge.forward_events(_event_stream(events))

        messages = []
        while not transport._queue.empty():
            messages.append(transport._queue.get_nowait())

        assistant_msgs = [m for m in messages if m["type"] == "assistant_message"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["data"]["chunk"] == "Hello world"
        assert assistant_msgs[0]["data"]["turn"] == 1

    @pytest.mark.asyncio
    async def test_tool_result_forwarded(self):
        """TOOL_RESULT events should be forwarded as 'tool_result'."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        # Simulate a tool_result event with simple data (no tuple)
        events = [
            LoopEvent(
                type=LoopEventType.tool_result,
                data="some output",
                turn=2,
            ),
        ]

        await bridge.forward_events(_event_stream(events))

        messages = []
        while not transport._queue.empty():
            messages.append(transport._queue.get_nowait())

        tool_msgs = [m for m in messages if m["type"] == "tool_result"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["data"]["turn"] == 2

    @pytest.mark.asyncio
    async def test_user_input_queued(self):
        """_handle_user_message should queue text for get_user_input."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        await bridge._handle_user_message({"text": "fix the bug"})

        result = await bridge.get_user_input(timeout=1.0)
        assert result == "fix the bug"

    @pytest.mark.asyncio
    async def test_get_user_input_timeout(self):
        """get_user_input should return None on timeout."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        result = await bridge.get_user_input(timeout=0.05)
        assert result is None

    @pytest.mark.asyncio
    async def test_event_to_message_handles_error(self):
        """ERROR events should be converted to error messages."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        events = [
            LoopEvent(
                type=LoopEventType.error,
                data="something went wrong",
                turn=3,
            ),
        ]

        await bridge.forward_events(_event_stream(events))

        messages = []
        while not transport._queue.empty():
            messages.append(transport._queue.get_nowait())

        error_msgs = [m for m in messages if m["type"] == "error"]
        assert len(error_msgs) == 1
        assert error_msgs[0]["data"]["message"] == "something went wrong"
        assert error_msgs[0]["data"]["turn"] == 3

    @pytest.mark.asyncio
    async def test_event_to_message_handles_tool_use(self):
        """TOOL_USE events should be converted to tool_use messages."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        events = [
            LoopEvent(
                type=LoopEventType.tool_use,
                data="read_file",
                turn=1,
            ),
        ]

        await bridge.forward_events(_event_stream(events))

        messages = []
        while not transport._queue.empty():
            messages.append(transport._queue.get_nowait())

        tool_msgs = [m for m in messages if m["type"] == "tool_use"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["data"]["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_event_to_message_handles_result(self):
        """RESULT events should be converted to status_update messages."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        result_data = MagicMock()
        result_data.reason = "completed"
        result_data.turn_count = 5
        result_data.cost_usd = 0.05

        events = [
            LoopEvent(
                type=LoopEventType.result,
                data=result_data,
                turn=5,
            ),
        ]

        await bridge.forward_events(_event_stream(events))

        messages = []
        while not transport._queue.empty():
            messages.append(transport._queue.get_nowait())

        status_msgs = [m for m in messages if m["type"] == "status_update"]
        assert len(status_msgs) == 1
        assert status_msgs[0]["data"]["status"] == "completed"
        assert status_msgs[0]["data"]["turn_count"] == 5

    @pytest.mark.asyncio
    async def test_is_running_reflects_state(self):
        """is_running should be False before and after forward_events."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        assert bridge.is_running is False
        await bridge.forward_events(_event_stream([]))
        assert bridge.is_running is False

    @pytest.mark.asyncio
    async def test_event_to_message_returns_none_for_unknown(self):
        """Unknown event types should return None (not forwarded)."""
        transport = InMemoryTransport()
        protocol = BridgeProtocol(transport)
        bridge = REPLBridge(protocol)

        # stream_start is not mapped in _event_to_message
        event = LoopEvent(
            type=LoopEventType.stream_start,
            data="starting",
            turn=0,
        )
        result = bridge._event_to_message(event)
        assert result is None
