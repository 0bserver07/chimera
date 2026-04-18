"""Tests for chimera.bridge.transports — StdioBridgeTransport and WebSocketTransport."""
from __future__ import annotations

import asyncio
import io
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chimera.bridge.transports import StdioBridgeTransport, WebSocketTransport


# ---------------------------------------------------------------------------
# StdioBridgeTransport: send writes JSON to stdout
# ---------------------------------------------------------------------------


class TestStdioBridgeTransport:

    @pytest.mark.asyncio
    async def test_send_writes_json_to_stdout(self):
        """send() should write a JSON line to stdout."""
        fake_stdout = io.StringIO()
        transport = StdioBridgeTransport()

        with patch.object(sys, "stdout", fake_stdout):
            await transport.send({"type": "ping", "seq": 1})

        output = fake_stdout.getvalue()
        parsed = json.loads(output.strip())
        assert parsed["type"] == "ping"
        assert parsed["seq"] == 1

    @pytest.mark.asyncio
    async def test_send_appends_newline(self):
        """Each send() call should end with a newline."""
        fake_stdout = io.StringIO()
        transport = StdioBridgeTransport()

        with patch.object(sys, "stdout", fake_stdout):
            await transport.send({"msg": "hello"})

        assert fake_stdout.getvalue().endswith("\n")

    @pytest.mark.asyncio
    async def test_receive_yields_parsed_json_lines(self):
        """receive() should yield parsed JSON from stdin lines."""
        lines = [
            json.dumps({"type": "a"}) + "\n",
            json.dumps({"type": "b"}) + "\n",
            "",  # EOF
        ]
        line_iter = iter(lines)

        transport = StdioBridgeTransport()

        received = []
        with patch.object(sys, "stdin", io.StringIO("".join(lines))):
            # We need to patch run_in_executor to read from our fake stdin
            loop = asyncio.get_event_loop()

            call_count = 0

            async def mock_run_in_executor(executor, fn):
                nonlocal call_count
                try:
                    line = next(line_iter)
                except StopIteration:
                    return ""
                call_count += 1
                return line

            with patch.object(loop, "run_in_executor", mock_run_in_executor):
                async for msg in transport.receive():
                    received.append(msg)

        assert len(received) == 2
        assert received[0]["type"] == "a"
        assert received[1]["type"] == "b"

    @pytest.mark.asyncio
    async def test_receive_skips_invalid_json(self):
        """receive() should skip lines that are not valid JSON."""
        lines = [
            "not json\n",
            json.dumps({"type": "valid"}) + "\n",
            "",  # EOF
        ]
        line_iter = iter(lines)

        transport = StdioBridgeTransport()
        loop = asyncio.get_event_loop()

        async def mock_run_in_executor(executor, fn):
            try:
                return next(line_iter)
            except StopIteration:
                return ""

        received = []
        with patch.object(loop, "run_in_executor", mock_run_in_executor):
            async for msg in transport.receive():
                received.append(msg)

        assert len(received) == 1
        assert received[0]["type"] == "valid"


# ---------------------------------------------------------------------------
# WebSocketTransport: connect, send, receive, disconnect
# ---------------------------------------------------------------------------


class TestWebSocketTransport:

    def test_init_stores_url(self):
        """WebSocketTransport.__init__ should store the URL and start disconnected."""
        transport = WebSocketTransport("ws://localhost:8080")
        assert transport._url == "ws://localhost:8080"
        assert transport._ws is None
        assert transport._connected is False
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_raises_import_error_on_connect_without_websockets(self):
        """connect() should raise ImportError if websockets is missing."""
        transport = WebSocketTransport("ws://localhost:8080")
        with patch.dict(sys.modules, {"websockets": None}):
            with pytest.raises(ImportError, match="websockets"):
                await transport.connect()

    @pytest.mark.asyncio
    async def test_connect_sets_connected(self):
        """connect() should set _connected to True on success."""
        transport = WebSocketTransport("ws://localhost:8080")
        mock_ws = AsyncMock()
        mock_websockets = MagicMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        with patch.dict(sys.modules, {"websockets": mock_websockets}):
            await transport.connect()

        assert transport.is_connected is True
        assert transport._ws is mock_ws

    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self):
        """send() should raise ConnectionError when not connected."""
        transport = WebSocketTransport("ws://localhost:8080")
        with pytest.raises(ConnectionError, match="not connected"):
            await transport.send({"hello": "world"})

    @pytest.mark.asyncio
    async def test_send_sends_json(self):
        """send() should JSON-encode and send via ws."""
        transport = WebSocketTransport("ws://localhost:8080")
        mock_ws = AsyncMock()
        transport._ws = mock_ws
        transport._connected = True

        await transport.send({"type": "ping"})

        mock_ws.send.assert_awaited_once_with(json.dumps({"type": "ping"}))

    @pytest.mark.asyncio
    async def test_receive_raises_when_not_connected(self):
        """receive() should raise ConnectionError when not connected."""
        transport = WebSocketTransport("ws://localhost:8080")
        with pytest.raises(ConnectionError, match="not connected"):
            async for _ in transport.receive():
                pass

    @pytest.mark.asyncio
    async def test_disconnect_closes_ws(self):
        """disconnect() should close the WebSocket and set connected to False."""
        transport = WebSocketTransport("ws://localhost:8080")
        mock_ws = AsyncMock()
        transport._ws = mock_ws
        transport._connected = True

        await transport.disconnect()

        mock_ws.close.assert_awaited_once()
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect_noop_when_not_connected(self):
        """disconnect() should be a no-op when no WebSocket exists."""
        transport = WebSocketTransport("ws://localhost:8080")
        # Should not raise
        await transport.disconnect()
        assert transport.is_connected is False

    @pytest.mark.asyncio
    async def test_receive_reraises_underlying_ws_exception(self):
        """receive() must re-raise (not swallow) ws exceptions.

        Regression test: previously ``async for raw in self._ws`` was
        wrapped in ``except Exception: self._connected = False`` which
        turned a dropped connection into a silent StopAsyncIteration so
        callers could not distinguish a closed socket from no messages.
        """
        transport = WebSocketTransport("ws://localhost:8080")

        class BrokenWS:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise ConnectionError("ws peer gone")

        transport._ws = BrokenWS()
        transport._connected = True

        with pytest.raises(ConnectionError, match="ws peer gone"):
            async for _ in transport.receive():
                pass
        # And the state flag must be cleared so callers can tell.
        assert transport.is_connected is False
