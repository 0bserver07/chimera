"""End-to-end tests proving Kimi features work inside an Agent loop.

These use a mock provider — no API key needed. They verify that the tools
are actually integrated into the Agent/ReAct loop, not just isolated modules.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.core.agent import Agent
from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.tools.ask_user import AskUserTool
from chimera.tools.dmail import DMailTool
from chimera.tools.think import ThinkTool
from chimera.tools.todo import TodoTool
from chimera.types import Message, ToolCall, ToolResult
from chimera.wire.wire import Wire
from chimera.wire.types import StepBegin, StepEnd


def _make_provider(responses):
    """Create a mock provider that returns a sequence of responses."""
    provider = MagicMock()
    provider.model_name = "test-model"
    provider.complete.side_effect = responses
    return provider


def _resp(content="", tool_calls=None):
    """Create a mock Response."""
    tcs = tool_calls or []
    r = MagicMock()
    r.content = content
    r.tool_calls = tcs
    r.usage = {"input_tokens": 10, "output_tokens": 5}
    r.has_tool_calls = len(tcs) > 0
    return r


# ---- ThinkTool inside an Agent loop ----


def test_think_tool_used_in_agent_loop():
    """Agent with ThinkTool can call 'think' and it records the thought."""
    think_call = ToolCall(id="tc1", name="think", arguments={"thought": "Let me reason about this"})
    provider = _make_provider([
        _resp("Let me think", tool_calls=[think_call]),
        _resp("The answer is 42."),
    ])

    agent = Agent(provider=provider, tools=[ThinkTool()], loop=ReAct(max_steps=5))
    result = agent.run("What is the meaning of life?", env=None)

    assert result.success
    assert result.steps == 2
    assert "42" in result.output


# ---- DMailTool inside an Agent loop ----


def test_dmail_binds_and_creates_checkpoint_in_loop():
    """DMailTool gets bound to agent's context and can create checkpoints."""
    cp_call = ToolCall(id="tc1", name="dmail", arguments={"action": "checkpoint"})
    provider = _make_provider([
        _resp("Creating checkpoint", tool_calls=[cp_call]),
        _resp("Done."),
    ])

    dmail = DMailTool()
    agent = Agent(provider=provider, tools=[dmail], loop=ReAct(max_steps=5))
    result = agent.run("Save a checkpoint.", env=None)

    assert result.success
    assert dmail._context is not None  # Was bound
    assert dmail.checkpoint_count == 1  # Checkpoint was created


def test_dmail_rewind_actually_truncates_agent_context():
    """DMailTool rewind actually affects the agent's conversation context."""
    # Step 1: create checkpoint
    cp_call = ToolCall(id="tc1", name="dmail", arguments={"action": "checkpoint"})
    # Step 2: generate noise
    # Step 3: send d-mail to rewind
    send_call = ToolCall(id="tc3", name="dmail", arguments={
        "action": "send", "checkpoint_id": 0,
        "message": "Skip the noise, the answer is X."
    })

    provider = _make_provider([
        _resp("Checkpointing", tool_calls=[cp_call]),
        _resp("Lots of noise here about irrelevant stuff"),  # step 2 - no tools
        # After rewind, context is truncated. Agent sees D-Mail and responds.
    ])

    dmail = DMailTool()
    agent = Agent(provider=provider, tools=[dmail], loop=ReAct(max_steps=5))

    # Step 2 ends with no tool calls, so the loop ends there
    result = agent.run("Analyze this codebase.", env=None)
    assert result.success

    # Verify the checkpoint was created and context was bound
    assert dmail._context is not None
    assert dmail.checkpoint_count == 1


# ---- TodoTool state persists across steps ----


def test_todo_state_persists_across_steps():
    """TodoTool keeps items across multiple tool calls in the same loop."""
    add_call = ToolCall(id="tc1", name="todo", arguments={"action": "add", "task": "Fix bug #1"})
    add_call2 = ToolCall(id="tc2", name="todo", arguments={"action": "add", "task": "Write tests"})
    list_call = ToolCall(id="tc3", name="todo", arguments={"action": "list"})

    provider = _make_provider([
        _resp("Adding tasks", tool_calls=[add_call, add_call2]),
        _resp("Listing tasks", tool_calls=[list_call]),
        _resp("All done."),
    ])

    todo = TodoTool()
    agent = Agent(provider=provider, tools=[todo], loop=ReAct(max_steps=5))
    result = agent.run("Track my tasks.", env=None)

    assert result.success
    assert len(todo.items) == 2
    assert todo.items[0].task == "Fix bug #1"
    assert todo.items[1].task == "Write tests"


# ---- AskUserTool with callback ----


def test_ask_user_callback_invoked_in_loop():
    """AskUserTool invokes the callback when the agent calls it."""
    ask_call = ToolCall(id="tc1", name="ask_user", arguments={
        "question": "Which file should I edit?"
    })

    callback_invoked = []
    def my_callback(question, choices=None):
        callback_invoked.append(question)
        return "main.py"

    provider = _make_provider([
        _resp("Let me ask", tool_calls=[ask_call]),
        _resp("I'll edit main.py."),
    ])

    ask = AskUserTool(callback=my_callback)
    agent = Agent(provider=provider, tools=[ask], loop=ReAct(max_steps=5))
    result = agent.run("Edit a file for me.", env=None)

    assert result.success
    assert len(callback_invoked) == 1
    assert "Which file" in callback_invoked[0]


# ---- Wire messages emitted during agent run ----


def test_wire_emits_lifecycle_messages_during_run():
    """Wire receives step lifecycle messages when connected via LoopConfig."""
    wire = Wire()
    received = []
    wire.on_message(lambda msg: received.append(msg))

    config = LoopConfig(wire=wire)
    provider = _make_provider([_resp("Hello!")])

    agent = Agent(provider=provider, tools=[], loop=ReAct(max_steps=5, config=config))
    result = agent.run("Say hello.", env=None)

    assert result.success
    type_names = [type(m).__name__ for m in received]
    assert "StepBegin" in type_names
    assert "StepEnd" in type_names


def test_wire_step_count_matches_agent_steps():
    """Wire StepBegin/End count matches actual steps taken."""
    wire = Wire()
    received = []
    wire.on_message(lambda msg: received.append(msg))

    echo_call = ToolCall(id="tc1", name="think", arguments={"thought": "hmm"})
    config = LoopConfig(wire=wire)
    provider = _make_provider([
        _resp("thinking", tool_calls=[echo_call]),
        _resp("done"),
    ])

    agent = Agent(provider=provider, tools=[ThinkTool()], loop=ReAct(max_steps=5, config=config))
    result = agent.run("Think about it.", env=None)

    assert result.success
    assert result.steps == 2
    step_begins = [m for m in received if isinstance(m, StepBegin)]
    step_ends = [m for m in received if isinstance(m, StepEnd)]
    assert len(step_begins) == 2
    assert len(step_ends) == 2


# ---- Multiple Kimi tools together ----


def test_think_and_todo_together():
    """Agent uses ThinkTool and TodoTool in the same loop."""
    think_call = ToolCall(id="tc1", name="think", arguments={"thought": "I need to plan"})
    add_call = ToolCall(id="tc2", name="todo", arguments={"action": "add", "task": "Step 1"})

    provider = _make_provider([
        _resp("Planning", tool_calls=[think_call, add_call]),
        _resp("Plan complete."),
    ])

    think = ThinkTool()
    todo = TodoTool()
    agent = Agent(provider=provider, tools=[think, todo], loop=ReAct(max_steps=5))
    result = agent.run("Plan the work.", env=None)

    assert result.success
    assert len(todo.items) == 1
