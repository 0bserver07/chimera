"""Tests for extended event types."""
from chimera.events.types import (
    ModelRequestEvent, ModelResponseEvent,
    TurnStartEvent, TurnEndEvent,
    StreamStartEvent, StreamEndEvent,
    AgentStartEvent, AgentEndEvent,
    SteeringEvent, CancellationEvent,
)
from chimera.events.base import EventBus


def test_model_request_event():
    e = ModelRequestEvent(model="glm-5", message_count=3, tool_count=5)
    assert e.type == "model_request"
    assert e.model == "glm-5"


def test_model_response_event():
    e = ModelResponseEvent(model="glm-5", content_length=100, tool_calls_count=2,
                           input_tokens=50, output_tokens=30)
    assert e.type == "model_response"
    assert e.input_tokens == 50


def test_turn_events():
    s = TurnStartEvent(turn_number=1)
    e = TurnEndEvent(turn_number=1, tool_calls_count=3)
    assert s.type == "turn_start"
    assert e.type == "turn_end"


def test_stream_events():
    s = StreamStartEvent(model="test")
    e = StreamEndEvent(total_tokens=500)
    assert s.type == "stream_start"
    assert e.total_tokens == 500


def test_agent_events():
    s = AgentStartEvent(max_steps=50)
    e = AgentEndEvent(steps=10, success=True, total_cost=0.05)
    assert s.max_steps == 50
    assert e.total_cost == 0.05


def test_steering_event():
    e = SteeringEvent(content="change direction")
    assert e.type == "steering"
    assert e.content == "change direction"


def test_cancellation_event():
    e = CancellationEvent(at_step=5)
    assert e.type == "cancellation"
    assert e.at_step == 5


def test_event_bus_receives_new_types():
    bus = EventBus()
    received = []
    bus.subscribe("model_request", lambda e: received.append(e))
    bus.publish(ModelRequestEvent(model="test", message_count=1))
    assert len(received) == 1
    assert received[0].model == "test"


def test_all_new_events_serializable():
    """All new events should have proper dataclass fields."""
    import dataclasses
    events = [
        ModelRequestEvent(), ModelResponseEvent(),
        TurnStartEvent(), TurnEndEvent(),
        StreamStartEvent(), StreamEndEvent(),
        AgentStartEvent(), AgentEndEvent(),
        SteeringEvent(), CancellationEvent(),
    ]
    for e in events:
        d = dataclasses.asdict(e)
        assert "type" in d
