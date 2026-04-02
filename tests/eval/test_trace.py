"""Tests for benchmark trace capture and diagnosis."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult
from chimera.eval.trace import RunTrace, TraceCollector, ToolTrace, TurnTrace
from chimera.types import Message, ToolCall, ToolResult
from chimera.providers.base import Response


class TestRunTraceDiagnosis:
    def test_no_tool_calls(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        assert "NO_TOOL_CALLS" in trace.diagnosis

    def test_explore_only(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        turn = TurnTrace(turn_number=1, assistant_content="looking...")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="read_file", arguments={"path": "foo.py"}, output="content", success=True, timestamp=0))
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="bash", arguments={"command": "ls"}, output="files", success=True, timestamp=0))
        trace.turns.append(turn)
        assert "EXPLORE_ONLY" in trace.diagnosis

    def test_edit_failures(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        turn = TurnTrace(turn_number=1, assistant_content="editing...")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="edit_file", arguments={"path": "foo.py"}, output="search text not found", success=False, timestamp=0))
        trace.turns.append(turn)
        assert "EDIT_FAILURES" in trace.diagnosis

    def test_wrong_patch(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        turn = TurnTrace(turn_number=1, assistant_content="fixed it")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="edit_file", arguments={"path": "foo.py"}, output="ok", success=True, timestamp=0))
        trace.turns.append(turn)
        trace.patch = "--- a/foo.py\n+++ b/foo.py\n-old\n+new"
        trace.final_reason = "completed"
        assert "WRONG_PATCH" in trace.diagnosis

    def test_resolved(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        trace.resolved = True
        assert trace.diagnosis == "RESOLVED"

    def test_max_turns_with_edits(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        trace.final_reason = "max_turns"
        turn = TurnTrace(turn_number=1, assistant_content="")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="write_file", arguments={}, output="ok", success=True, timestamp=0))
        trace.turns.append(turn)
        assert "MAX_TURNS" in trace.diagnosis


class TestRunTraceProperties:
    def test_tools_used(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        turn = TurnTrace(turn_number=1, assistant_content="")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="bash", arguments={}, output="", success=True, timestamp=0))
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="bash", arguments={}, output="", success=True, timestamp=0))
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="read_file", arguments={}, output="", success=True, timestamp=0))
        trace.turns.append(turn)
        assert trace.tools_used == {"bash": 2, "read_file": 1}

    def test_files_read(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        turn = TurnTrace(turn_number=1, assistant_content="")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="read_file", arguments={"file_path": "a.py"}, output="", success=True, timestamp=0))
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="read_file", arguments={"file_path": "b.py"}, output="", success=True, timestamp=0))
        trace.turns.append(turn)
        assert trace.files_read == ["a.py", "b.py"]

    def test_files_edited(self):
        trace = RunTrace(instance_id="test", model="m", preset="p", task="t")
        turn = TurnTrace(turn_number=1, assistant_content="")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="edit_file", arguments={"file_path": "a.py"}, output="", success=True, timestamp=0))
        trace.turns.append(turn)
        assert trace.files_edited == ["a.py"]


class TestTraceCollector:
    def test_collects_assistant_content(self):
        collector = TraceCollector("test", "model", "preset", "task")
        event = LoopEvent(type=LoopEventType.assistant, data=Message.assistant("hello"), turn=0)
        collector.record(event)
        collector.record(LoopEvent(type=LoopEventType.result, data=LoopResult(reason="completed", messages=[], usage={}, cost_usd=0, duration_ms=0, turn_count=1), turn=0))
        trace = collector.finalize(resolved=False)
        assert trace.turns[0].assistant_content == "hello"

    def test_collects_tool_results(self):
        collector = TraceCollector("test", "model", "preset", "task")
        tc = ToolCall(id="t1", name="bash", arguments={"command": "ls"})
        result = ToolResult(output="file1\nfile2")
        event = LoopEvent(type=LoopEventType.tool_result, data=(tc, result), turn=0)
        collector.record(event)
        collector.record(LoopEvent(type=LoopEventType.result, data=LoopResult(reason="completed", messages=[], usage={}, cost_usd=0, duration_ms=0, turn_count=1), turn=0))
        trace = collector.finalize(resolved=False)
        assert len(trace.turns[0].tool_traces) == 1
        assert trace.turns[0].tool_traces[0].tool_name == "bash"

    def test_tracks_turns(self):
        collector = TraceCollector("test", "model", "preset", "task")
        collector.record(LoopEvent(type=LoopEventType.assistant, data=Message.assistant("turn 0"), turn=0))
        collector.record(LoopEvent(type=LoopEventType.assistant, data=Message.assistant("turn 1"), turn=1))
        collector.record(LoopEvent(type=LoopEventType.result, data=LoopResult(reason="completed", messages=[], usage={}, cost_usd=0, duration_ms=0, turn_count=2), turn=1))
        trace = collector.finalize(resolved=False)
        assert len(trace.turns) == 2


class TestTraceSave:
    def test_save_and_load(self):
        trace = RunTrace(instance_id="test-123", model="gpt-4o", preset="swebench", task="fix bug")
        turn = TurnTrace(turn_number=1, assistant_content="I'll fix it")
        turn.tool_traces.append(ToolTrace(turn=1, tool_name="bash", arguments={"command": "ls"}, output="files", success=True, timestamp=0))
        trace.turns.append(turn)
        trace.final_reason = "completed"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trace.json"
            trace.save(path)
            assert path.exists()

            import json
            data = json.loads(path.read_text())
            assert data["instance_id"] == "test-123"
            assert data["diagnosis"] is not None
            assert len(data["turns"]) == 1
            assert data["turns"][0]["tools"][0]["name"] == "bash"
