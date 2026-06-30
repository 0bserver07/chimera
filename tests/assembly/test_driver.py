"""Unit tests for AgentDriver (the TUI/REPL control surface) and render_event."""
import pytest

from chimera.assembly.driver import AgentDriver, render_event
from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.providers.base import Response
from chimera.types import ToolCall


class MockProvider:
    """Minimal provider: completes a turn with no tool calls."""

    model_name = "mock-model"
    context_window = 222_000

    async def async_complete(self, messages, tools=None):
        return Response(
            content="hello back",
            tool_calls=[],
            usage={"input_tokens": 5, "output_tokens": 3},
        )


def _driver(tmp_path):
    return AgentDriver(
        model="mock", preset="minimal",
        project_dir=str(tmp_path), provider=MockProvider(),
    )


@pytest.mark.asyncio
async def test_send_streams_to_result_and_counts_turns(tmp_path):
    d = _driver(tmp_path)
    events = [ev async for ev in d.send("hello")]
    assert any(e.type == LoopEventType.result for e in events)
    assert d.turn_count == 1
    assert isinstance(d.total_cost, float)


@pytest.mark.asyncio
async def test_history_persists_across_turns(tmp_path):
    d = _driver(tmp_path)
    [ev async for ev in d.send("first")]
    h1 = len(d.history)
    assert h1 > 0
    [ev async for ev in d.send("second")]
    assert len(d.history) > h1


@pytest.mark.asyncio
async def test_clear_resets_history(tmp_path):
    d = _driver(tmp_path)
    [ev async for ev in d.send("hello")]
    assert d.history
    d.clear()
    assert d.history == []


def test_steer_and_cancel(tmp_path):
    d = _driver(tmp_path)
    d.steer("go left")
    assert d.agent._message_queue.has_steering()
    d.cancel()
    assert d.agent._abort_signal.aborted


def test_state_properties(tmp_path):
    d = _driver(tmp_path)
    assert d.model == "mock-model"
    assert d.context_window == 222_000
    assert isinstance(d.tools, list) and len(d.tools) >= 1


def test_render_event_formatting():
    tc = ToolCall(id="1", name="edit_file", arguments={"path": "a.py", "content": "x"})
    assert render_event(LoopEvent(LoopEventType.tool_use, tc, 0)).startswith("\n  ⚙ edit_file(")
    assert render_event(LoopEvent(LoopEventType.assistant_chunk, "tok", 0)) == "tok"
    assert render_event(LoopEvent(LoopEventType.error, "boom", 0)) == "\n  [error] boom"
    # The full assistant message is skipped so it never double-prints with chunks.
    assert render_event(LoopEvent(LoopEventType.assistant, object(), 0)) is None
