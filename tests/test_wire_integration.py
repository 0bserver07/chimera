"""Tests for Wire integration with ReAct loop."""
from unittest.mock import MagicMock
import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.types import Message, ToolResult
from chimera.wire.wire import Wire
from chimera.wire.types import TurnBegin, TurnEnd, StepBegin, StepEnd


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def execute(self, args, env=None):
        return ToolResult(output=args["text"])


def _mock_provider_no_tools():
    provider = MagicMock()
    provider.model_name = "test-model"
    provider.complete.return_value = MagicMock(
        content="Hello!", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5},
        has_tool_calls=False,
    )
    return provider


def _mock_provider_with_tool_then_done():
    """Provider that makes one tool call, then responds with no tools."""
    from chimera.types import ToolCall
    provider = MagicMock()
    provider.model_name = "test-model"

    tool_call = ToolCall(id="tc1", name="echo", arguments={"text": "hi"})
    resp1 = MagicMock(content="Let me echo", tool_calls=[tool_call],
                       usage={"input_tokens": 10, "output_tokens": 5}, has_tool_calls=True)
    resp2 = MagicMock(content="Done!", tool_calls=[],
                       usage={"input_tokens": 15, "output_tokens": 3}, has_tool_calls=False)
    provider.complete.side_effect = [resp1, resp2]
    return provider


def test_wire_receives_turn_begin_and_end():
    wire = Wire()
    received = []
    wire.on_message(lambda msg: received.append(msg))

    config = LoopConfig(wire=wire)
    loop = ReAct(max_steps=5, config=config)
    provider = _mock_provider_no_tools()
    context = Context(system="test")
    context.add(Message.user("hi"))

    loop.run(provider, [], context, None)

    types = [type(m).__name__ for m in received]
    assert "TurnBegin" in types
    assert "TurnEnd" in types


def test_wire_receives_step_begin_and_end():
    wire = Wire()
    received = []
    wire.on_message(lambda msg: received.append(msg))

    config = LoopConfig(wire=wire)
    loop = ReAct(max_steps=5, config=config)
    provider = _mock_provider_no_tools()
    context = Context(system="test")
    context.add(Message.user("hi"))

    loop.run(provider, [], context, None)

    types = [type(m).__name__ for m in received]
    assert "StepBegin" in types
    assert "StepEnd" in types


def test_wire_step_count_matches_tool_steps():
    wire = Wire()
    received = []
    wire.on_message(lambda msg: received.append(msg))

    config = LoopConfig(wire=wire)
    loop = ReAct(max_steps=5, config=config)
    provider = _mock_provider_with_tool_then_done()
    context = Context(system="test")
    context.add(Message.user("echo hi"))

    loop.run(provider, [_EchoTool()], context, None)

    step_begins = [m for m in received if isinstance(m, StepBegin)]
    step_ends = [m for m in received if isinstance(m, StepEnd)]
    assert len(step_begins) == 2  # two LLM calls
    assert len(step_ends) == 2


def test_wire_not_required():
    """Loop works fine without wire."""
    config = LoopConfig()  # no wire
    loop = ReAct(max_steps=5, config=config)
    provider = _mock_provider_no_tools()
    context = Context(system="test")
    context.add(Message.user("hi"))

    result = loop.run(provider, [], context, None)
    assert result.success
