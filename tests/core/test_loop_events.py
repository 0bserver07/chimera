from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult


def test_loop_event_types_exist():
    assert LoopEventType.stream_start
    assert LoopEventType.assistant
    assert LoopEventType.assistant_chunk
    assert LoopEventType.tool_use
    assert LoopEventType.tool_progress
    assert LoopEventType.tool_result
    assert LoopEventType.system
    assert LoopEventType.compact_boundary
    assert LoopEventType.error
    assert LoopEventType.result


def test_loop_event_creation():
    event = LoopEvent(type=LoopEventType.assistant, data={"text": "hello"}, turn=1)
    assert event.type == LoopEventType.assistant
    assert event.data == {"text": "hello"}
    assert event.turn == 1
    assert event.timestamp > 0


def test_loop_result():
    result = LoopResult(
        reason="max_turns",
        messages=[],
        usage={},
        cost_usd=0.0,
        duration_ms=100.0,
        turn_count=3,
    )
    assert result.reason == "max_turns"
