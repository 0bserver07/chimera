"""Tests for chimera.rpc."""
import json
from dataclasses import asdict
from chimera.rpc.types import (
    PromptCommand, SteerCommand, CancelCommand, GetStateCommand,
    CompactCommand, SetModelCommand,
    RpcResponse, StateResponse,
    MessageEvent, TextDeltaEvent, ToolExecutionEvent, ErrorEvent,
    CompactionEvent, RpcCommand,
)


def test_prompt_command_serializable():
    cmd = PromptCommand(message="hello", id="req-1")
    d = asdict(cmd)
    assert d["type"] == "prompt"
    assert d["message"] == "hello"
    assert json.dumps(d)


def test_steer_command():
    cmd = SteerCommand(message="change direction", id="req-2")
    d = asdict(cmd)
    assert d["type"] == "steer"
    assert d["message"] == "change direction"


def test_cancel_command():
    cmd = CancelCommand(id="req-3")
    assert cmd.type == "cancel"


def test_get_state_command():
    cmd = GetStateCommand(id="req-4")
    assert cmd.type == "get_state"


def test_compact_command():
    cmd = CompactCommand(instructions="focus on code", id="req-5")
    assert cmd.instructions == "focus on code"


def test_set_model_command():
    cmd = SetModelCommand(provider="anthropic", model="glm-5", id="req-6")
    assert cmd.provider == "anthropic"
    assert cmd.model == "glm-5"


def test_rpc_response_serializable():
    resp = RpcResponse(command="prompt", id="req-1", success=True)
    d = asdict(resp)
    assert json.dumps(d)
    assert d["success"] is True


def test_state_response():
    resp = StateResponse(
        id="req-2",
        messages=[{"role": "user", "content": "hi"}],
        model="glm-5",
        total_cost=0.05,
    )
    d = asdict(resp)
    assert d["model"] == "glm-5"
    assert d["total_cost"] == 0.05


def test_message_event():
    evt = MessageEvent(role="assistant", content="hello", done=True)
    d = asdict(evt)
    assert d["type"] == "message"
    assert d["done"] is True


def test_text_delta_event():
    evt = TextDeltaEvent(content="partial")
    assert evt.type == "text_delta"


def test_tool_execution_event():
    evt = ToolExecutionEvent(
        tool_name="bash", arguments={"command": "ls"}, phase="start",
    )
    d = asdict(evt)
    assert d["tool_name"] == "bash"
    assert d["phase"] == "start"


def test_compaction_event():
    evt = CompactionEvent(tokens_before=5000, tokens_after=1000)
    assert evt.tokens_before == 5000


def test_error_event():
    evt = ErrorEvent(message="something broke")
    d = asdict(evt)
    assert d["type"] == "error"
    assert d["message"] == "something broke"


def test_rpc_command_defaults():
    cmd = RpcCommand()
    assert cmd.type == ""
    assert cmd.id == ""
