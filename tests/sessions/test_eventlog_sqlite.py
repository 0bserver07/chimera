"""SQLite-backed :class:`EventSourcedSession` (M-L4).

These tests cover the SQLite primary-journal path added in wave 4:

* construction via ``store=SqliteEventStore(...)`` and via the
  :meth:`EventSourcedSession.with_sqlite` classmethod;
* full append + replay round trip on the SQLite backend;
* fast-resume from a snapshot at seq=N — the snapshot's messages seed
  the context, and only events with ``seq > N`` are replayed;
* backwards-compat guard: passing both ``log_dir`` and ``store`` (or
  neither) is rejected.

The existing JSONL tests in ``test_eventlog.py`` keep guarding the
default path; we don't repeat that coverage here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.events.sourcing.sqlite_store import SqliteEventStore
from chimera.events.sourcing.types import AgentResultEvent, UserMessageEvent
from chimera.sessions.eventlog.log import EventLog
from chimera.sessions.eventlog.session import EventSourcedSession
from chimera.types import AgentResult


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_mock_agent(reply: str = "hello") -> MagicMock:
    """Mock Agent matching tests/sessions/test_eventlog.py's factory."""
    agent = MagicMock()
    agent.prompt.render.return_value = "system prompt"
    agent.tools = []
    result = AgentResult(
        output=reply,
        steps=1,
        tool_calls_total=0,
        cost=0.0,
        success=True,
    )
    agent.loop.run.return_value = result
    return agent


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


class TestConstruction:
    def test_with_sqlite_creates_session_on_disk(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        db = tmp_path / "events.db"
        session = EventSourcedSession.with_sqlite(
            db, agent=agent, session_id="s1",
        )
        assert session.uses_sqlite is True
        assert isinstance(session.sqlite_store, SqliteEventStore)
        assert session.event_count == 0
        assert db.exists()

    def test_with_sqlite_in_memory(self) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession.with_sqlite(
            ":memory:", agent=agent, session_id="mem-1",
        )
        assert session.uses_sqlite is True
        assert session.event_count == 0

    def test_explicit_store_keyword(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        store = SqliteEventStore(tmp_path / "events.db")
        session = EventSourcedSession(
            agent=agent, store=store, session_id="explicit",
        )
        assert session.sqlite_store is store

    def test_event_log_property_unavailable_on_sqlite(self) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession.with_sqlite(
            ":memory:", agent=agent, session_id="x",
        )
        with pytest.raises(AttributeError, match="event_log"):
            _ = session.event_log

    def test_sqlite_store_property_unavailable_on_jsonl(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent, log_dir=tmp_path / "logs", session_id="j-1",
        )
        with pytest.raises(AttributeError, match="sqlite_store"):
            _ = session.sqlite_store
        # And uses_sqlite is False.
        assert session.uses_sqlite is False

    def test_neither_log_dir_nor_store_rejected(self) -> None:
        agent = _make_mock_agent()
        with pytest.raises(ValueError, match="either `log_dir` or `store`"):
            EventSourcedSession(agent=agent, session_id="bad")

    def test_both_log_dir_and_store_rejected(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        store = SqliteEventStore(":memory:")
        with pytest.raises(ValueError, match="either `log_dir` or `store`, not both"):
            EventSourcedSession(
                agent=agent,
                log_dir=tmp_path / "logs",
                store=store,
                session_id="bad",
            )

    def test_explicit_eventlog_via_store_keyword(self, tmp_path: Path) -> None:
        """Passing an :class:`EventLog` via ``store=`` uses the JSONL path."""
        agent = _make_mock_agent()
        log = EventLog(tmp_path / "custom-loc")
        session = EventSourcedSession(
            agent=agent, store=log, session_id="custom",
        )
        assert session.uses_sqlite is False
        assert session.event_log is log


# ----------------------------------------------------------------------
# Round trip: chat -> typed events -> resume
# ----------------------------------------------------------------------


class TestSqliteRoundTrip:
    def test_chat_appends_typed_events(self) -> None:
        agent = _make_mock_agent(reply="ack")
        session = EventSourcedSession.with_sqlite(
            ":memory:", agent=agent, session_id="rt",
        )
        result = session.chat("ping")
        assert result.output == "ack"
        # 2 typed events: UserMessageEvent + AgentResultEvent.
        assert session.event_count == 2

        events = list(session.sqlite_store.read_since("rt", from_seq=0))
        assert len(events) == 2
        assert isinstance(events[0].payload, UserMessageEvent)
        assert events[0].payload.content == "ping"
        assert isinstance(events[1].payload, AgentResultEvent)
        assert events[1].payload.output == "ack"

    def test_resume_replays_full_stream(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        db = tmp_path / "events.db"

        # Phase 1: populate.
        s1 = EventSourcedSession.with_sqlite(
            db, agent=agent, session_id="rt-resume",
        )
        s1.chat("first")
        s1.chat("second")
        assert s1.event_count == 4

        # Phase 2: resume in a fresh process-equivalent instance.
        agent2 = _make_mock_agent()
        store2 = SqliteEventStore(db)
        s2 = EventSourcedSession.resume(
            session_id="rt-resume",
            agent=agent2,
            store=store2,
        )
        assert s2.event_count == 4
        # 2 user + 2 assistant messages reconstructed in order.
        msgs = s2.messages
        assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert msgs[0].content == "first"
        assert msgs[2].content == "second"


# ----------------------------------------------------------------------
# Snapshot fast-resume (M10 + M-L4)
# ----------------------------------------------------------------------


class TestSnapshotFastResume:
    def test_snapshot_at_n_then_resume_replays_only_tail(
        self, tmp_path: Path,
    ) -> None:
        """Snapshot after 4 events; resume hydrates state and replays seq>4."""
        agent = _make_mock_agent()
        db = tmp_path / "events.db"

        s1 = EventSourcedSession.with_sqlite(
            db, agent=agent, session_id="fast",
        )
        s1.chat("a")     # seq 1, 2
        s1.chat("b")     # seq 3, 4
        # Manually snapshot at seq=4.
        s1.snapshot_now()
        assert s1.event_count == 4

        # Append more events post-snapshot.
        s1.chat("c")     # seq 5, 6
        s1.chat("d")     # seq 7, 8
        assert s1.event_count == 8

        # Resume.  Snapshot exists at seq=4 — context should be hydrated
        # from the snapshot and only seq>4 replayed.
        store2 = SqliteEventStore(db)
        snap = store2.latest_snapshot("fast")
        assert snap is not None
        assert snap[0] == 4

        s2 = EventSourcedSession.resume(
            session_id="fast",
            agent=_make_mock_agent(),
            store=store2,
        )
        assert s2.event_count == 8
        msgs = s2.messages
        # Full conversation reconstructed despite skipping replay of seq 1-4.
        contents = [m.content for m in msgs if m.role == "user"]
        assert contents == ["a", "b", "c", "d"]

    def test_resume_without_snapshot_replays_from_zero(
        self, tmp_path: Path,
    ) -> None:
        agent = _make_mock_agent()
        db = tmp_path / "events.db"

        s1 = EventSourcedSession.with_sqlite(
            db, agent=agent, session_id="no-snap",
        )
        s1.chat("only")
        assert s1.event_count == 2
        # No snapshot taken.

        store2 = SqliteEventStore(db)
        assert store2.latest_snapshot("no-snap") is None
        s2 = EventSourcedSession.resume(
            session_id="no-snap",
            agent=_make_mock_agent(),
            store=store2,
        )
        msgs = s2.messages
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].content == "only"

    def test_auto_snapshot_every_n(self) -> None:
        """``snapshot_every_n_events=4`` snapshots at seq 4, 8, 12 …"""
        agent = _make_mock_agent()
        session = EventSourcedSession.with_sqlite(
            ":memory:",
            agent=agent,
            session_id="auto",
            snapshot_every_n_events=4,
        )
        # 10 chats => 20 events => snapshots at 4, 8, 12, 16, 20.
        for i in range(10):
            session.chat(f"m-{i}")
        latest = session.sqlite_store.latest_snapshot("auto")
        assert latest is not None
        assert latest[0] == 20

    def test_resume_after_auto_snapshot_full_state(self, tmp_path: Path) -> None:
        """End-to-end: create, auto-snapshot, resume from N+1, assert state."""
        agent = _make_mock_agent()
        db = tmp_path / "events.db"

        s1 = EventSourcedSession.with_sqlite(
            db,
            agent=agent,
            session_id="e2e",
            snapshot_every_n_events=2,  # snapshot every 2 events => after each chat
        )
        for i in range(3):
            s1.chat(f"msg-{i}")
        # Each chat appends 2 events => 6 events total; snapshots at 2,4,6.
        assert s1.event_count == 6
        assert s1.sqlite_store.latest_snapshot("e2e")[0] == 6  # type: ignore[index]

        # Append one more chat post-snapshot to verify the fast-resume
        # also picks up the tail.
        s1.chat("after-snap")
        assert s1.event_count == 8

        store2 = SqliteEventStore(db)
        s2 = EventSourcedSession.resume(
            session_id="e2e",
            agent=_make_mock_agent(),
            store=store2,
        )
        assert s2.event_count == 8
        contents = [m.content for m in s2.messages if m.role == "user"]
        assert contents == ["msg-0", "msg-1", "msg-2", "after-snap"]


# ----------------------------------------------------------------------
# resume() validation
# ----------------------------------------------------------------------


class TestResumeValidation:
    def test_resume_requires_session_id(self) -> None:
        agent = _make_mock_agent()
        store = SqliteEventStore(":memory:")
        with pytest.raises(ValueError, match="non-empty session_id"):
            EventSourcedSession.resume(store=store, agent=agent)

    def test_resume_requires_agent(self) -> None:
        store = SqliteEventStore(":memory:")
        with pytest.raises(ValueError, match="requires an agent"):
            EventSourcedSession.resume(store=store, session_id="s")

    def test_resume_rejects_neither(self) -> None:
        agent = _make_mock_agent()
        with pytest.raises(ValueError, match="either `log_dir` or `store`"):
            EventSourcedSession.resume(session_id="s", agent=agent)

    def test_resume_rejects_both(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        store = SqliteEventStore(":memory:")
        # Pre-create the directory so the log_dir branch wouldn't fail
        # on "missing log dir" before the both-supplied check fires.
        (tmp_path / "logs" / "s").mkdir(parents=True)
        with pytest.raises(ValueError, match="not both"):
            EventSourcedSession.resume(
                log_dir=tmp_path / "logs",
                store=store,
                session_id="s",
                agent=agent,
            )
