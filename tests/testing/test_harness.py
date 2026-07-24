"""chimera.testing harness: scripted turns through the REAL AgentLoop.

Covers the harness contract end to end — completion, real tool execution in a
temp workspace, file bookkeeping, multi-turn history, scripted usage/cost,
streamed thinking chunks, mid-stream steering, mid-turn cancellation, and
provider error injection. Nothing here mocks the loop; only the model is
scripted (FauxProvider).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.providers.cost import calculate_cost
from chimera.providers.faux import FauxProviderError
from chimera.testing import (
    AgentHarness,
    create_assembled_harness,
    create_harness,
)

# ---------------------------------------------------------------------------
# Basics: completion, event order, terminal reason
# ---------------------------------------------------------------------------


def test_completion_and_event_order(tmp_path: Path) -> None:
    run = create_harness(turns=[{"text": "all done"}], workspace=tmp_path).run("go")

    assert run.reason == "completed"
    assert run.error is None
    assert run.output_text == "all done"
    # Shape: the stream starts, the assistant answers, the loop terminates.
    types = run.event_types
    assert types.index(LoopEventType.stream_start) < types.index(LoopEventType.assistant)
    assert types[-1] == LoopEventType.result
    assert run.turn_count == 1


def test_max_turns_terminal_reason(tmp_path: Path) -> None:
    # A provider that never stops calling tools hits the loop's turn ceiling.
    h = create_harness(
        turns=[{"text": "again", "tool_calls": [{"name": "list_files", "arguments": {}}]}],
        workspace=tmp_path,
        max_turns=2,
    )
    h.provider._on_exhausted = "repeat"  # keep tool-calling forever
    run = h.run("loop")
    assert run.reason == "max_turns"
    assert len(run.tool_calls) == 2


# ---------------------------------------------------------------------------
# Real tool execution + file bookkeeping
# ---------------------------------------------------------------------------


def test_scripted_tool_calls_execute_for_real(tmp_path: Path) -> None:
    run = create_harness(
        turns=[
            {
                "text": "writing",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "arguments": {"path": "hello.txt", "content": "hi there"},
                    },
                ],
            },
            {"text": "done"},
        ],
        workspace=tmp_path,
    ).run("create hello.txt")

    assert run.reason == "completed"
    assert (tmp_path / "hello.txt").read_text() == "hi there"  # really executed
    assert run.files_created == ["hello.txt"]
    assert run.files_modified == []
    assert [tc.name for tc in run.tool_calls] == ["write_file"]
    (_, result), = run.tool_results
    assert result.success is True


def test_file_modified_and_deleted_tracking(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old")
    (tmp_path / "b.txt").write_text("bye")
    run = create_harness(
        turns=[
            {
                "text": "changing",
                "tool_calls": [
                    {"name": "write_file", "arguments": {"path": "a.txt", "content": "new"}},
                    {"name": "bash", "arguments": {"command": "rm b.txt"}},
                ],
            },
            {"text": "done"},
        ],
        workspace=tmp_path,
    ).run("mutate the workspace")

    assert run.files_modified == ["a.txt"]
    assert run.files_deleted == ["b.txt"]


def test_malformed_tool_args_surface_as_error_result(tmp_path: Path) -> None:
    # write_file without its required args raises inside the tool; the real
    # executor converts that into an error ToolResult and the loop continues.
    run = create_harness(
        turns=[
            {"text": "oops", "tool_calls": [{"name": "write_file", "arguments": {}}]},
            {"text": "recovered"},
        ],
        workspace=tmp_path,
    ).run("bad args")

    (_, result), = run.tool_results
    assert result.success is False
    assert run.reason == "completed"  # the loop survived the bad call
    assert run.output_text == "recovered"


def test_unknown_tool_surfaces_as_error_result(tmp_path: Path) -> None:
    run = create_harness(
        turns=[
            {"text": "?", "tool_calls": [{"name": "no_such_tool", "arguments": {}}]},
            {"text": "ok"},
        ],
        workspace=tmp_path,
    ).run("call something missing")

    (_, result), = run.tool_results
    assert result.success is False
    assert "no_such_tool" in (result.error or "")


# ---------------------------------------------------------------------------
# Multi-turn scripts and history
# ---------------------------------------------------------------------------


def test_multi_turn_history_carries_between_runs(tmp_path: Path) -> None:
    h = create_harness(
        turns=[
            {"text": "checking", "tool_calls": [{"name": "list_files", "arguments": {}}]},
            {"text": "first answer"},
            {"text": "second answer"},
        ],
        workspace=tmp_path,
    )
    first = h.run("turn one")
    assert first.output_text == "first answer"
    # History mirrors production (CodingAgent keeps result.messages verbatim):
    # tool turns are fully recorded; the terminal assistant text is NOT in the
    # message list (the loop's completion branch omits it — the documented
    # quirk coding_agent_adapter works around; use output_text for the answer).
    assert len(h.history) >= 3  # user + assistant(tool) + tool result

    second = h.run("turn two")
    assert second.output_text == "second answer"
    # The second run's conversation contains the whole first turn.
    contents = [str(getattr(m, "content", "")) for m in second.messages]
    assert any("turn one" in c for c in contents)
    assert any("checking" in c for c in contents)
    assert any("turn two" in c for c in contents)
    assert h.provider.call_count == 3


# ---------------------------------------------------------------------------
# Scripted usage → cost accounting through the real loop
# ---------------------------------------------------------------------------


def test_scripted_usage_drives_real_cost_accounting(tmp_path: Path) -> None:
    usage = {"input_tokens": 1_000, "output_tokens": 200}
    per_call = calculate_cost("glm-5.2", usage)
    assert per_call > 0.0  # guard: the model is priced

    run = create_harness(
        turns=[
            {
                "text": "look",
                "tool_calls": [{"name": "list_files", "arguments": {}}],
                "usage": dict(usage),
            },
            {"text": "done", "usage": dict(usage)},
        ],
        workspace=tmp_path,
        model="glm-5.2",
    ).run("count the cost")

    assert run.usage["input_tokens"] == 2_000
    assert run.usage["output_tokens"] == 400
    assert run.cost_usd == pytest.approx(2 * per_call)


# ---------------------------------------------------------------------------
# Streaming: assistant chunks + scripted thinking chunks
# ---------------------------------------------------------------------------


def test_stream_mode_emits_scripted_thinking_chunks(tmp_path: Path) -> None:
    run = create_harness(
        turns=[{"thinking": ["hmm, ", "let me see"], "text": "the answer"}],
        workspace=tmp_path,
        stream=True,
    ).run("think first")

    assert run.thinking_chunks == ["hmm, ", "let me see"]
    assert run.streamed_text == "the answer"
    assert run.output_text == "the answer"
    types = run.event_types
    # Thinking streams before the committed assistant message.
    assert types.index(LoopEventType.thinking_chunk) < types.index(LoopEventType.assistant)


# ---------------------------------------------------------------------------
# Mid-stream steering injection
# ---------------------------------------------------------------------------


def test_mid_stream_steering_lands_in_the_conversation(tmp_path: Path) -> None:
    h = create_harness(
        turns=[
            {"text": "working", "tool_calls": [{"name": "list_files", "arguments": {}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
    )

    def steer_once(ev: LoopEvent) -> None:
        if ev.type == LoopEventType.tool_result:
            h.steer("STEER-MARKER: also check the README")

    run = h.run("start", on_event=steer_once)

    assert run.reason == "completed"
    # Drained mid-run (between the tool turn and the next model call) …
    assert h.message_queue.has_steering() is False
    # … and appended after the tool-turn record in the working conversation.
    contents = [str(getattr(m, "content", "")) for m in run.messages]
    steer_idx = next(i for i, c in enumerate(contents) if "STEER-MARKER" in c)
    tool_turn_idx = next(i for i, c in enumerate(contents) if "working" in c)
    assert tool_turn_idx < steer_idx
    assert run.output_text == "done"  # the model still finished the task


# ---------------------------------------------------------------------------
# Cancellation mid-turn
# ---------------------------------------------------------------------------


def test_abort_mid_turn_yields_aborted_result(tmp_path: Path) -> None:
    h = create_harness(
        turns=[
            {"text": "step", "tool_calls": [{"name": "list_files", "arguments": {}}]},
            {"text": "never reached"},
        ],
        workspace=tmp_path,
    )

    def cancel_on_first_tool(ev: LoopEvent) -> None:
        if ev.type == LoopEventType.tool_use:
            h.abort("harness cancel")

    run = h.run("start", on_event=cancel_on_first_tool)

    assert run.reason.startswith("aborted")
    assert run.error is None  # cooperative cancel is a clean terminal event
    assert h.provider.call_count == 1  # the second scripted step never played


# ---------------------------------------------------------------------------
# Provider error injection
# ---------------------------------------------------------------------------


def test_provider_error_step_surfaces_on_run_error(tmp_path: Path) -> None:
    run = create_harness(
        turns=[{"error": "simulated 429"}],
        workspace=tmp_path,
    ).run("boom")

    assert run.result is None
    assert run.reason == "error"
    assert isinstance(run.error, FauxProviderError)
    assert "simulated 429" in str(run.error)


# ---------------------------------------------------------------------------
# Workspace ownership
# ---------------------------------------------------------------------------


def test_owned_workspace_created_and_cleaned_up() -> None:
    with AgentHarness([{"text": "ok"}]) as h:
        ws = h.workspace
        assert ws.is_dir()
        run = h.run("go")
        assert run.reason == "completed"
    assert not ws.exists()  # context exit removes the harness-owned temp dir


def test_caller_workspace_is_not_deleted(tmp_path: Path) -> None:
    h = AgentHarness([{"text": "ok"}], workspace=tmp_path)
    h.cleanup()
    assert tmp_path.exists()


def test_script_and_provider_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        AgentHarness([{"text": "x"}], provider=object())


# ---------------------------------------------------------------------------
# The assembled path: AgentDriver / CodingAgent
# ---------------------------------------------------------------------------


def test_assembled_path_runs_the_full_stack(tmp_path: Path) -> None:
    usage = {"input_tokens": 1_000, "output_tokens": 100}
    h = create_assembled_harness(
        turns=[
            {
                "text": "writing",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "arguments": {"path": "made.txt", "content": "assembled"},
                    },
                ],
                "usage": dict(usage),
            },
            {"text": "finished", "usage": dict(usage)},
        ],
        workspace=tmp_path,
        model="glm-5.2",
        preset="minimal",
    )
    run = h.run("create made.txt")

    assert run.reason == "completed"
    assert (tmp_path / "made.txt").read_text() == "assembled"
    assert "made.txt" in run.files_created
    assert run.output_text == "finished"
    # Cost accrued on the driver exactly once (from the terminal result).
    assert h.driver.total_cost == pytest.approx(
        2 * calculate_cost("glm-5.2", usage),
    )
    # Multi-turn memory lives on the driver.
    assert any(
        "create made.txt" in str(getattr(m, "content", "")) for m in h.history
    )


def test_assembled_path_steer_and_cancel_seams_exist(tmp_path: Path) -> None:
    h = create_assembled_harness(
        turns=[{"text": "quick"}], workspace=tmp_path, preset="minimal",
    )
    # The seams delegate to the driver; exercising them must not raise.
    h.queue_follow_up("later")
    run = h.run("go")
    assert run.reason == "completed"


# ---------------------------------------------------------------------------
# Harness config passthrough
# ---------------------------------------------------------------------------


def test_config_forwards_loop_kwargs(tmp_path: Path) -> None:
    # loop_detector is a real AgentLoop kwarg: a detector that trips instantly
    # turns the run into a loop_detected terminal — proof config reaches the loop.
    class _TripDetector:
        def record(self, name: str, args: Any) -> None:  # noqa: ARG002
            pass

        def check(self) -> str:
            return "tripped"

    run = create_harness(
        turns=[
            {"text": "x", "tool_calls": [{"name": "list_files", "arguments": {}}]},
            {"text": "unreachable"},
        ],
        workspace=tmp_path,
        config={"loop_detector": _TripDetector()},
    ).run("go")

    assert run.reason == "loop_detected"
