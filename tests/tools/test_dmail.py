# tests/test_dmail.py
"""Tests for the DMailTool."""
import pytest

from chimera.core.context import Context
from chimera.tools.dmail import DMailTool
from chimera.types import Message


@pytest.fixture
def context():
    return Context(system="You are an agent.")


@pytest.fixture
def tool(context):
    t = DMailTool()
    t.bind_context(context)
    return t


# ------------------------------------------------------------------
# Schema / identity
# ------------------------------------------------------------------


def test_name_and_schema(tool):
    assert tool.name == "dmail"
    assert "action" in tool.parameters["properties"]
    assert "checkpoint_id" in tool.parameters["properties"]
    assert "message" in tool.parameters["properties"]
    assert "action" in tool.parameters["required"]


# ------------------------------------------------------------------
# create_checkpoint
# ------------------------------------------------------------------


def test_create_checkpoint(tool):
    cp_id = tool.create_checkpoint()
    assert cp_id == 0


def test_create_multiple_checkpoints(tool, context):
    context.add(Message.user("hello"))
    cp0 = tool.create_checkpoint()
    context.add(Message.assistant("hi"))
    cp1 = tool.create_checkpoint()
    context.add(Message.user("more"))
    cp2 = tool.create_checkpoint()
    assert (cp0, cp1, cp2) == (0, 1, 2)


def test_checkpoint_count(tool, context):
    assert tool.checkpoint_count == 0
    tool.create_checkpoint()
    assert tool.checkpoint_count == 1
    context.add(Message.user("x"))
    tool.create_checkpoint()
    assert tool.checkpoint_count == 2
    tool.create_checkpoint()
    assert tool.checkpoint_count == 3


def test_checkpoint_via_execute(tool, context):
    context.add(Message.user("hello"))
    result = tool.execute({"action": "checkpoint"})
    assert result.error is None
    assert "Checkpoint 0" in result.output
    assert result.metadata["checkpoint_id"] == 0


# ------------------------------------------------------------------
# execute — rewind
# ------------------------------------------------------------------


def test_execute_rewinds_context(tool, context):
    # Set up: msg0, checkpoint, msg1, msg2
    context.add(Message.user("step 1"))
    tool.create_checkpoint()  # cp 0 — at index 1
    context.add(Message.assistant("response 1"))
    context.add(Message.user("step 2"))
    assert len(context.messages) == 3

    result = tool.execute({"action": "send", "checkpoint_id": 0, "message": "Skip ahead."})
    assert result.error is None

    # After rewind: original msg0 ("step 1") is kept, plus the dmail message
    assert len(context.messages) == 2
    assert context.messages[0].content == "step 1"
    assert "D-Mail" in context.messages[1].content


def test_execute_appends_message(tool, context):
    tool.create_checkpoint()  # cp 0 — at index 0 (empty context)
    context.add(Message.user("noise"))
    context.add(Message.assistant("more noise"))

    result = tool.execute({"action": "send", "checkpoint_id": 0, "message": "The answer is 42."})
    assert result.error is None

    # Context should have exactly the dmail message
    assert len(context.messages) == 1
    assert context.messages[0].role == "user"
    assert "The answer is 42." in context.messages[0].content


def test_execute_invalid_checkpoint(tool):
    result = tool.execute({"action": "send", "checkpoint_id": 7, "message": "hello"})
    assert result.error is not None
    assert "7" in result.error


def test_execute_removes_future_checkpoints(tool, context):
    tool.create_checkpoint()  # cp 0
    context.add(Message.user("a"))
    tool.create_checkpoint()  # cp 1
    context.add(Message.user("b"))
    tool.create_checkpoint()  # cp 2
    context.add(Message.user("c"))

    result = tool.execute({"action": "send", "checkpoint_id": 1, "message": "rewind"})
    assert result.error is None

    # cp 0 and cp 1 should still exist, cp 2 should be gone
    # Verify by trying to rewind to cp 2 — should fail
    result2 = tool.execute({"action": "send", "checkpoint_id": 2, "message": "should fail"})
    assert result2.error is not None
    assert "2" in result2.error

    # cp 0 should still be accessible
    # (but we'd need to add messages first to make this meaningful;
    #  just verify no error on a rewind to cp 0)
    context.add(Message.user("post-rewind"))
    result3 = tool.execute({"action": "send", "checkpoint_id": 0, "message": "back to start"})
    assert result3.error is None


def test_dmail_message_format(tool, context):
    context.add(Message.user("setup"))
    tool.create_checkpoint()
    context.add(Message.assistant("work work work"))

    tool.execute({"action": "send", "checkpoint_id": 0, "message": "Summary of findings."})

    dmail_msg = context.messages[-1]
    assert dmail_msg.content.startswith("[D-Mail from future self]")
    assert "Summary of findings." in dmail_msg.content


# ------------------------------------------------------------------
# Unbound context
# ------------------------------------------------------------------


def test_unbound_context_returns_error():
    tool = DMailTool()
    result = tool.execute({"action": "checkpoint"})
    assert result.error is not None
    assert "not bound" in result.error


def test_unbound_create_checkpoint_raises():
    tool = DMailTool()
    with pytest.raises(RuntimeError, match="not bound"):
        tool.create_checkpoint()


def test_send_requires_checkpoint_id_and_message(tool):
    result = tool.execute({"action": "send"})
    assert result.error is not None
    assert "'send' requires" in result.error


def test_unknown_action(tool):
    result = tool.execute({"action": "invalid"})
    assert result.error is not None
    assert "Unknown action" in result.error


# ------------------------------------------------------------------
# Agent integration
# ------------------------------------------------------------------


def test_agent_binds_context():
    from unittest.mock import MagicMock

    from chimera.core.agent import Agent

    provider = MagicMock()
    provider.complete.return_value = MagicMock(
        content="done",
        tool_calls=[],
        usage={"input_tokens": 0, "output_tokens": 0},
        has_tool_calls=False,
    )

    dmail = DMailTool()
    agent = Agent(provider=provider, tools=[dmail])
    agent.run("test", env=None)

    # After run, dmail should have been bound to a context
    assert dmail._context is not None
