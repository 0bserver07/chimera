"""Tests for SqliteEventStore snapshot persistence and snapshot-aware replay.

Snapshots let long-lived aggregates resume without replaying from seq=1.
This module verifies the round-trip (snapshot -> latest_snapshot), the
implicit ``replay(since_seq=None)`` resumption from the newest snapshot,
and that the ``from_seq`` legacy path still works unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.events.sourcing import (
    ProjectorRegistry,
    SqliteEventStore,
    ToolCalledEvent,
)
from chimera.events.sourcing.projector import Projector


class _SeqCollector(Projector):
    """Projector that records every (seq, tool_name) pair it sees."""

    name = "seq_collector"

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def apply(self, event_name: str, payload: Any) -> None:
        if event_name == "tool.called":
            self.seen.append((payload.call_id, payload.tool_name))


def _populate(store: SqliteEventStore, session: str, n: int) -> None:
    """Append *n* tool.called events with call ids ``c1`` … ``cN``."""
    for i in range(1, n + 1):
        store.append(
            session,
            ToolCalledEvent(
                session_id=session,
                call_id=f"c{i}",
                tool_name=f"t{i}",
            ),
        )


def test_latest_snapshot_returns_none_when_absent(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    assert store.latest_snapshot("s1") is None


def test_snapshot_round_trip(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    state = {"counters": {"tool.called": 3}, "last_tool": "bash"}
    store.snapshot("s1", seq=3, state=state)

    got = store.latest_snapshot("s1")
    assert got is not None
    seq, restored = got
    assert seq == 3
    assert restored == state


def test_snapshot_latest_picks_highest_seq(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.snapshot("s1", seq=10, state={"v": "ten"})
    store.snapshot("s1", seq=50, state={"v": "fifty"})
    store.snapshot("s1", seq=25, state={"v": "twenty-five"})  # out-of-order write

    got = store.latest_snapshot("s1")
    assert got == (50, {"v": "fifty"})


def test_snapshot_isolated_per_session(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    store.snapshot("s1", seq=5, state={"who": "s1"})
    store.snapshot("s2", seq=7, state={"who": "s2"})

    assert store.latest_snapshot("s1") == (5, {"who": "s1"})
    assert store.latest_snapshot("s2") == (7, {"who": "s2"})


def test_snapshot_rejects_negative_seq(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    with pytest.raises(ValueError):
        store.snapshot("s1", seq=-1, state={})


def test_snapshot_rejects_non_serializable_state(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "evt.db")
    with pytest.raises(TypeError):
        store.snapshot("s1", seq=1, state={"bad": object()})


def test_snapshot_persists_across_reopens(tmp_path: Path) -> None:
    db = tmp_path / "evt.db"
    store = SqliteEventStore(db)
    store.snapshot("s1", seq=42, state={"hello": "world"})
    store.close()

    store2 = SqliteEventStore(db)
    assert store2.latest_snapshot("s1") == (42, {"hello": "world"})


def test_replay_from_snapshot_skips_pre_snapshot_events(tmp_path: Path) -> None:
    """Write 100 events, snapshot at 50, verify replay covers only 51-100."""
    store = SqliteEventStore(tmp_path / "evt.db")
    _populate(store, "s1", 100)
    assert store.last_seq("s1") == 100

    # Pretend a projector folded events 1..50, captured state, snapshotted.
    store.snapshot("s1", seq=50, state={"folded_through": 50})

    reg = ProjectorRegistry()
    collector = _SeqCollector()
    reg.register(collector)
    # Cursor must reflect snapshot — otherwise the registry would re-fold
    # from 1.  This mirrors a real restart-from-snapshot flow.
    reg.set_cursor(collector.name, 50)

    n = store.replay("s1", reg)  # since_seq=None -> uses snapshot

    assert n == 50
    assert len(collector.seen) == 50
    seen_call_ids = [call_id for call_id, _ in collector.seen]
    assert seen_call_ids == [f"c{i}" for i in range(51, 101)]


def test_replay_explicit_since_seq_overrides_snapshot(tmp_path: Path) -> None:
    """``since_seq`` is honoured even when a newer snapshot exists."""
    store = SqliteEventStore(tmp_path / "evt.db")
    _populate(store, "s1", 10)
    store.snapshot("s1", seq=8, state={"folded_through": 8})

    reg = ProjectorRegistry()
    collector = _SeqCollector()
    reg.register(collector)

    # Force a full replay despite the snapshot.
    n = store.replay("s1", reg, since_seq=0)
    assert n == 10
    assert [c for c, _ in collector.seen] == [f"c{i}" for i in range(1, 11)]


def test_replay_no_snapshot_falls_back_to_from_seq(tmp_path: Path) -> None:
    """When no snapshot exists, ``since_seq=None`` honours the legacy
    ``from_seq`` parameter (back-compat for existing callers)."""
    store = SqliteEventStore(tmp_path / "evt.db")
    _populate(store, "s1", 5)

    reg = ProjectorRegistry()
    collector = _SeqCollector()
    reg.register(collector)
    reg.set_cursor(collector.name, 2)

    n = store.replay("s1", reg, from_seq=2)
    assert n == 3
    assert [c for c, _ in collector.seen] == ["c3", "c4", "c5"]


def test_replay_from_seq_one_still_works_with_snapshot_present(
    tmp_path: Path,
) -> None:
    """Existing callers that pass ``from_seq=0`` (and don't know about
    snapshots) keep working when ``since_seq`` is supplied explicitly."""
    store = SqliteEventStore(tmp_path / "evt.db")
    _populate(store, "s1", 6)
    store.snapshot("s1", seq=4, state={"folded_through": 4})

    reg = ProjectorRegistry()
    collector = _SeqCollector()
    reg.register(collector)

    # Explicit since_seq=0 means "replay from start", regardless of snapshot.
    n = store.replay("s1", reg, since_seq=0)
    assert n == 6
    assert [c for c, _ in collector.seen] == [f"c{i}" for i in range(1, 7)]


def test_snapshot_table_does_not_break_existing_tables(tmp_path: Path) -> None:
    """Sanity check: adding the snapshot table does not regress event I/O."""
    store = SqliteEventStore(tmp_path / "evt.db")
    _populate(store, "s1", 3)
    rows = list(store.read_since("s1"))
    assert len(rows) == 3
    assert [r.seq for r in rows] == [1, 2, 3]
