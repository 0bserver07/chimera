"""Tests for chimera.bridge.transports — StdioBridgeTransport and WebSocketTransport."""
from __future__ import annotations

import asyncio
import io
import json
import sys
from unittest.mock import patch

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
            original_run_in_executor = loop.run_in_executor

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
# WebSocketTransport: import check and stubs
# ---------------------------------------------------------------------------


class TestWebSocketTransport:

    def test_init_stores_url(self):
        """WebSocketTransport should store the URL."""
        # This may raise ImportError if websockets is not installed,
        # which is fine — that's also tested below.
        try:
            transport = WebSocketTransport("ws://localhost:8080")
            assert transport._url == "ws://localhost:8080"
        except ImportError:
            pass

    def test_raises_import_error_without_websockets(self):
        """WebSocketTransport should raise ImportError if websockets is missing."""
        with patch.dict(sys.modules, {"websockets": None}):
            with pytest.raises(ImportError, match="websockets"):
                WebSocketTransport("ws://localhost:8080")

    @pytest.mark.asyncio
    async def test_send_not_implemented(self):
        """send() should raise NotImplementedError."""
        try:
            transport = WebSocketTransport("ws://localhost:8080")
        except ImportError:
            pytest.skip("websockets not installed")
        with pytest.raises(NotImplementedError):
            await transport.send({"test": True})

    @pytest.mark.asyncio
    async def test_receive_not_implemented(self):
        """receive() should raise NotImplementedError."""
        try:
            transport = WebSocketTransport("ws://localhost:8080")
        except ImportError:
            pytest.skip("websockets not installed")
        with pytest.raises(NotImplementedError):
            async for _ in transport.receive():
                pass
