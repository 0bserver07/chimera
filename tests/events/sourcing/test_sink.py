"""Tests for EventSourcingSink: EventBus -> SqliteEventStore translation."""
from __future__ import annotations

from pathlib import Path

from chimera.events.base import EventBus
from chimera.events.sourcing import (
    EventSourcingSink,
    SqliteEventStore,
)
from chimera.events.types import (
    CompactionEvent,
    ErrorEvent,
    ModelRequestEvent,
    ModelResponseEvent,
    PermissionEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def test_sink_records_tool_lifecycle(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    sink = EventSourcingSink(store, aggregate_id="s1", bus=bus)

    bus.publish(ToolCallEvent(tool_name="bash", arguments={"cmd": "ls"}, call_id="c1"))
    bus.publish(ToolResultEvent(call_id="c1", output="a\nb\n", success=True))

    events = [e for e in store.read_since("s1")]
    names = [e.name for e in events]
    assert names == ["tool.called", "tool.completed"]
    assert events[0].payload.tool_name == "bash"  # type: ignore[attr-defined]
    assert events[1].payload.success is True  # type: ignore[attr-defined]
    assert sink._aggregate_id == "s1"  # noqa: SLF001 — sanity


def test_sink_synthesizes_file_mutated(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    EventSourcingSink(store, aggregate_id="s1", bus=bus)

    target = tmp_path / "new_file.txt"  # does not exist yet
    bus.publish(ToolCallEvent(
        tool_name="write_file", arguments={"path": str(target)}, call_id="c1",
    ))
    bus.publish(ToolResultEvent(call_id="c1", output="ok", success=True))

    events = [e for e in store.read_since("s1")]
    names = [e.name for e in events]
    assert "file.mutated" in names
    fm = next(e for e in events if e.name == "file.mutated")
    assert fm.payload.path == str(target)  # type: ignore[attr-defined]
    assert fm.payload.operation == "created"  # type: ignore[attr-defined]


def test_sink_records_permission_decided(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    EventSourcingSink(store, aggregate_id="s1", bus=bus)

    bus.publish(PermissionEvent(
        tool_name="bash", action="allow", granted=True, call_id="c1",
    ))
    events = [e for e in store.read_since("s1")]
    assert events[0].name == "permission.decided"
    p = events[0].payload
    assert p.tool_name == "bash"  # type: ignore[attr-defined]
    assert p.granted is True  # type: ignore[attr-defined]


def test_sink_records_model_round_trip(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    EventSourcingSink(store, aggregate_id="s1", bus=bus)
    bus.publish(ModelRequestEvent(model="glm-5", message_count=3, tool_count=2))
    bus.publish(ModelResponseEvent(
        model="glm-5", content_length=42, tool_calls_count=1,
        input_tokens=100, output_tokens=20,
    ))
    events = [e for e in store.read_since("s1")]
    assert [e.name for e in events] == ["model.requested", "model.responded"]


def test_sink_records_compaction_and_error(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    EventSourcingSink(store, aggregate_id="s1", bus=bus)
    bus.publish(CompactionEvent(messages_before=10, messages_after=5))
    bus.publish(ErrorEvent(error="boom", recoverable=True))
    events = [e for e in store.read_since("s1")]
    names = [e.name for e in events]
    assert "compaction.performed" in names
    assert "error.occurred" in names


def test_sink_manual_session_lifecycle(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    sink = EventSourcingSink(store, aggregate_id="s1", bus=bus)
    sink.record_session_created(agent_name="planner", model="glm-5")
    sink.record_user_message("hello")
    sink.record_session_ended(success=True, total_steps=4, total_cost=0.01)
    events = [e for e in store.read_since("s1")]
    names = [e.name for e in events]
    assert names == ["session.created", "user.message", "session.ended"]


def test_sink_detach_stops_recording(tmp_path: Path) -> None:
    bus = EventBus()
    store = SqliteEventStore(tmp_path / "evt.db")
    sink = EventSourcingSink(store, aggregate_id="s1", bus=bus)
    sink.detach()
    bus.publish(ToolCallEvent(tool_name="x", arguments={}, call_id="c1"))
    assert store.last_seq("s1") == 0
