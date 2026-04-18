"""Integration tests for SessionTree wired into Session."""
from __future__ import annotations

import pytest

from chimera.sessions.tree import SessionTree
from chimera.sessions.session import Session
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.providers.base import Provider, Response


class MockProvider(Provider):
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content="mock reply", tool_calls=[], usage={"input_tokens": 5, "output_tokens": 3})

    @property
    def context_window(self) -> int:
        return 1000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock"


def _make_session(tree=None) -> Session:
    agent = Agent(provider=MockProvider(), loop=ReAct(max_steps=5))
    return Session(agent=agent, tree=tree)


def test_session_with_tree(tmp_path):
    """Session with a tree tracks user and assistant messages."""
    tree = SessionTree(tmp_path / "session.jsonl")
    session = _make_session(tree=tree)
    result = session.chat("hello")
    assert result.output == "mock reply"
    assert tree.entry_count >= 2  # user + assistant


def test_session_without_tree():
    """Session without a tree works identically to before."""
    session = _make_session()
    result = session.chat("hello")
    assert result.output == "mock reply"


def test_session_tree_persists(tmp_path):
    """Messages written via Session are readable from a freshly loaded tree."""
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    session = _make_session(tree=tree)
    session.chat("hello")

    # Reload the tree from disk
    tree2 = SessionTree(path)
    msgs = tree2.get_messages()
    assert any(m.content == "hello" for m in msgs)
    assert any(m.content == "mock reply" for m in msgs)


def test_session_tree_user_message_recorded(tmp_path):
    """User message is the first entry in the tree branch."""
    tree = SessionTree(tmp_path / "s.jsonl")
    session = _make_session(tree=tree)
    session.chat("first message")
    msgs = tree.get_messages()
    assert msgs[0].role == "user"
    assert msgs[0].content == "first message"


def test_session_tree_assistant_message_recorded(tmp_path):
    """Assistant reply is recorded in the tree after the user message."""
    tree = SessionTree(tmp_path / "s.jsonl")
    session = _make_session(tree=tree)
    session.chat("ping")
    msgs = tree.get_messages()
    assert any(m.role == "assistant" and m.content == "mock reply" for m in msgs)


def test_session_tree_multi_turn(tmp_path):
    """Multiple chat() calls accumulate entries in the tree."""
    tree = SessionTree(tmp_path / "s.jsonl")
    session = _make_session(tree=tree)
    session.chat("turn 1")
    session.chat("turn 2")
    msgs = tree.get_messages()
    # 2 user + 2 assistant = 4 messages
    assert len(msgs) >= 4
    user_msgs = [m for m in msgs if m.role == "user"]
    assert len(user_msgs) == 2


def test_switch_branch_rebuilds_context(tmp_path):
    """switch_branch() replaces the session context with the branch's messages."""
    tree = SessionTree(tmp_path / "s.jsonl")
    session = _make_session(tree=tree)

    # Add a message to establish a base entry
    session.chat("hello")
    leaves_after_first = tree.get_leaves()
    assert len(leaves_after_first) == 1
    first_leaf = leaves_after_first[0]

    # Fork to a new branch and add another message
    tree.fork(first_leaf)
    session.chat("second branch message")

    # Now switch back to the first leaf — context should reflect that branch
    session.switch_branch(first_leaf)
    # After switching, context messages should come from the first branch only
    msgs = session.context.messages
    contents = [m.content for m in msgs]
    assert "hello" in contents
    assert "second branch message" not in contents


def test_switch_branch_invalid_id(tmp_path):
    """switch_branch() with an unknown ID raises ValueError."""
    tree = SessionTree(tmp_path / "s.jsonl")
    session = _make_session(tree=tree)
    with pytest.raises(ValueError):
        session.switch_branch("nonexistent-id")


def test_switch_branch_no_tree():
    """switch_branch() is a no-op (no error) when no tree is attached."""
    session = _make_session()
    # Should not raise even without a tree
    session.switch_branch("any-id")


def test_tree_none_attribute():
    """Session._tree is None when tree parameter is not provided."""
    session = _make_session()
    assert session._tree is None


def test_tree_attribute_set(tmp_path):
    """Session._tree is the passed SessionTree instance."""
    tree = SessionTree(tmp_path / "s.jsonl")
    session = _make_session(tree=tree)
    assert session._tree is tree
