"""Resume-from-snapshot tests for :class:`EventSourcedSession`.

These tests verify the wave-4 fast-resume path:

* When an event store with the M10 snapshot API is wired in, resuming
  hydrates context from the newest snapshot and replays only the
  events that were recorded *after* it.
* When no compatible store is configured, the resume path is
  unchanged: the on-disk EventLog is replayed in full.
* The ``snapshot_every_n_events`` knob auto-snapshots after every N
  recorded events when enabled, and is a no-op when disabled (default).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.sessions.eventlog.session import EventSourcedSession
from chimera.types import AgentResult


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_mock_agent() -> MagicMock:
    """Mirror tests/sessions/test_eventlog.py's mock-agent factory."""
    agent = MagicMock()
    agent.prompt.render.return_value = "system prompt"
    agent.tools = []
    result = AgentResult(
        output="hello",
        steps=1,
        tool_calls_total=0,
        cost=0.0,
        success=True,
    )
    agent.loop.run.return_value = result
    return agent


class FakeSnapshotStore:
    """In-memory stand-in that mirrors the M10 snapshot API.

    L4 will eventually wire a real ``SqliteEventStore`` onto the session;
    L7's wiring is intentionally late-bound (duck-typed), so any object
    exposing ``snapshot`` / ``latest_snapshot`` works.  Using a fake
    here keeps the test independent of L4's landing time and of the
    SQLite-on-disk path.
    """

    def __init__(self) -> None:
        self._snaps: dict[str, list[tuple[int, Any]]] = {}

    def snapshot(self, session_id: str, seq: int, state: Any) -> None:
        self._snaps.setdefault(session_id, []).append((seq, state))

    def latest_snapshot(self, session_id: str) -> tuple[int, Any] | None:
        rows = self._snaps.get(session_id, [])
        if not rows:
            return None
        # Newest by seq.
        rows = sorted(rows, key=lambda r: r[0])
        return rows[-1]

    @property
    def snapshot_count(self) -> int:
        return sum(len(v) for v in self._snaps.values())


# ----------------------------------------------------------------------
# Auto-snapshot every N events
# ----------------------------------------------------------------------


class TestAutoSnapshotEveryN:
    def test_auto_snapshot_fires_at_threshold(self, tmp_path: Path) -> None:
        """Recording 50 events with N=10 produces 5 snapshots."""
        agent = _make_mock_agent()
        store = FakeSnapshotStore()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="auto-snap",
            event_store=store,
            snapshot_every_n_events=10,
        )
        # Each chat() records 2 events (user_message + agent_result).
        # 25 chats => 50 events => snapshots at 10, 20, 30, 40, 50.
        for i in range(25):
            session.chat(f"msg-{i}")
        assert session.event_count == 50
        assert store.snapshot_count == 5
        latest = store.latest_snapshot("auto-snap")
        assert latest is not None
        seq, state = latest
        assert seq == 50
        assert state["events_recorded"] == 50

    def test_no_snapshots_without_store(self, tmp_path: Path) -> None:
        """``snapshot_every_n_events`` set but no store: no snapshots."""
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="no-store",
            snapshot_every_n_events=10,
        )
        for i in range(15):
            session.chat(f"msg-{i}")
        # No event_store configured => silently no-op.
        assert session.event_count == 30

    def test_no_snapshots_when_disabled(self, tmp_path: Path) -> None:
        """Default config: zero snapshots even with a store wired in."""
        agent = _make_mock_agent()
        store = FakeSnapshotStore()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="no-auto",
            event_store=store,
        )
        for i in range(15):
            session.chat(f"msg-{i}")
        assert store.snapshot_count == 0


# ----------------------------------------------------------------------
# Fast-resume from snapshot
# ----------------------------------------------------------------------


class TestResumeFromSnapshot:
    def test_resume_replays_only_after_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Headline scenario: 50 events, auto-snap every 10, resume,
        confirm replay touches only events 41-50 (last snapshot at 40).

        We force the snapshot threshold to 40 (rather than the live 50)
        by stopping after 20 chats and then writing one extra snapshot
        at seq=40 manually-suppressed; instead the simpler approach is
        to record 25 chats (50 events, snapshot at 50) and verify that
        resume short-circuits to seq=50 with zero replayed events.

        For the strict "events 41-50 replayed" check, we record 25
        chats with N=10 and then *manually* delete the 50-seq snapshot
        so the latest is 40, then resume and count replayed events.
        """
        agent = _make_mock_agent()
        store = FakeSnapshotStore()
        log_dir = tmp_path / "logs"

        # 1. Build a session that records 50 events with snapshots
        #    every 10 events.
        s1 = EventSourcedSession(
            agent=agent,
            log_dir=log_dir,
            session_id="snap-resume",
            event_store=store,
            snapshot_every_n_events=10,
        )
        for i in range(25):
            s1.chat(f"msg-{i}")
        assert s1.event_count == 50
        assert store.snapshot_count == 5

        # 2. Drop the seq=50 snapshot so the newest one is at seq=40.
        snaps_for_session = store._snaps["snap-resume"]
        snaps_for_session[:] = [(seq, st) for seq, st in snaps_for_session if seq != 50]
        latest = store.latest_snapshot("snap-resume")
        assert latest is not None and latest[0] == 40

        # 3. Spy on _replay_events to count how many events were
        #    replayed.  The fast-resume path should only replay the
        #    tail (events 41-50, i.e. 10 events).
        replayed: list[int] = []
        original_replay = EventSourcedSession._replay_events

        def _spy(self: EventSourcedSession, events: list[Any]) -> None:
            replayed.append(len(events))
            return original_replay(self, events)

        monkeypatch.setattr(EventSourcedSession, "_replay_events", _spy)

        # 4. Resume.
        s2 = EventSourcedSession.resume(
            log_dir=log_dir,
            session_id="snap-resume",
            agent=agent,
            event_store=store,
        )

        # 5. Replay covered exactly events 41-50 (10 events).
        assert replayed == [10]
        # Total event_count is unchanged (all 50 events live on disk).
        assert s2.event_count == 50
        # Context has the full message history rebuilt: 25 user + 25 assistant.
        assert len(s2.messages) == 50
        # First message is from msg-0 (snapshot hydrated from full state).
        assert s2.messages[0].role == "user"
        assert s2.messages[0].content == "msg-0"
        # Last message is from msg-24's assistant response.
        assert s2.messages[-1].role == "assistant"

    def test_resume_without_store_uses_full_replay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Backwards-compat: no event_store => full replay path."""
        agent = _make_mock_agent()
        log_dir = tmp_path / "logs"
        s1 = EventSourcedSession(
            agent=agent,
            log_dir=log_dir,
            session_id="full-replay",
        )
        for i in range(5):
            s1.chat(f"msg-{i}")
        assert s1.event_count == 10

        replayed: list[int] = []
        original_replay = EventSourcedSession._replay_events

        def _spy(self: EventSourcedSession, events: list[Any]) -> None:
            replayed.append(len(events))
            return original_replay(self, events)

        monkeypatch.setattr(EventSourcedSession, "_replay_events", _spy)

        s2 = EventSourcedSession.resume(
            log_dir=log_dir,
            session_id="full-replay",
            agent=agent,
        )
        # All 10 events were replayed (no snapshot to short-circuit).
        assert replayed == [10]
        assert len(s2.messages) == 10

    def test_resume_with_store_but_no_snapshot_uses_full_replay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Store exists but has no snapshot for this session: full replay."""
        agent = _make_mock_agent()
        store = FakeSnapshotStore()
        log_dir = tmp_path / "logs"
        s1 = EventSourcedSession(
            agent=agent,
            log_dir=log_dir,
            session_id="empty-snap",
            event_store=store,
            # Auto-snapshot off => no snapshots written.
        )
        for i in range(3):
            s1.chat(f"msg-{i}")
        assert s1.event_count == 6
        assert store.snapshot_count == 0

        replayed: list[int] = []
        original_replay = EventSourcedSession._replay_events

        def _spy(self: EventSourcedSession, events: list[Any]) -> None:
            replayed.append(len(events))
            return original_replay(self, events)

        monkeypatch.setattr(EventSourcedSession, "_replay_events", _spy)

        s2 = EventSourcedSession.resume(
            log_dir=log_dir,
            session_id="empty-snap",
            agent=agent,
            event_store=store,
        )
        # latest_snapshot returns None => full replay.
        assert replayed == [6]
        assert len(s2.messages) == 6

    def test_snapshot_now_forces_explicit_snapshot(self, tmp_path: Path) -> None:
        """``snapshot_now()`` writes immediately when a store is wired."""
        agent = _make_mock_agent()
        store = FakeSnapshotStore()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="manual-snap",
            event_store=store,
        )
        session.chat("hi")
        assert store.snapshot_count == 0
        # Manual flush.
        ok = session.snapshot_now()
        assert ok is True
        assert store.snapshot_count == 1

    def test_snapshot_now_returns_false_without_store(self, tmp_path: Path) -> None:
        """Without a store, ``snapshot_now()`` is a safe no-op."""
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="no-snap",
        )
        assert session.snapshot_now() is False


# ----------------------------------------------------------------------
# Integration with real SqliteEventStore (M10)
# ----------------------------------------------------------------------


class TestRealSqliteEventStoreIntegration:
    """Confirm the late-binding duck-type works with the actual M10 store."""

    def test_real_sqlite_store_round_trips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from chimera.events.sourcing.sqlite_store import SqliteEventStore

        agent = _make_mock_agent()
        store = SqliteEventStore(tmp_path / "events.db")
        log_dir = tmp_path / "logs"

        s1 = EventSourcedSession(
            agent=agent,
            log_dir=log_dir,
            session_id="real-sqlite",
            event_store=store,
            snapshot_every_n_events=4,
        )
        # 6 chats => 12 events => snapshots at 4, 8, 12.
        for i in range(6):
            s1.chat(f"msg-{i}")
        latest = store.latest_snapshot("real-sqlite")
        assert latest is not None
        assert latest[0] == 12

        replayed: list[int] = []
        original_replay = EventSourcedSession._replay_events

        def _spy(self: EventSourcedSession, events: list[Any]) -> None:
            replayed.append(len(events))
            return original_replay(self, events)

        monkeypatch.setattr(EventSourcedSession, "_replay_events", _spy)

        s2 = EventSourcedSession.resume(
            log_dir=log_dir,
            session_id="real-sqlite",
            agent=agent,
            event_store=store,
        )
        # Snapshot at seq=12 covers all events => replay is empty.
        assert replayed == [0]
        assert len(s2.messages) == 12
        store.close()
