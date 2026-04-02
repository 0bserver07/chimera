"""Benchmark trace capture — records everything the agent does during a run.

Captures every event from AgentLoop into a structured trace that can be
analyzed to understand WHY the agent failed, not just THAT it failed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType


@dataclass
class ToolTrace:
    """Record of a single tool call."""
    turn: int
    tool_name: str
    arguments: dict[str, Any]
    output: str
    success: bool
    timestamp: float


@dataclass
class TurnTrace:
    """Record of a single agent turn (model response + tool calls)."""
    turn_number: int
    assistant_content: str
    tool_traces: list[ToolTrace] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class RunTrace:
    """Full trace of an agent run — everything needed to diagnose failure."""
    instance_id: str
    model: str
    preset: str
    task: str
    turns: list[TurnTrace] = field(default_factory=list)
    final_reason: str = ""
    resolved: bool = False
    elapsed_s: float = 0.0
    patch: str = ""
    error: str = ""
    start_time: float = field(default_factory=time.time)

    # Computed diagnostics
    @property
    def total_tool_calls(self) -> int:
        return sum(len(t.tool_traces) for t in self.turns)

    @property
    def tools_used(self) -> dict[str, int]:
        """Count of each tool used."""
        counts: dict[str, int] = {}
        for turn in self.turns:
            for tc in turn.tool_traces:
                counts[tc.tool_name] = counts.get(tc.tool_name, 0) + 1
        return counts

    @property
    def files_read(self) -> list[str]:
        """Files the agent read."""
        files = []
        for turn in self.turns:
            for tc in turn.tool_traces:
                if tc.tool_name in ("read_file", "cached_read"):
                    path = tc.arguments.get("file_path") or tc.arguments.get("path", "")
                    if path and path not in files:
                        files.append(path)
        return files

    @property
    def files_edited(self) -> list[str]:
        """Files the agent tried to edit."""
        files = []
        for turn in self.turns:
            for tc in turn.tool_traces:
                if tc.tool_name in ("edit_file", "write_file", "replace_in_file", "apply_patch"):
                    path = tc.arguments.get("file_path") or tc.arguments.get("path") or tc.arguments.get("file", "")
                    if path and path not in files:
                        files.append(path)
        return files

    @property
    def edit_attempts(self) -> int:
        """Number of edit/write tool calls."""
        return sum(1 for t in self.turns for tc in t.tool_traces
                   if tc.tool_name in ("edit_file", "write_file", "replace_in_file", "apply_patch"))

    @property
    def failed_edits(self) -> list[ToolTrace]:
        """Edit calls that returned errors."""
        return [tc for t in self.turns for tc in t.tool_traces
                if tc.tool_name in ("edit_file", "write_file", "replace_in_file")
                and not tc.success]

    @property
    def diagnosis(self) -> str:
        """Human-readable diagnosis of why the run failed."""
        if self.resolved:
            return "RESOLVED"

        if self.edit_attempts == 0:
            if self.total_tool_calls == 0:
                return "NO_TOOL_CALLS: Agent never called any tools"
            tools = self.tools_used
            if all(t in ("bash", "read_file", "search", "list_files", "think", "git", "cached_read", "grep", "glob")
                   for t in tools):
                return f"EXPLORE_ONLY: Agent explored ({self.total_tool_calls} calls) but never attempted any edits. Read {len(self.files_read)} files."
            return f"NO_EDITS: Agent used tools ({list(tools.keys())}) but never called edit/write"

        if self.failed_edits:
            failures = [(fe.tool_name, fe.output[:100]) for fe in self.failed_edits]
            return f"EDIT_FAILURES: {len(self.failed_edits)} edit(s) failed: {failures}"

        if self.final_reason == "max_turns":
            return f"MAX_TURNS: Agent made {self.edit_attempts} edit(s) but hit turn limit ({len(self.turns)} turns)"

        if self.patch and not self.resolved:
            return f"WRONG_PATCH: Agent produced a patch ({len(self.patch.splitlines())} lines) but tests didn't pass"

        return f"UNKNOWN: reason={self.final_reason}, tools={self.total_tool_calls}, edits={self.edit_attempts}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "model": self.model,
            "preset": self.preset,
            "resolved": self.resolved,
            "diagnosis": self.diagnosis,
            "final_reason": self.final_reason,
            "elapsed_s": self.elapsed_s,
            "total_turns": len(self.turns),
            "total_tool_calls": self.total_tool_calls,
            "tools_used": self.tools_used,
            "files_read": self.files_read,
            "files_edited": self.files_edited,
            "edit_attempts": self.edit_attempts,
            "failed_edits": len(self.failed_edits),
            "patch_lines": len(self.patch.splitlines()) if self.patch else 0,
            "error": self.error,
            "turns": [
                {
                    "turn": t.turn_number,
                    "assistant": t.assistant_content[:500],
                    "tools": [
                        {
                            "name": tc.tool_name,
                            "args": {k: str(v)[:200] for k, v in tc.arguments.items()},
                            "output": tc.output[:300],
                            "success": tc.success,
                        }
                        for tc in t.tool_traces
                    ],
                }
                for t in self.turns
            ],
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))


class TraceCollector:
    """Collect LoopEvents into a RunTrace."""

    def __init__(self, instance_id: str, model: str, preset: str, task: str):
        self.trace = RunTrace(
            instance_id=instance_id, model=model, preset=preset, task=task,
        )
        self._current_turn = TurnTrace(turn_number=0, assistant_content="")
        self._last_turn_num = -1

    def record(self, event: LoopEvent) -> None:
        """Record a LoopEvent into the trace."""
        # New turn?
        if event.turn != self._last_turn_num:
            if self._last_turn_num >= 0:
                self.trace.turns.append(self._current_turn)
            self._current_turn = TurnTrace(turn_number=event.turn, assistant_content="")
            self._last_turn_num = event.turn

        if event.type == LoopEventType.assistant:
            content = getattr(event.data, "content", str(event.data))
            self._current_turn.assistant_content += content

        elif event.type == LoopEventType.tool_result:
            tc, result = event.data if isinstance(event.data, tuple) else (None, event.data)
            tool_name = getattr(tc, "name", "unknown") if tc else "unknown"
            args = getattr(tc, "arguments", {}) if tc else {}
            output = getattr(result, "output", str(result))
            success = getattr(result, "success", True)
            self._current_turn.tool_traces.append(ToolTrace(
                turn=event.turn, tool_name=tool_name,
                arguments=args, output=output[:2000],
                success=success, timestamp=time.time(),
            ))

        elif event.type == LoopEventType.result:
            self.trace.final_reason = getattr(event.data, "reason", "")
            # Flush last turn
            if self._current_turn.assistant_content or self._current_turn.tool_traces:
                self.trace.turns.append(self._current_turn)

    def finalize(self, resolved: bool, patch: str = "", error: str = "") -> RunTrace:
        self.trace.resolved = resolved
        self.trace.patch = patch
        self.trace.error = error
        self.trace.elapsed_s = round(time.time() - self.trace.start_time, 1)
        return self.trace
