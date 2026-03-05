"""Tests for the Agent Client Protocol (ACP) module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from chimera.acp.client import ACPClient
from chimera.acp.tool import ExternalAgentTool
from chimera.acp.types import ACPResponse, ACPSessionConfig, ACPToolCall


# ---------------------------------------------------------------------------
# ACPSessionConfig
# ---------------------------------------------------------------------------

class TestACPSessionConfig:
    def test_defaults(self):
        cfg = ACPSessionConfig()
        assert cfg.command == []
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.working_dir is None
        assert cfg.notification_drain_delay == 0.1

    def test_custom(self):
        cfg = ACPSessionConfig(
            command=["npx", "-y", "claude-code-acp"],
            args=["--verbose"],
            env={"KEY": "val"},
            working_dir="/tmp",
        )
        assert cfg.command == ["npx", "-y", "claude-code-acp"]
        assert cfg.args == ["--verbose"]
        assert cfg.env == {"KEY": "val"}
        assert cfg.working_dir == "/tmp"


# ---------------------------------------------------------------------------
# ACPToolCall
# ---------------------------------------------------------------------------

class TestACPToolCall:
    def test_creation(self):
        tc = ACPToolCall(
            tool_call_id="tc1", title="Read file",
            tool_kind="filesystem", status="running",
        )
        assert tc.tool_call_id == "tc1"
        assert tc.status == "running"
        assert tc.is_error is False
        assert tc.raw_output is None


# ---------------------------------------------------------------------------
# ACPResponse
# ---------------------------------------------------------------------------

class TestACPResponse:
    def test_creation(self):
        resp = ACPResponse(
            text="Hello", thoughts=["thinking"], tool_calls=[],
            cost=0.01, input_tokens=100, output_tokens=50,
        )
        assert resp.text == "Hello"
        assert resp.cost == 0.01


# ---------------------------------------------------------------------------
# ACPClient
# ---------------------------------------------------------------------------

def _make_mock_process(responses: list[dict]) -> MagicMock:
    """Create a mock subprocess that returns JSON-RPC responses."""
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()

    lines = [json.dumps(r).encode() + b"\n" for r in responses]
    proc.stdout.readline = MagicMock(side_effect=lines)
    return proc


class TestACPClient:
    def test_start_creates_session(self):
        config = ACPSessionConfig(
            command=["echo"], working_dir="/tmp",
        )
        client = ACPClient(config)

        response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        mock_proc = _make_mock_process([response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()

        assert client._session_id == "s1"

    def test_send_message_collects_text(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        # Simulate start
        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        text_notification = {
            "method": "agent/messageChunk",
            "params": {"text": "Hello "},
        }
        text_notification2 = {
            "method": "agent/messageChunk",
            "params": {"text": "world"},
        }
        send_response = {"jsonrpc": "2.0", "id": 2, "result": {}}

        mock_proc = _make_mock_process([
            start_response,
            text_notification,
            text_notification2,
            send_response,
        ])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            resp = client.send_message("test")

        assert resp.text == "Hello world"

    def test_send_message_collects_thoughts(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        thought = {
            "method": "agent/thoughtChunk",
            "params": {"text": "I need to think"},
        }
        send_response = {"jsonrpc": "2.0", "id": 2, "result": {}}

        mock_proc = _make_mock_process([start_response, thought, send_response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            resp = client.send_message("test")

        assert resp.thoughts == ["I need to think"]

    def test_send_message_tracks_tool_calls(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        tool_start = {
            "method": "agent/toolCallStart",
            "params": {
                "tool_call_id": "tc1", "title": "bash",
                "tool_kind": "shell",
            },
        }
        tool_complete = {
            "method": "agent/toolCallComplete",
            "params": {
                "tool_call_id": "tc1", "output": "done",
                "is_error": False,
            },
        }
        send_response = {"jsonrpc": "2.0", "id": 2, "result": {}}

        mock_proc = _make_mock_process([
            start_response, tool_start, tool_complete, send_response,
        ])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            resp = client.send_message("test")

        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].tool_call_id == "tc1"
        assert resp.tool_calls[0].status == "completed"
        assert resp.tool_calls[0].raw_output == "done"

    def test_send_message_tracks_usage(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        usage = {
            "method": "agent/usageUpdate",
            "params": {
                "total_cost": 0.05,
                "input_tokens": 1000,
                "output_tokens": 500,
            },
        }
        send_response = {"jsonrpc": "2.0", "id": 2, "result": {}}

        mock_proc = _make_mock_process([start_response, usage, send_response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            resp = client.send_message("test")

        assert resp.cost == 0.05
        assert resp.input_tokens == 1000
        assert resp.output_tokens == 500

    def test_fork_session(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        fork_response = {"jsonrpc": "2.0", "id": 2, "result": {"session_id": "s2"}}

        mock_proc = _make_mock_process([start_response, fork_response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            new_id = client.fork_session()

        assert new_id == "s2"

    def test_stop_terminates_process(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)
        mock_proc = MagicMock()
        client._process = mock_proc

        client.stop()

        mock_proc.terminate.assert_called_once()
        assert client._process is None

    def test_rpc_error_raises(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        error_response = {
            "jsonrpc": "2.0", "id": 2,
            "error": {"code": -1, "message": "bad"},
        }

        mock_proc = _make_mock_process([start_response, error_response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            with pytest.raises(RuntimeError, match="ACP RPC error"):
                client.fork_session()

    def test_context_manager(self):
        config = ACPSessionConfig(command=["echo"])
        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        mock_proc = _make_mock_process([start_response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            with ACPClient(config) as client:
                assert client._session_id == "s1"

    def test_rpc_without_start_raises(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)
        with pytest.raises(RuntimeError, match="not started"):
            client._rpc("test", {})

    def test_on_chunk_callback(self):
        config = ACPSessionConfig(command=["echo"])
        client = ACPClient(config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        chunk = {"method": "agent/messageChunk", "params": {"text": "hi"}}
        send_response = {"jsonrpc": "2.0", "id": 2, "result": {}}

        mock_proc = _make_mock_process([start_response, chunk, send_response])
        chunks: list[str] = []

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            client.start()
            client.send_message("test", on_chunk=chunks.append)

        assert chunks == ["hi"]


# ---------------------------------------------------------------------------
# ExternalAgentTool
# ---------------------------------------------------------------------------

class TestExternalAgentTool:
    def test_name_and_schema(self):
        tool = ExternalAgentTool(
            config=ACPSessionConfig(command=["echo"]),
            agent_name="claude_code",
        )
        assert tool.name == "claude_code"
        assert "task" in tool.parameters["properties"]
        schema = tool.to_anthropic_schema()
        assert schema["name"] == "claude_code"

    def test_execute_delegates_to_client(self):
        tool = ExternalAgentTool(
            config=ACPSessionConfig(command=["echo"]),
        )
        mock_client = MagicMock()
        mock_client.send_message.return_value = ACPResponse(
            text="Done!", thoughts=[], tool_calls=[],
            cost=0.01, input_tokens=100, output_tokens=50,
        )
        tool._client = mock_client

        result = tool.execute({"task": "fix the bug"}, env=None)

        mock_client.send_message.assert_called_once_with("fix the bug")
        assert result.output == "Done!"
        assert result.metadata["cost"] == 0.01

    def test_cleanup_stops_client(self):
        tool = ExternalAgentTool(
            config=ACPSessionConfig(command=["echo"]),
        )
        mock_client = MagicMock()
        tool._client = mock_client

        tool.cleanup()

        mock_client.stop.assert_called_once()
        assert tool._client is None

    def test_setup_creates_client(self):
        config = ACPSessionConfig(command=["echo"])
        tool = ExternalAgentTool(config=config)

        start_response = {"jsonrpc": "2.0", "id": 1, "result": {"session_id": "s1"}}
        mock_proc = _make_mock_process([start_response])

        with patch("chimera.acp.client.subprocess.Popen", return_value=mock_proc):
            tool.setup()

        assert tool._client is not None
