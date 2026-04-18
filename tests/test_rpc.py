"""Tests for chimera.rpc — types, server, and handler."""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock


from chimera.rpc.types import (
    CancelCommand,
    CompactCommand,
    ErrorEvent,
    GetStateCommand,
    MessageEvent,
    PromptCommand,
    RpcCommand,
    RpcResponse,
    SetModelCommand,
    StateResponse,
    SteerCommand,
    ToolExecutionEvent,
)


# ---------------------------------------------------------------------------
# Type tests (~14)
# ---------------------------------------------------------------------------


class TestRpcCommand:
    def test_defaults(self):
        cmd = RpcCommand()
        assert cmd.type == ""
        assert cmd.id == ""

    def test_custom(self):
        cmd = RpcCommand(type="ping", id="42")
        assert cmd.type == "ping"
        assert cmd.id == "42"


class TestPromptCommand:
    def test_defaults(self):
        cmd = PromptCommand()
        assert cmd.type == "prompt"
        assert cmd.message == ""

    def test_custom(self):
        cmd = PromptCommand(id="1", message="hello")
        assert cmd.message == "hello"
        assert cmd.id == "1"


class TestSteerCommand:
    def test_defaults(self):
        cmd = SteerCommand()
        assert cmd.type == "steer"
        assert cmd.message == ""


class TestCancelCommand:
    def test_defaults(self):
        cmd = CancelCommand()
        assert cmd.type == "cancel"


class TestGetStateCommand:
    def test_defaults(self):
        cmd = GetStateCommand()
        assert cmd.type == "get_state"


class TestCompactCommand:
    def test_defaults(self):
        cmd = CompactCommand()
        assert cmd.type == "compact"


class TestSetModelCommand:
    def test_defaults(self):
        cmd = SetModelCommand()
        assert cmd.type == "set_model"
        assert cmd.model == ""

    def test_custom(self):
        cmd = SetModelCommand(id="3", model="glm-5")
        assert cmd.model == "glm-5"


class TestRpcResponse:
    def test_defaults(self):
        resp = RpcResponse()
        assert resp.command == ""
        assert resp.success is True
        assert resp.error == ""

    def test_error(self):
        resp = RpcResponse(command="prompt", id="1", success=False, error="boom")
        assert resp.success is False
        assert resp.error == "boom"


class TestStateResponse:
    def test_defaults(self):
        resp = StateResponse()
        assert resp.command == "get_state"
        assert resp.messages == []
        assert resp.model == ""

    def test_custom(self):
        resp = StateResponse(id="r1", messages=[{"role": "user", "content": "hi"}], model="glm-5")
        assert len(resp.messages) == 1
        assert resp.model == "glm-5"


class TestMessageEvent:
    def test_defaults(self):
        evt = MessageEvent()
        assert evt.type == "message"
        assert evt.role == "assistant"
        assert evt.done is False

    def test_custom(self):
        evt = MessageEvent(content="hello", done=True)
        assert evt.content == "hello"
        assert evt.done is True


class TestToolExecutionEvent:
    def test_defaults(self):
        evt = ToolExecutionEvent()
        assert evt.type == "tool_execution"
        assert evt.status == "running"
        assert evt.result is None


class TestErrorEvent:
    def test_defaults(self):
        evt = ErrorEvent()
        assert evt.type == "error"
        assert evt.message == ""

    def test_custom(self):
        evt = ErrorEvent(message="oops")
        assert evt.message == "oops"


# ---------------------------------------------------------------------------
# Server + Handler tests
# ---------------------------------------------------------------------------

from chimera.rpc.server import RpcServer
from chimera.rpc.handler import RpcHandler


def test_server_parse_prompt_command():
    server = RpcServer.__new__(RpcServer)
    server._handlers = {}
    cmd = server._parse_command({"type": "prompt", "id": "1", "message": "hello"})
    assert isinstance(cmd, PromptCommand)
    assert cmd.message == "hello"


def test_server_parse_unknown_command():
    server = RpcServer.__new__(RpcServer)
    server._handlers = {}
    cmd = server._parse_command({"type": "unknown_xyz", "id": "1"})
    assert isinstance(cmd, RpcCommand)
    assert cmd.type == "unknown_xyz"


def test_server_emit():
    server = RpcServer.__new__(RpcServer)
    server._stdout = io.StringIO()
    server._emit(ErrorEvent(message="oops"))
    output = server._stdout.getvalue()
    parsed = json.loads(output.strip())
    assert parsed["type"] == "error"
    assert parsed["message"] == "oops"


def test_server_dispatch_unknown():
    server = RpcServer.__new__(RpcServer)
    server._stdout = io.StringIO()
    server._handlers = {}
    server._dispatch(RpcCommand(type="nope", id="1"))
    output = server._stdout.getvalue()
    parsed = json.loads(output.strip())
    assert parsed["success"] is False
    assert "Unknown command" in parsed["error"]


def test_handler_get_state():
    mock_session = MagicMock()
    mock_session.messages = []
    mock_session._agent.provider.model_name = "glm-5"

    server = RpcServer.__new__(RpcServer)
    server._session = mock_session
    server._stdout = io.StringIO()

    handler = RpcHandler(server)
    handler.handle_get_state(GetStateCommand(id="req-1"))

    output = server._stdout.getvalue()
    parsed = json.loads(output.strip())
    assert parsed["command"] == "get_state"
    assert parsed["model"] == "glm-5"


def test_server_run_processes_lines():
    mock_session = MagicMock()
    mock_session.messages = []
    mock_session._agent.provider.model_name = "test"

    stdin = io.StringIO('{"type": "get_state", "id": "1"}\n')
    stdout = io.StringIO()

    server = RpcServer(mock_session, stdin=stdin, stdout=stdout)
    handler = RpcHandler(server)
    server.set_handlers(handler.handlers)
    server.run()

    output = stdout.getvalue()
    lines = [json.loads(l) for l in output.strip().split("\n") if l.strip()]
    assert any(l.get("command") == "get_state" for l in lines)
