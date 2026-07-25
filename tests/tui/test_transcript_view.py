"""The full-screen transcript overlay (R-FOLD-7) and its plain mode (R-VIEW-4).

The overlay's contract is *the record, whole*: the panes elide tool output for
display only, so what the pager shows must be the untruncated session record —
and the key it is opened with must be whatever the registry currently binds,
never a hardcoded string.
"""
from __future__ import annotations

import pytest

pytest.importorskip("rich")
pytest.importorskip("textual")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.transcript_view import (  # noqa: E402
    TranscriptScreen,
    match_lines,
    transcript_lines,
    view_rows,
)
from chimera.types import ToolCall  # noqa: E402


class _Result:
    def __init__(self, output: str, success: bool = True) -> None:
        self.output = output
        self.success = success


class _Driver:
    """Minimal DriverProtocol stand-in (the overlay never drives a turn)."""

    model = "test-model"
    context_window = 128_000
    tools: list = []
    total_cost = 0.0
    history: list = []

    def cancel(self) -> None:  # pragma: no cover - never called here
        pass


def _lane(lines: list[str] | None = None) -> Lane:
    lane = Lane(LaneConfig("A", "alpha", "test-model"), _Driver())
    for line in lines or []:
        lane.note(line)
    return lane


def _cohort():
    """A one-lane cohort — the daily-driver surface the overlay opens over."""
    from chimera.tui.cohort import Cohort

    return Cohort([_lane()], task="t")


def _tool_result(output: str) -> LoopEvent:
    return LoopEvent(
        LoopEventType.tool_result,
        (ToolCall(id="1", name="bash", arguments={}), _Result(output)),
        0,
    )


# -- pure helpers -----------------------------------------------------------
def test_transcript_lines_splits_multi_line_record_entries():
    lane = _lane(["› do the thing"])
    lane.record(_tool_result("one\ntwo\nthree"))
    assert transcript_lines(lane) == ["› do the thing", "one", "two", "three"]


def test_transcript_lines_are_untruncated_even_though_panes_elide():
    """R-FOLD-3/7: the record keeps everything; the overlay shows that."""
    body = "\n".join(f"line {i}" for i in range(400))
    lane = _lane()
    lane.record(_tool_result(body))
    lines = transcript_lines(lane)
    assert len(lines) == 400
    assert lines[0] == "line 0" and lines[-1] == "line 399"
    assert not any("+" in line and "lines" in line for line in lines)  # no marker


def test_transcript_lines_does_not_mutate_the_record():
    lane = _lane(["a", "b"])
    before = list(lane.transcript_lines)
    transcript_lines(lane)
    assert lane.transcript_lines == before


def test_match_lines_is_case_insensitive_substring():
    lines = ["Read file.py", "bash: ls", "read again"]
    assert match_lines(lines, "read") == [0, 2]
    assert match_lines(lines, "BASH") == [1]
    assert match_lines(lines, "nope") == []


def test_blank_query_matches_every_line():
    lines = ["a", "b", "c"]
    assert match_lines(lines, "") == [0, 1, 2]
    assert match_lines(lines, "   ") == [0, 1, 2]


def test_view_rows_rich_mode_carries_a_line_number_gutter():
    lines = [f"line {i}" for i in range(12)]
    rows = view_rows(lines, match_lines(lines, ""))
    assert rows[0].plain == " 1 │ line 0"     # width-aligned to the max number
    assert rows[11].plain == "12 │ line 11"


def test_view_rows_plain_mode_is_the_raw_text(monkeypatch):
    """R-VIEW-4: no gutter, no color — a selection yields the transcript."""
    lines = ["one", "two"]
    rows = view_rows(lines, [0, 1], plain=True)
    assert [r.plain for r in rows] == ["one", "two"]
    assert all(not r.spans for r in rows)


def test_view_rows_filtered_keeps_the_original_line_numbers():
    lines = ["alpha", "beta", "alpha again"]
    rows = view_rows(lines, match_lines(lines, "alpha"))
    assert [r.plain for r in rows] == ["1 │ alpha", "3 │ alpha again"]


# -- the screen -------------------------------------------------------------
@pytest.mark.asyncio
async def test_overlay_opens_on_the_bound_key_and_shows_the_full_record():
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_cohort())
    async with app.run_test() as pilot:
        lane = app._cohort.lanes[0]
        lane.record(_tool_result("\n".join(f"line {i}" for i in range(300))))
        await pilot.press("ctrl+f")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TranscriptScreen)
        assert len(screen._lines) >= 300
        assert "line 299" in screen._lines


@pytest.mark.asyncio
async def test_overlay_plain_toggle_and_search_and_close():
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_cohort())
    async with app.run_test() as pilot:
        lane = app._cohort.lanes[0]
        lane.record(_tool_result("alpha\nbeta\ngamma"))
        await pilot.press("ctrl+f")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TranscriptScreen)
        assert screen._plain is False
        await pilot.press("p")                      # R-VIEW-4 toggle
        assert screen._plain is True
        await pilot.press("p")
        assert screen._plain is False
        await pilot.press("slash")                  # focus the filter
        await pilot.pause()
        assert screen.query_one("#transcript-search").has_focus
        screen.query_one("#transcript-search").value = "beta"
        await pilot.pause()
        assert screen._query == "beta"
        assert match_lines(screen._lines, screen._query) == [
            i for i, ln in enumerate(screen._lines) if "beta" in ln
        ]
        await pilot.press("escape")                 # filter → pager
        await pilot.pause()
        assert app.screen is screen                 # not closed yet
        await pilot.press("escape")                 # pager → closed
        await pilot.pause()
        assert not isinstance(app.screen, TranscriptScreen)


@pytest.mark.asyncio
async def test_overlay_key_is_rebindable_and_the_marker_follows(tmp_path):
    """R-KEY-2/3 + R-FOLD-7: the elision marker names the bound overlay key."""
    from chimera.tui.multiplex import LanePane, MultiplexApp
    from chimera.tui.render import plain

    app = MultiplexApp(_cohort(), keybinds={"show_transcript": "f9"})
    async with app.run_test() as pilot:
        pane = app.query_one(LanePane)
        sink: list = []
        assert pane._transcript is not None
        pane._transcript._sink = sink.append
        pane.feed(_tool_result("\n".join(f"line {i}" for i in range(200))))
        [rendered] = sink
        assert "f9 full transcript" in plain(rendered)
        assert "ctrl+f" not in plain(rendered)
        await pilot.press("f9")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)


@pytest.mark.asyncio
async def test_overlay_does_not_stack_over_another_overlay():
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_cohort())
    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert isinstance(app.screen, TranscriptScreen)
        depth = len(app.screen_stack)
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert len(app.screen_stack) == depth
