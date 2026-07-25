"""OSC 133 shell-integration zone marks around committed turns.

Three things must hold, and each is pinned here:

1. **Off by default is byte-identical.** Not one extra byte reaches the
   terminal unless ``[tui] shell_integration`` is on.
2. **On is invisible.** The marks are zero-width: the visible content of a
   commit batch — what ``strip_ansi`` sees — is the same either way, so the
   inline hybrid's "each line fits the screen" contract cannot be broken by
   a mark.
3. **The marks land on the right rows.** Prompt marks on the echoed prompt
   row, output-start on the first row the turn produces, command-end queued
   for the next row.

The mark builders and the queue are stdlib-only (no terminal, no framework),
so those tests run under CI's no-extras posture. Only the frontend
integration needs the ``tui`` extra.
"""
from __future__ import annotations

import pytest

from chimera.tui import scrollback as sb
from chimera.tui.shell_marks import (
    COMMAND_END,
    COMMAND_START,
    OUTPUT_START,
    PROMPT_START,
    ShellMarks,
    command_end,
    load_shell_integration,
    mark,
)


# -- the vocabulary --------------------------------------------------------
def test_marks_are_osc_133_sequences_with_a_string_terminator():
    assert PROMPT_START == "\x1b]133;A\x1b\\"
    assert COMMAND_START == "\x1b]133;B\x1b\\"
    assert OUTPUT_START == "\x1b]133;C\x1b\\"
    assert COMMAND_END == "\x1b]133;D\x1b\\"
    assert command_end(0) == "\x1b]133;D;0\x1b\\"
    assert command_end(1) == "\x1b]133;D;1\x1b\\"
    assert command_end(None) == COMMAND_END


def test_marks_are_zero_width_and_strip_cleanly():
    """The module's own ANSI stripper already removes OSC — proof of inertness."""
    for sequence in (PROMPT_START, COMMAND_START, OUTPUT_START, command_end(0)):
        assert sb.strip_ansi(f"before{sequence}after") == "beforeafter"


def test_mark_builder_accepts_a_raw_parameter_string():
    assert mark("D;7") == "\x1b]133;D;7\x1b\\"


# -- the queue -------------------------------------------------------------
def test_disabled_queue_emits_nothing():
    marks = ShellMarks()                     # default: off
    marks.turn_start()
    marks.output_start()
    marks.turn_end(ok=False)
    assert marks.take() == ""


def test_enabled_queue_drains_in_order_and_empties():
    marks = ShellMarks(enabled=True)
    marks.turn_start()
    assert marks.take() == PROMPT_START + COMMAND_START
    assert marks.take() == ""                # drained
    marks.output_start()
    assert marks.take() == OUTPUT_START


def test_turn_end_reports_the_exit_status():
    marks = ShellMarks(enabled=True)
    marks.turn_end()
    assert marks.take() == command_end(0)
    marks.turn_end(ok=False)
    assert marks.take() == command_end(1)


def test_command_end_pairs_with_the_next_prompt_mark():
    """A shell emits D then A at its next prompt; so does the frontend."""
    marks = ShellMarks(enabled=True)
    marks.turn_end()
    marks.turn_start()
    assert marks.take() == command_end(0) + PROMPT_START + COMMAND_START


# -- the config knob -------------------------------------------------------
def test_shell_integration_is_off_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    assert load_shell_integration(str(tmp_path), home=str(tmp_path)) is False


def test_shell_integration_reads_the_unified_config_chain(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    scope = tmp_path / ".chimera"
    scope.mkdir()
    (scope / "config.toml").write_text("[tui]\nshell_integration = true\n")
    assert load_shell_integration(str(tmp_path / "p"), home=str(tmp_path)) is True


# -- the commit seam -------------------------------------------------------
def test_commit_lines_without_a_prefix_is_byte_identical():
    baseline = sb.commit_lines(["a", "b"], history_bottom=20, start_row=20)
    assert sb.commit_lines(
        ["a", "b"], history_bottom=20, start_row=20, prefix="",
    ) == baseline


def test_prefix_lands_at_column_1_of_the_first_row_only():
    seq = sb.commit_lines(
        ["one", "two"], history_bottom=20, start_row=20, prefix=PROMPT_START,
    )
    # after the first row's erase, before its content — and nowhere else
    assert f"{sb.CLEAR_TO_EOL}{PROMPT_START}one" in seq
    assert seq.count(PROMPT_START) == 1
    assert f"{sb.CLEAR_TO_EOL}two" in seq


def test_a_prefix_changes_nothing_visible():
    plain = sb.commit_lines(["one", "two"], history_bottom=20, start_row=20)
    marked = sb.commit_lines(
        ["one", "two"], history_bottom=20, start_row=20,
        prefix=PROMPT_START + COMMAND_START,
    )
    assert sb.strip_ansi(marked) == sb.strip_ansi(plain)


def test_nothing_to_commit_still_emits_nothing():
    assert sb.commit_lines([], history_bottom=20, start_row=20, prefix=PROMPT_START) == ""


def test_hybrid_screen_commit_passes_the_prefix_through():
    import io

    out = io.StringIO()
    screen = sb.HybridScreen(out, band_height=3, assume_bottom=True)
    screen.band_top = screen.geom.band_top
    screen.commit(["hello"], prefix=PROMPT_START)
    written = out.getvalue()
    assert PROMPT_START in written
    assert sb.strip_ansi(written).strip() == "hello"


# -- the inline frontend ---------------------------------------------------
def _drive_inline_turn(**kwargs) -> str:
    """Run one scripted turn through the inline frontend; return what it wrote.

    The same TTY-free drive :mod:`tests.tui.test_inline_frontend` uses: a fake
    driver replaying a scripted event list into a StringIO, with a quit
    pre-enqueued so the input loop leaves after the auto-submitted task.
    """
    import asyncio
    import io

    from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult
    from chimera.tui import inline_frontend as inf
    from chimera.tui.inline_frontend import InlineFrontend
    from chimera.tui.lane import Lane, LaneConfig

    class _Driver:
        context_window = 128_000
        total_cost = 0.0
        tools: list = []
        history: list = []

        async def send(self, text):
            yield LoopEvent(LoopEventType.assistant_chunk, "Hello ", turn=1)
            yield LoopEvent(LoopEventType.assistant_chunk, "world.\n\n", turn=1)
            yield LoopEvent(
                LoopEventType.result,
                LoopResult(
                    reason="completed", messages=[], usage={},
                    cost_usd=0.001, duration_ms=1.0, turn_count=1,
                ),
                turn=1,
            )

        def steer(self, text): ...
        def queue_follow_up(self, text): ...
        def cancel(self): ...
        def clear(self): ...
        def load_history(self, messages): ...

    out = io.StringIO()
    lane = Lane(LaneConfig(lane_id="A", label="glm-x", model="glm-x"), _Driver(), None)
    frontend = InlineFrontend(lane, out=out, markdown=False, **kwargs)
    frontend._input_q.put_nowait(inf._QUIT)
    asyncio.run(frontend.run(initial_task="do the thing"))
    return out.getvalue()


def test_inline_turn_is_bracketed_by_zone_marks():
    pytest.importorskip("rich")
    written = _drive_inline_turn(shell_marks=True)
    # the prompt zone rides the echoed prompt row, the output zone the first
    # row the turn produced, and the turn's end is queued for the next row
    assert PROMPT_START in written
    assert COMMAND_START in written
    assert OUTPUT_START in written
    assert written.index(PROMPT_START) < written.index(OUTPUT_START)
    assert "› do the thing" in sb.strip_ansi(written)


def test_inline_emits_no_marks_by_default():
    pytest.importorskip("rich")
    assert "133;" not in _drive_inline_turn()


def test_marks_do_not_change_what_the_user_sees():
    pytest.importorskip("rich")
    plain = sb.strip_ansi(_drive_inline_turn())
    marked = sb.strip_ansi(_drive_inline_turn(shell_marks=True))
    assert marked == plain
