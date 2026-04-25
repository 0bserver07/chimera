"""Tests for SqliteEventStore append / read / replay."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.events.sourcing import (
    ProjectorRegistry,
    SequenceMismatchError,
    SqliteEventStore,
    ToolCalledEvent,
    ToolCompletedEvent,
)
from chimera.events.sourcing.projector import Projector


class _ToolEventCollector(Projector):
    name = "tool_events"

    def __init__(self) -> None:
        self.calls: list[ToolCalledEvent] = []
        self.completes: list[ToolCompletedEvent] = []

    def apply(self, event_name: str, payload: Any) -> None:
        if event_name == "tool.called":
            self.calls.append(payload)
        elif event_name == "tool.completed":
            self.completes.append(payload)


def test_append_assigns_monotonic_seq(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    a = store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="bash"))
    b = store.append("s1", ToolCalledEvent(session_id="s1", call_id="c2", tool_name="bash"))
    assert a.seq == 1
    assert b.seq == 2
    assert store.last_seq("s1") == 2


def test_append_separate_aggregates_have_independent_seq(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    a = store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="bash"))
    b = store.append("s2", ToolCalledEvent(session_id="s2", call_id="c2", tool_name="bash"))
    assert a.seq == 1
    assert b.seq == 1


def test_read_since_typed_payload(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="bash"))
    events = list(store.read_since("s1"))
    assert len(events) == 1
    payload = events[0].payload
    assert isinstance(payload, ToolCalledEvent)
    assert payload.tool_name == "bash"


def test_read_since_filter_by_seq(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="a"))
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c2", tool_name="b"))
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c3", tool_name="c"))
    events = list(store.read_since("s1", from_seq=1))
    assert [e.seq for e in events] == [2, 3]


def test_replay_drives_projector(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="bash"))
    store.append("s1", ToolCompletedEvent(
        session_id="s1", call_id="c1", tool_name="bash",
        success=True, output="ok",
    ))
    reg = ProjectorRegistry()
    collector = _ToolEventCollector()
    reg.register(collector)
    n = store.replay("s1", reg)
    assert n == 2
    assert len(collector.calls) == 1
    assert collector.calls[0].tool_name == "bash"
    assert len(collector.completes) == 1
    assert collector.completes[0].success is True


def test_replay_idempotent_via_from_seq(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="a"))
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c2", tool_name="b"))
    reg = ProjectorRegistry()
    collector = _ToolEventCollector()
    reg.register(collector)
    store.replay("s1", reg)
    # Replay again from current cursor should be a no-op.
    store.replay("s1", reg, from_seq=reg.cursor_for("tool_events"))
    assert len(collector.calls) == 2


def test_sequence_mismatch_raises(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="a"))
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c2", tool_name="b"))

    # Manually delete the row at seq=1 to create a gap.
    store._conn.execute(  # noqa: SLF001
        "DELETE FROM events WHERE aggregate_id = 's1' AND seq = 1;",
    )

    reg = ProjectorRegistry()
    reg.register(_ToolEventCollector())
    with pytest.raises(SequenceMismatchError):
        store.replay("s1", reg)


def test_persists_across_reopens(tmp_path: Path) -> None:
    db = tmp_path / "evt.db"
    store = SqliteEventStore(db)
    store.append("s1", ToolCalledEvent(session_id="s1", call_id="c1", tool_name="bash"))
    store.close()

    store2 = SqliteEventStore(db)
    assert store2.last_seq("s1") == 1
    events = list(store2.read_since("s1"))
    assert len(events) == 1
    assert events[0].payload.tool_name == "bash"  # type: ignore[attr-defined]
