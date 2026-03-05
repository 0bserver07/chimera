"""Tests for chimera.sessions.eventlog."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from chimera.events.base import Event
from chimera.sessions.eventlog.log import LOCK_TIMEOUT, EventLog, _FileLock
from chimera.sessions.eventlog.session import EventSourcedSession


# ======================================================================
# Helpers
# ======================================================================


def _make_event(event_type: str = "test", **meta: object) -> Event:
    return Event(type=event_type, metadata=dict(meta))


def _make_mock_agent() -> MagicMock:
    """Build a mock Agent with the minimum interface required by Session."""
    from chimera.types import AgentResult, Message

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


# ======================================================================
# EventLog — basic operations
# ======================================================================


class TestEventLogAppendAndRetrieve:
    def test_append_returns_sequential_indices(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        idx0 = log.append(_make_event("a"))
        idx1 = log.append(_make_event("b"))
        idx2 = log.append(_make_event("c"))
        assert (idx0, idx1, idx2) == (0, 1, 2)

    def test_get_by_index(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        log.append(_make_event("first"))
        log.append(_make_event("second"))
        assert log.get_by_index(0) is not None
        assert log.get_by_index(0).type == "first"  # type: ignore[union-attr]
        assert log.get_by_index(1) is not None
        assert log.get_by_index(1).type == "second"  # type: ignore[union-attr]
        assert log.get_by_index(99) is None

    def test_get_by_id(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        e = _make_event("tagged", event_id="cafebabe")
        log.append(e)
        assert log.get_by_id("cafebabe") is not None
        assert log.get_by_id("cafebabe").type == "tagged"  # type: ignore[union-attr]
        assert log.get_by_id("nonexistent") is None

    def test_get_by_id_generated(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        e = _make_event("auto")
        log.append(e)
        eid = e.metadata["event_id"]
        assert log.get_by_id(eid) is not None


# ======================================================================
# EventLog — range queries
# ======================================================================


class TestEventLogRanges:
    def test_get_range(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        for i in range(5):
            log.append(_make_event(f"e{i}"))
        result = log.get_range(1, 4)
        assert [e.type for e in result] == ["e1", "e2", "e3"]

    def test_get_range_defaults(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        for i in range(3):
            log.append(_make_event(f"e{i}"))
        result = log.get_range()
        assert len(result) == 3

    def test_get_since(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        for i in range(5):
            log.append(_make_event(f"e{i}"))
        result = log.get_since(3)
        assert [e.type for e in result] == ["e3", "e4"]


# ======================================================================
# EventLog — properties
# ======================================================================


class TestEventLogProperties:
    def test_length(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        assert log.length == 0
        log.append(_make_event("x"))
        assert log.length == 1
        log.append(_make_event("y"))
        assert log.length == 2

    def test_last_index_empty(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        assert log.last_index == -1

    def test_last_index(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "log")
        log.append(_make_event("a"))
        log.append(_make_event("b"))
        assert log.last_index == 1


# ======================================================================
# EventLog — persistence
# ======================================================================


class TestEventLogPersistence:
    def test_reload_from_disk(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "log"
        log1 = EventLog(log_dir)
        log1.append(_make_event("alpha"))
        log1.append(_make_event("beta"))

        # Create a new instance from the same directory.
        log2 = EventLog(log_dir)
        assert log2.length == 2
        assert log2.get_by_index(0) is not None
        assert log2.get_by_index(0).type == "alpha"  # type: ignore[union-attr]
        assert log2.get_by_index(1) is not None
        assert log2.get_by_index(1).type == "beta"  # type: ignore[union-attr]

    def test_append_after_reload(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "log"
        log1 = EventLog(log_dir)
        log1.append(_make_event("first"))

        log2 = EventLog(log_dir)
        idx = log2.append(_make_event("second"))
        assert idx == 1
        assert log2.length == 2


# ======================================================================
# EventLog — gap detection
# ======================================================================


class TestEventLogGapDetection:
    def test_gap_warning(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "log"
        log = EventLog(log_dir)
        log.append(_make_event("e0"))
        log.append(_make_event("e1"))
        log.append(_make_event("e2"))

        # Delete the file for index 1 to create a gap.
        for f in log_dir.iterdir():
            if f.name.startswith("event-000001"):
                f.unlink()
                break

        with pytest.warns(UserWarning, match="gaps at indices"):
            EventLog(log_dir)


# ======================================================================
# EventLog — serialization roundtrip
# ======================================================================


class TestEventLogSerialization:
    def test_serialize_deserialize(self, tmp_path: Path) -> None:
        event = Event(type="test_type", timestamp=1234567890.0, metadata={"key": "val"})
        data = EventLog._serialize(event, 42, "abcd1234")
        assert data["idx"] == 42
        assert data["event_id"] == "abcd1234"
        assert data["type"] == "test_type"
        assert data["timestamp"] == 1234567890.0
        assert data["metadata"]["key"] == "val"

        restored = EventLog._deserialize(data)
        assert restored.type == event.type
        assert restored.timestamp == event.timestamp
        assert restored.metadata["key"] == "val"


# ======================================================================
# EventLog — empty directory
# ======================================================================


class TestEventLogEmpty:
    def test_empty_directory(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "empty")
        assert log.length == 0
        assert log.last_index == -1
        assert log.get_range() == []
        assert log.get_since(0) == []


# ======================================================================
# _FileLock
# ======================================================================


class TestFileLock:
    def test_lock_acquire_release(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        with _FileLock(lock_path) as lock:
            assert lock is not None
            assert lock_path.exists()

    def test_lock_timeout(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        # Hold the lock in a separate fd.
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(TimeoutError, match="Could not acquire lock"):
                with _FileLock(lock_path, timeout=0.1):
                    pass  # pragma: no cover
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_reentrant_after_release(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        with _FileLock(lock_path):
            pass
        # Should be able to acquire again.
        with _FileLock(lock_path):
            pass


# ======================================================================
# EventSourcedSession — creation
# ======================================================================


class TestEventSourcedSessionCreation:
    def test_create_session(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="test-session",
        )
        assert session.session_id == "test-session"
        assert session.event_count == 0

    def test_create_with_auto_id(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession(agent=agent, log_dir=tmp_path / "logs")
        assert session.session_id  # non-empty


# ======================================================================
# EventSourcedSession — chat records events
# ======================================================================


class TestEventSourcedSessionChat:
    def test_chat_records_events(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent,
            log_dir=tmp_path / "logs",
            session_id="chat-test",
        )
        result = session.chat("hello world")
        assert result.output == "hello"
        # Should have 2 events: user_message + agent_result
        assert session.event_count == 2
        e0 = session.event_log.get_by_index(0)
        e1 = session.event_log.get_by_index(1)
        assert e0 is not None and e0.type == "user_message"
        assert e0.metadata["content"] == "hello world"
        assert e1 is not None and e1.type == "agent_result"
        assert e1.metadata["output"] == "hello"


# ======================================================================
# EventSourcedSession — resume
# ======================================================================


class TestEventSourcedSessionResume:
    def test_resume_replays_events(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        agent = _make_mock_agent()

        # Create and populate a session.
        s1 = EventSourcedSession(
            agent=agent, log_dir=log_dir, session_id="resume-test"
        )
        s1.chat("first message")
        s1.chat("second message")
        assert s1.event_count == 4  # 2 chats * 2 events each

        # Resume from disk.
        s2 = EventSourcedSession.resume(
            log_dir=log_dir,
            session_id="resume-test",
            agent=agent,
        )
        assert s2.event_count == 4
        # Context should have been rebuilt with user + assistant messages.
        msgs = s2.messages
        assert len(msgs) == 4  # 2 user + 2 assistant
        assert msgs[0].role == "user"
        assert msgs[0].content == "first message"
        assert msgs[1].role == "assistant"
        assert msgs[2].role == "user"
        assert msgs[2].content == "second message"
        assert msgs[3].role == "assistant"

    def test_resume_missing_session(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        with pytest.raises(ValueError, match="No event log found"):
            EventSourcedSession.resume(
                log_dir=tmp_path / "logs",
                session_id="nonexistent",
                agent=agent,
            )


# ======================================================================
# EventSourcedSession — resume_from (partial replay)
# ======================================================================


class TestEventSourcedSessionResumeFrom:
    def test_resume_from_partial(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        agent = _make_mock_agent()

        s1 = EventSourcedSession(
            agent=agent, log_dir=log_dir, session_id="partial-test"
        )
        s1.chat("msg1")
        s1.chat("msg2")
        s1.chat("msg3")
        assert s1.event_count == 6

        # Resume only up to index 3 (first 2 chats = indices 0,1,2,3).
        s2 = EventSourcedSession.resume_from(
            log_dir=log_dir,
            session_id="partial-test",
            agent=agent,
            up_to_index=3,
        )
        # All 6 events are on disk, but only 4 were replayed.
        assert s2.event_count == 6
        msgs = s2.messages
        assert len(msgs) == 4  # 2 user + 2 assistant from first 2 chats

    def test_resume_from_missing_session(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        with pytest.raises(ValueError, match="No event log found"):
            EventSourcedSession.resume_from(
                log_dir=tmp_path / "logs",
                session_id="nonexistent",
                agent=agent,
                up_to_index=0,
            )


# ======================================================================
# EventSourcedSession — event_count
# ======================================================================


class TestEventSourcedSessionEventCount:
    def test_event_count_starts_zero(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent, log_dir=tmp_path / "logs", session_id="count-test"
        )
        assert session.event_count == 0

    def test_event_count_increments(self, tmp_path: Path) -> None:
        agent = _make_mock_agent()
        session = EventSourcedSession(
            agent=agent, log_dir=tmp_path / "logs", session_id="count-test"
        )
        session.chat("a")
        assert session.event_count == 2
        session.chat("b")
        assert session.event_count == 4
