"""Tests for chimera.core.agent_context — AgentContext with isolation levels."""
from __future__ import annotations

import pytest

from chimera.core.abort import AbortSignal
from chimera.core.agent_context import AgentContext, IsolationLevel
from chimera.core.loop_state import QuerySource


# ---------------------------------------------------------------------------
# Test 1: IsolationLevel enum values
# ---------------------------------------------------------------------------


def test_isolation_level_values():
    assert IsolationLevel.FULL.value == "full"
    assert IsolationLevel.SELECTIVE.value == "selective"
    assert IsolationLevel.SHARED.value == "shared"


# ---------------------------------------------------------------------------
# Test 2: AgentContext creation with all fields
# ---------------------------------------------------------------------------


def test_agent_context_creation():
    abort = AbortSignal()
    ctx = AgentContext(
        messages=[{"role": "user", "content": "hi"}],
        file_state_cache={"file.py": "cached"},
        abort_signal=abort,
        denial_tracking={"tool_x": 2},
        agent_id="agent-1",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda updater: None,
        set_app_state_for_tasks=lambda updater: None,
    )

    assert ctx.messages == [{"role": "user", "content": "hi"}]
    assert ctx.file_state_cache == {"file.py": "cached"}
    assert ctx.abort_signal is abort
    assert ctx.denial_tracking == {"tool_x": 2}
    assert ctx.agent_id == "agent-1"
    assert ctx.parent_agent_id is None
    assert ctx.query_source == QuerySource.FOREGROUND
    assert ctx.depth == 0


# ---------------------------------------------------------------------------
# Test 3: create_child with FULL isolation
# ---------------------------------------------------------------------------


def test_create_child_full_isolation():
    state_mutations = []

    def parent_set_app_state(updater):
        state_mutations.append(("parent", updater))

    def parent_set_app_state_for_tasks(updater):
        state_mutations.append(("tasks", updater))

    parent_abort = AbortSignal()
    parent = AgentContext(
        messages=[{"role": "user", "content": "hello"}],
        file_state_cache={"a.py": "content_a"},
        abort_signal=parent_abort,
        denial_tracking={"bash": 3},
        agent_id="parent-1",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {"key": "value"},
        set_app_state=parent_set_app_state,
        set_app_state_for_tasks=parent_set_app_state_for_tasks,
    )

    child = AgentContext.create_child(parent, isolation=IsolationLevel.FULL)

    # Messages should be cloned (independent copy)
    assert child.messages == parent.messages
    child.messages.append({"role": "assistant", "content": "bye"})
    assert len(parent.messages) == 1  # Parent unaffected

    # File state cache should be cloned
    assert child.file_state_cache == {"a.py": "content_a"}
    child.file_state_cache["b.py"] = "new"
    assert "b.py" not in parent.file_state_cache

    # Denial tracking should be fresh/empty
    assert child.denial_tracking == {}

    # Parent/child relationship
    assert child.parent_agent_id == "parent-1"
    assert child.agent_id != parent.agent_id

    # Depth incremented
    assert child.depth == 1

    # set_app_state is no-op in FULL isolation
    child.set_app_state(lambda s: s)
    assert len(state_mutations) == 0

    # set_app_state_for_tasks always uses parent's callback
    child.set_app_state_for_tasks(lambda s: s)
    assert len(state_mutations) == 1
    assert state_mutations[0][0] == "tasks"


# ---------------------------------------------------------------------------
# Test 4: create_child with SHARED isolation
# ---------------------------------------------------------------------------


def test_create_child_shared_isolation():
    state_mutations = []

    def parent_set_app_state(updater):
        state_mutations.append(("parent", updater))

    parent_abort = AbortSignal()
    parent = AgentContext(
        messages=[{"role": "user", "content": "hello"}],
        file_state_cache={"a.py": "content_a"},
        abort_signal=parent_abort,
        denial_tracking={"bash": 3},
        agent_id="parent-1",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=parent_set_app_state,
        set_app_state_for_tasks=lambda updater: None,
    )

    child = AgentContext.create_child(parent, isolation=IsolationLevel.SHARED)

    # set_app_state should use parent's callback in SHARED mode
    child.set_app_state(lambda s: s)
    assert len(state_mutations) == 1
    assert state_mutations[0][0] == "parent"


# ---------------------------------------------------------------------------
# Test 5: create_child abort signal linking
# ---------------------------------------------------------------------------


def test_create_child_abort_signal_default_not_shared():
    parent_abort = AbortSignal()
    parent = AgentContext(
        messages=[],
        file_state_cache={},
        abort_signal=parent_abort,
        denial_tracking={},
        agent_id="p",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda u: None,
        set_app_state_for_tasks=lambda u: None,
    )

    child = AgentContext.create_child(parent, share_abort=False)

    # Child should have a fresh, independent abort signal
    assert child.abort_signal is not parent_abort
    assert not child.abort_signal.aborted

    # Aborting parent should NOT abort child when share_abort=False
    parent_abort.abort("test")
    assert not child.abort_signal.aborted


def test_create_child_abort_signal_shared():
    parent_abort = AbortSignal()
    parent = AgentContext(
        messages=[],
        file_state_cache={},
        abort_signal=parent_abort,
        denial_tracking={},
        agent_id="p",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda u: None,
        set_app_state_for_tasks=lambda u: None,
    )

    child = AgentContext.create_child(parent, share_abort=True)

    # Child should have a linked abort signal
    assert child.abort_signal is not parent_abort
    assert not child.abort_signal.aborted

    # Aborting parent SHOULD abort child when share_abort=True
    parent_abort.abort("cascade")
    assert child.abort_signal.aborted
    assert child.abort_signal.reason == "cascade"


# ---------------------------------------------------------------------------
# Test 6: create_child depth chaining
# ---------------------------------------------------------------------------


def test_create_child_depth_chaining():
    root = AgentContext(
        messages=[],
        file_state_cache={},
        abort_signal=AbortSignal(),
        denial_tracking={},
        agent_id="root",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda u: None,
        set_app_state_for_tasks=lambda u: None,
    )

    child = AgentContext.create_child(root)
    grandchild = AgentContext.create_child(child)

    assert root.depth == 0
    assert child.depth == 1
    assert grandchild.depth == 2
    assert grandchild.parent_agent_id == child.agent_id


# ---------------------------------------------------------------------------
# Test 7: create_child with SELECTIVE isolation
# ---------------------------------------------------------------------------


def test_create_child_selective_isolation():
    state_mutations = []

    def parent_set_app_state(updater):
        state_mutations.append(("parent", updater))

    parent = AgentContext(
        messages=[{"role": "user", "content": "hello"}],
        file_state_cache={"a.py": "content_a"},
        abort_signal=AbortSignal(),
        denial_tracking={"bash": 1},
        agent_id="parent-1",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=parent_set_app_state,
        set_app_state_for_tasks=lambda u: None,
    )

    child = AgentContext.create_child(parent, isolation=IsolationLevel.SELECTIVE)

    # Messages cloned
    assert child.messages == parent.messages
    child.messages.append({"role": "assistant", "content": "x"})
    assert len(parent.messages) == 1

    # File state cache cloned
    assert child.file_state_cache == parent.file_state_cache

    # Denial tracking fresh
    assert child.denial_tracking == {}

    # set_app_state uses parent's callback in SELECTIVE mode
    child.set_app_state(lambda s: s)
    assert len(state_mutations) == 1


# ---------------------------------------------------------------------------
# Test 8: Unique agent_id generation
# ---------------------------------------------------------------------------


def test_create_child_unique_agent_ids():
    parent = AgentContext(
        messages=[],
        file_state_cache={},
        abort_signal=AbortSignal(),
        denial_tracking={},
        agent_id="parent",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda u: None,
        set_app_state_for_tasks=lambda u: None,
    )

    children = [AgentContext.create_child(parent) for _ in range(5)]
    agent_ids = [c.agent_id for c in children]

    # All agent_ids should be unique
    assert len(set(agent_ids)) == 5
    # None should match parent
    assert all(aid != "parent" for aid in agent_ids)
