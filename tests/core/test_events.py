# tests/test_events.py
from __future__ import annotations

import logging
from typing import Any

import pytest

from chimera.events.base import Event, EventBus
from chimera.events.middleware import FilterMiddleware, LoggingMiddleware, Middleware
from chimera.events.types import (
    CompactionEvent,
    ErrorEvent,
    LoopDetectedEvent,
    PermissionEvent,
    SessionEvent,
    StepEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Recorder:
    """Simple helper that records every event it receives."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# EventBus: subscribe / publish
# ---------------------------------------------------------------------------

class TestEventBusSubscribePublish:
    def test_basic_subscribe_and_publish(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("step", rec)

        evt = Event(type="step")
        bus.publish(evt)

        assert len(rec.events) == 1
        assert rec.events[0] is evt

    def test_multiple_handlers_for_same_type(self) -> None:
        bus = EventBus()
        rec1 = _Recorder()
        rec2 = _Recorder()
        bus.subscribe("step", rec1)
        bus.subscribe("step", rec2)

        bus.publish(Event(type="step"))

        assert len(rec1.events) == 1
        assert len(rec2.events) == 1

    def test_handler_only_receives_matching_type(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("step", rec)

        bus.publish(Event(type="error"))

        assert len(rec.events) == 0


# ---------------------------------------------------------------------------
# EventBus: unsubscribe
# ---------------------------------------------------------------------------

class TestEventBusUnsubscribe:
    def test_unsubscribe_callable(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        unsub = bus.subscribe("step", rec)

        bus.publish(Event(type="step"))
        assert len(rec.events) == 1

        unsub()
        bus.publish(Event(type="step"))
        assert len(rec.events) == 1  # no new event

    def test_double_unsubscribe_is_safe(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        unsub = bus.subscribe("step", rec)

        unsub()
        unsub()  # should not raise


# ---------------------------------------------------------------------------
# EventBus: wildcard handler
# ---------------------------------------------------------------------------

class TestEventBusWildcard:
    def test_wildcard_receives_all_events(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("*", rec)

        bus.publish(Event(type="step"))
        bus.publish(Event(type="error"))
        bus.publish(Event(type="custom"))

        assert len(rec.events) == 3

    def test_wildcard_and_specific_both_fire(self) -> None:
        bus = EventBus()
        specific = _Recorder()
        wild = _Recorder()
        bus.subscribe("step", specific)
        bus.subscribe("*", wild)

        bus.publish(Event(type="step"))

        assert len(specific.events) == 1
        assert len(wild.events) == 1


# ---------------------------------------------------------------------------
# EventBus: decorator
# ---------------------------------------------------------------------------

class TestEventBusDecorator:
    def test_on_decorator_registers_handler(self) -> None:
        bus = EventBus()
        received: list[Event] = []

        @bus.on("step")
        def handle_step(event: Event) -> None:
            received.append(event)

        bus.publish(Event(type="step"))
        assert len(received) == 1

    def test_on_decorator_returns_original_function(self) -> None:
        bus = EventBus()

        @bus.on("step")
        def handle_step(event: Event) -> None:
            pass

        assert callable(handle_step)


# ---------------------------------------------------------------------------
# EventBus: middleware
# ---------------------------------------------------------------------------

class TestEventBusMiddleware:
    def test_middleware_chain_processes_event(self) -> None:
        bus = EventBus()
        order: list[str] = []

        class M1(Middleware):
            def process(self, event: Event, next_handler: Any) -> None:
                order.append("m1_before")
                next_handler(event)
                order.append("m1_after")

        class M2(Middleware):
            def process(self, event: Event, next_handler: Any) -> None:
                order.append("m2_before")
                next_handler(event)
                order.append("m2_after")

        rec = _Recorder()
        bus.subscribe("step", rec)
        bus.use(M1())
        bus.use(M2())

        bus.publish(Event(type="step"))

        # M1 wraps M2 wraps dispatch (nested like function calls)
        assert order == ["m1_before", "m2_before", "m2_after", "m1_after"]
        assert len(rec.events) == 1

    def test_middleware_can_suppress_event(self) -> None:
        bus = EventBus()

        class Suppressor(Middleware):
            def process(self, event: Event, next_handler: Any) -> None:
                pass  # do not call next_handler

        rec = _Recorder()
        bus.subscribe("step", rec)
        bus.use(Suppressor())

        bus.publish(Event(type="step"))
        assert len(rec.events) == 0


# ---------------------------------------------------------------------------
# EventBus: clear
# ---------------------------------------------------------------------------

class TestEventBusClear:
    def test_clear_removes_handlers_and_middleware(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("step", rec)
        bus.use(LoggingMiddleware())

        bus.clear()

        bus.publish(Event(type="step"))
        assert len(rec.events) == 0


# ---------------------------------------------------------------------------
# Event types: instantiation
# ---------------------------------------------------------------------------

class TestEventTypes:
    def test_tool_call_event(self) -> None:
        evt = ToolCallEvent(tool_name="bash", arguments={"cmd": "ls"}, call_id="c1")
        assert evt.type == "tool_call"
        assert evt.tool_name == "bash"
        assert evt.arguments == {"cmd": "ls"}
        assert evt.call_id == "c1"
        assert isinstance(evt.timestamp, float)

    def test_tool_result_event(self) -> None:
        evt = ToolResultEvent(call_id="c1", output="ok", success=True)
        assert evt.type == "tool_result"
        assert evt.call_id == "c1"
        assert evt.output == "ok"
        assert evt.success is True

    def test_step_event(self) -> None:
        evt = StepEvent(step_number=3, content="thinking")
        assert evt.type == "step"
        assert evt.step_number == 3
        assert evt.content == "thinking"

    def test_text_delta_event(self) -> None:
        evt = TextDeltaEvent(content="hello")
        assert evt.type == "text_delta"
        assert evt.content == "hello"

    def test_error_event(self) -> None:
        evt = ErrorEvent(error="boom", recoverable=False)
        assert evt.type == "error"
        assert evt.error == "boom"
        assert evt.recoverable is False

    def test_loop_detected_event(self) -> None:
        evt = LoopDetectedEvent(pattern="A-B-A-B")
        assert evt.type == "loop_detected"
        assert evt.pattern == "A-B-A-B"

    def test_compaction_event(self) -> None:
        evt = CompactionEvent(messages_before=100, messages_after=20)
        assert evt.type == "compaction"
        assert evt.messages_before == 100
        assert evt.messages_after == 20

    def test_permission_event(self) -> None:
        evt = PermissionEvent(tool_name="bash", action="execute", granted=True)
        assert evt.type == "permission"
        assert evt.tool_name == "bash"
        assert evt.action == "execute"
        assert evt.granted is True

    def test_session_event(self) -> None:
        evt = SessionEvent(action="start", session_id="s1")
        assert evt.type == "session"
        assert evt.action == "start"
        assert evt.session_id == "s1"

    def test_event_types_inherit_from_event(self) -> None:
        classes = [
            ToolCallEvent, ToolResultEvent, StepEvent, TextDeltaEvent,
            ErrorEvent, LoopDetectedEvent, CompactionEvent, PermissionEvent,
            SessionEvent,
        ]
        for cls in classes:
            assert issubclass(cls, Event)

    def test_metadata_default(self) -> None:
        evt = ToolCallEvent(tool_name="x", arguments={}, call_id="c")
        assert evt.metadata == {}

    def test_metadata_provided(self) -> None:
        evt = ErrorEvent(error="x", metadata={"key": "val"})
        assert evt.metadata == {"key": "val"}


# ---------------------------------------------------------------------------
# Event types: publish through EventBus
# ---------------------------------------------------------------------------

class TestEventTypesOnBus:
    def test_typed_events_publish_with_correct_type(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("tool_call", rec)

        bus.publish(ToolCallEvent(tool_name="bash", arguments={}, call_id="c1"))

        assert len(rec.events) == 1
        assert isinstance(rec.events[0], ToolCallEvent)


# ---------------------------------------------------------------------------
# LoggingMiddleware
# ---------------------------------------------------------------------------

class TestLoggingMiddleware:
    def test_logs_event(self, caplog: pytest.LogCaptureFixture) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("step", rec)
        bus.use(LoggingMiddleware(log=logging.getLogger("chimera.events.test")))

        with caplog.at_level(logging.DEBUG, logger="chimera.events.test"):
            bus.publish(Event(type="step"))

        assert len(rec.events) == 1
        assert any("step" in r.message for r in caplog.records)

    def test_logging_middleware_forwards_event(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("error", rec)
        bus.use(LoggingMiddleware())

        bus.publish(Event(type="error"))

        assert len(rec.events) == 1


# ---------------------------------------------------------------------------
# FilterMiddleware
# ---------------------------------------------------------------------------

class TestFilterMiddleware:
    def test_allows_matching_types(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("step", rec)
        bus.use(FilterMiddleware(allow_types={"step", "error"}))

        bus.publish(Event(type="step"))

        assert len(rec.events) == 1

    def test_blocks_non_matching_types(self) -> None:
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("tool_call", rec)
        bus.use(FilterMiddleware(allow_types={"step"}))

        bus.publish(Event(type="tool_call"))

        assert len(rec.events) == 0

    def test_filter_combined_with_logging(self) -> None:
        """FilterMiddleware + LoggingMiddleware chained together."""
        bus = EventBus()
        rec = _Recorder()
        bus.subscribe("step", rec)
        bus.subscribe("error", rec)

        bus.use(FilterMiddleware(allow_types={"step"}))
        bus.use(LoggingMiddleware())

        bus.publish(Event(type="step"))
        bus.publish(Event(type="error"))

        # Only "step" passes the filter
        assert len(rec.events) == 1
        assert rec.events[0].type == "step"
