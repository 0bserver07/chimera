"""M4-D: TodoWriteEvent durability tests.

Verifies that every TodoTool mutation is journaled to the EventLog so
that ``EventSourcedSession.resume`` rebuilds the in-memory todo list,
even after HARD compaction or process restarts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.events.base import Event
from chimera.events.types import TodoWriteEvent
from chimera.sessions.eventlog.session import EventSourcedSession
from chimera.tools.todo import TodoTool
from chimera.types import AgentResult


# ======================================================================
# Helpers
# ======================================================================


def _make_agent_with_todo(tmp_path: Path) -> tuple[MagicMock, TodoTool]:
    """Build a mock Agent that owns a single non-persistent TodoTool.

    Persistence is disabled so each test starts from a clean in-memory
    list — the EventLog is the only source of truth being exercised.

    Args:
        tmp_path: pytest temp dir used as the TodoTool cwd.

    Returns:
        ``(agent, todo)`` — the mock agent and its TodoTool.
    """
    todo = TodoTool(cwd=str(tmp_path), persist=False)

    agent = MagicMock()
    agent.prompt.render.return_value = "system prompt"
    agent.tools = [todo]
    agent.loop.run.return_value = AgentResult(
        output="ok", steps=1, tool_calls_total=0, cost=0.0, success=True,
    )
    return agent, todo


def _new_agent_with_fresh_todo(tmp_path: Path) -> tuple[MagicMock, TodoTool]:
    """Like ``_make_agent_with_todo`` but returns a *brand new* TodoTool.

    Simulates a process restart — the todo list starts empty and must be
    rebuilt by event replay.
    """
    return _make_agent_with_todo(tmp_path)


# ======================================================================
# Tests
# ======================================================================


def test_resume_replays_todo_events(tmp_path: Path) -> None:
    """3 todos written, then session resumed: all 3 reappear."""
    log_dir = tmp_path / "logs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    agent_a, todo_a = _make_agent_with_todo(cwd)
    session = EventSourcedSession(
        agent=agent_a, log_dir=log_dir, session_id="s-3todos",
    )

    # Three writes — bypass execute() to keep the test focused on the
    # event path; execute() is exercised in test_todo.py.
    todo_a.execute({"action": "add", "task": "Alpha"})
    todo_a.execute({"action": "add", "task": "Beta"})
    todo_a.execute({"action": "add", "task": "Gamma"})

    # Three writes -> three TodoWriteEvent records on disk.
    todo_events = [
        e for e in session.event_log.get_range() if e.type == "todo_write"
    ]
    assert len(todo_events) == 3

    # Simulate /resume in a fresh process.
    agent_b, todo_b = _new_agent_with_fresh_todo(cwd)
    assert todo_b.items == []  # sanity: started empty

    resumed = EventSourcedSession.resume(
        log_dir=log_dir, session_id="s-3todos", agent=agent_b,
    )
    assert resumed is not None  # quiet unused-var warning

    tasks = [it.task for it in todo_b.items]
    assert tasks == ["Alpha", "Beta", "Gamma"]
    assert all(not it.done for it in todo_b.items)


def test_compact_keeps_todo_event_chain(tmp_path: Path) -> None:
    """HARD compaction does not touch the EventLog: todos still survive."""
    log_dir = tmp_path / "logs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    agent_a, todo_a = _make_agent_with_todo(cwd)
    session = EventSourcedSession(
        agent=agent_a,
        log_dir=log_dir,
        session_id="s-compact",
    )

    todo_a.execute({"action": "add", "task": "Survives compaction A"})
    todo_a.execute({"action": "add", "task": "Survives compaction B"})

    # Force a HARD compaction by blowing away all in-memory messages.
    # The EventLog is independent of the Context; this is the worst
    # case the contract has to handle.
    session._context._messages = []  # type: ignore[attr-defined]
    todo_a.execute({"action": "add", "task": "Post-compaction C"})

    # Resume from a fresh agent — todos must still be present.
    agent_b, todo_b = _new_agent_with_fresh_todo(cwd)
    EventSourcedSession.resume(
        log_dir=log_dir, session_id="s-compact", agent=agent_b,
    )

    tasks = [it.task for it in todo_b.items]
    assert tasks == [
        "Survives compaction A",
        "Survives compaction B",
        "Post-compaction C",
    ]


def test_event_ordering_preserved(tmp_path: Path) -> None:
    """add A, complete A, add B  ->  resume sees [A done, B pending]."""
    log_dir = tmp_path / "logs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    agent_a, todo_a = _make_agent_with_todo(cwd)
    EventSourcedSession(
        agent=agent_a, log_dir=log_dir, session_id="s-order",
    )

    todo_a.execute({"action": "add", "task": "A"})
    todo_a.execute({"action": "complete", "task": "1"})
    todo_a.execute({"action": "add", "task": "B"})

    agent_b, todo_b = _new_agent_with_fresh_todo(cwd)
    EventSourcedSession.resume(
        log_dir=log_dir, session_id="s-order", agent=agent_b,
    )

    items = todo_b.items
    assert len(items) == 2
    assert items[0].task == "A"
    assert items[0].done is True
    assert items[1].task == "B"
    assert items[1].done is False


# ======================================================================
# Bonus: direct event-shape checks (light)
# ======================================================================


def test_todo_write_event_payload_shape(tmp_path: Path) -> None:
    """TodoWriteEvent metadata round-trips through EventLog cleanly."""
    log_dir = tmp_path / "logs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    agent, todo = _make_agent_with_todo(cwd)
    session = EventSourcedSession(
        agent=agent, log_dir=log_dir, session_id="s-shape",
    )
    todo.execute({"action": "add", "task": "Inspect me"})

    events = [e for e in session.event_log.get_range() if e.type == "todo_write"]
    assert len(events) == 1
    ev = events[0]
    assert ev.metadata["op"] == "add"
    assert ev.metadata["session_id"] == "s-shape"
    assert ev.metadata["todos"] == [{"id": 1, "task": "Inspect me", "done": False}]


def test_apply_event_handles_base_event_form() -> None:
    """TodoTool.apply_event accepts both first-class and base-Event payloads."""
    todo = TodoTool(persist=False)
    base_form = Event(
        type="todo_write",
        metadata={
            "todos": [{"id": 7, "task": "from-disk", "done": True}],
            "op": "set",
            "session_id": "x",
        },
    )
    todo.apply_event(base_form)
    assert todo.items[0].id == 7
    assert todo.items[0].task == "from-disk"
    assert todo.items[0].done is True

    runtime_form = TodoWriteEvent(
        todos=[{"id": 9, "task": "runtime", "done": False}],
        op="add",
        session_id="x",
    )
    todo.apply_event(runtime_form)
    assert todo.items[0].id == 9
    assert todo.items[0].task == "runtime"


# ======================================================================
# Sanity: pre-existing TodoTool API still works
# ======================================================================


def test_no_event_bus_means_no_emit(tmp_path: Path) -> None:
    """Bare TodoTool without an attached bus must not raise on mutation."""
    todo = TodoTool(cwd=str(tmp_path), persist=False)
    # No EventBus attached -> _emit_write is a noop, mutation succeeds.
    result = todo.execute({"action": "add", "task": "lone wolf"})
    assert result.error is None
    assert todo.items[0].task == "lone wolf"


@pytest.mark.parametrize("op_args", [
    {"action": "add", "task": "x"},
    {"action": "complete", "task": "1"},
])
def test_emit_uses_attached_session_id(tmp_path: Path, op_args: dict[str, Any]) -> None:
    """attach_event_bus stamps session_id on every subsequent event."""
    from chimera.events.base import EventBus

    bus = EventBus()
    captured: list[Event] = []
    bus.subscribe("todo_write", captured.append)

    todo = TodoTool(cwd=str(tmp_path), persist=False)
    todo.attach_event_bus(bus, session_id="captured-sid")

    todo.execute({"action": "add", "task": "x"})  # ensure id 1 exists
    captured.clear()

    todo.execute(op_args)
    assert captured, "expected a todo_write event"
    ev = captured[-1]
    assert getattr(ev, "session_id", "") == "captured-sid"
