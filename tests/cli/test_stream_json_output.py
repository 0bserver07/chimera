# tests/test_stream_json_output.py
"""Tests for StreamJsonHandler / JsonHandler in chimera.cli.output_format."""
from __future__ import annotations

import io
import json

from chimera.cli.output_format import (
    JsonHandler,
    StreamJsonHandler,
    select_handler,
)
from chimera.events.types import (
    AgentEndEvent,
    AgentStartEvent,
    StepEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def _make_events() -> list:
    return [
        AgentStartEvent(max_steps=3),
        StepEvent(step_number=1, content="thinking"),
        ToolCallEvent(tool_name="bash", arguments={"cmd": "ls"}, call_id="c1"),
        ToolResultEvent(call_id="c1", output="ok", success=True),
        AgentEndEvent(steps=1, success=True, total_cost=0.42),
    ]


def test_stream_json_one_event_per_line() -> None:
    """Each LoopEvent becomes exactly one parseable JSON line."""
    buf = io.StringIO()
    handler = StreamJsonHandler(out=buf)
    events = _make_events()
    for ev in events:
        handler.handle_loop_event(ev)

    raw = buf.getvalue()
    lines = [ln for ln in raw.split("\n") if ln]
    assert len(lines) == len(events), f"expected {len(events)} lines, got {len(lines)}"

    parsed_types = []
    for ln in lines:
        obj = json.loads(ln)  # raises on malformed JSON
        assert "type" in obj and "ts" in obj
        assert isinstance(obj["ts"], (int, float))
        parsed_types.append(obj["type"])

    assert parsed_types == [ev.type for ev in events]


def test_stream_json_redacts_secrets() -> None:
    """A pattern-detected API key is redacted before serialization."""
    buf = io.StringIO()
    handler = StreamJsonHandler(out=buf)

    fake_key = "sk-" + "A" * 40
    leaked = f"here is the key {fake_key} done"
    handler.handle_loop_event(ToolResultEvent(call_id="c1", output=leaked))

    line = buf.getvalue().strip()
    obj = json.loads(line)
    assert fake_key not in line, "raw API key leaked into JSON output"
    assert fake_key not in obj["output"]
    assert "[REDACTED]" in obj["output"]


def test_json_aggregated_format() -> None:
    """JsonHandler buffers everything and emits one document on finalize."""
    buf = io.StringIO()
    handler = JsonHandler(out=buf)
    events = _make_events()
    for ev in events:
        handler.handle_loop_event(ev)
    handler.finalize()

    raw = buf.getvalue().strip()
    # Exactly one JSON document, no per-line splitting.
    doc = json.loads(raw)
    assert isinstance(doc, dict)
    assert set(doc.keys()) >= {"events", "result", "cost"}
    assert isinstance(doc["events"], list)
    assert len(doc["events"]) == len(events)
    assert [e["type"] for e in doc["events"]] == [ev.type for ev in events]
    assert doc["result"]["success"] is True
    assert doc["cost"]["total_cost"] == 0.42


def test_finalize_is_idempotent() -> None:
    """Calling finalize twice does not duplicate the document."""
    buf = io.StringIO()
    handler = JsonHandler(out=buf)
    handler.handle_loop_event(AgentEndEvent(success=True))
    handler.finalize()
    handler.finalize()
    assert buf.getvalue().count("\n") == 1


def test_select_handler_dispatches_known_formats() -> None:
    """Factory returns correct handler subclasses, raises on unknown."""
    assert isinstance(select_handler("stream-json"), StreamJsonHandler)
    assert isinstance(select_handler("json"), JsonHandler)
    # text format: just must not raise and must not be a JSON handler.
    text_h = select_handler("text")
    assert not isinstance(text_h, (StreamJsonHandler, JsonHandler))
    try:
        select_handler("xml")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown format")
