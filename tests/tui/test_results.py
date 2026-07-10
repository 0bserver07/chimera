"""Tests for the in-UI cohort comparison view (spec §13.1)."""
import io
from types import SimpleNamespace

import pytest

textual = pytest.importorskip("textual")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.results import ResultsScreen, render_diff, scoreboard_table  # noqa: E402


def _lane(lid, model, diff_text=None):
    cfg = LaneConfig(lane_id=lid, label=lid, model=model, preset="coding_agent")
    ws = None
    if diff_text is not None:
        ws = SimpleNamespace(
            path="/tmp/x", strategy="worktree", base_commit="abc", branch="b",
            diff=lambda: diff_text,
        )
    return Lane(cfg, driver=SimpleNamespace(), workspace=ws)


def _finish(lane, cost, steps, order, reason="completed"):
    lane.on_turn_begin()
    lane.record(LoopEvent(
        LoopEventType.result,
        SimpleNamespace(reason=reason, turn_count=steps, cost_usd=cost,
                        usage={"input_tokens": 100, "output_tokens": 50}, messages=[]),
        0,
    ))
    lane.on_turn_end(order=order)


# -- pure helpers -------------------------------------------------------
def test_render_diff_colors_add_remove_hunk():
    lines = render_diff("diff --git a/f b/f\n@@ -1 +1 @@\n-old line\n+new line\n context")
    styles = [str(line.style) for line in lines]
    assert any("green" in s for s in styles)   # +new
    assert any("red" in s for s in styles)     # -old
    assert any("cyan" in s for s in styles)    # @@ hunk


def test_scoreboard_table_lists_ranked_lanes():
    from rich.console import Console

    a, b = _lane("A", "glm-5.2"), _lane("B", "glm-4.6")
    _finish(b, 0.002, 5, order=1)   # B wins
    _finish(a, 0.001, 2, order=2)
    co = Cohort([a, b], task="fix the bug")

    buf = io.StringIO()
    Console(file=buf, width=140).print(scoreboard_table(co))
    out = buf.getvalue()
    assert "glm-5.2" in out and "glm-4.6" in out
    assert "A" in out and "B" in out


# -- the screen ---------------------------------------------------------
@pytest.mark.asyncio
async def test_results_screen_renders_and_cycles_lanes():
    from textual.app import App
    from textual.widgets import RichLog

    a = _lane("A", "glm-5.2", diff_text="diff --git a/x b/x\n+added by A\n")
    b = _lane("B", "glm-4.6", diff_text=None)  # no workspace → placeholder
    _finish(a, 0.001, 2, order=1)
    _finish(b, 0.002, 3, order=2)
    co = Cohort([a, b], task="fix the bug")

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ResultsScreen(co))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ResultsScreen)
        # lane A (idx 0) diff rendered
        assert screen.query_one("#diff-body", RichLog).lines
        # cycle to lane B → placeholder still renders content
        screen.action_next_lane()
        await pilot.pause()
        assert screen._idx == 1
        assert screen.query_one("#diff-body", RichLog).lines
        # wrap-around
        screen.action_next_lane()
        await pilot.pause()
        assert screen._idx == 0
        # close pops back
        screen.action_close()
        await pilot.pause()
        assert not isinstance(app.screen, ResultsScreen)


@pytest.mark.asyncio
async def test_multiplex_show_results_pushes_screen_after_a_run():
    from chimera.tui.multiplex import MultiplexApp

    a = _lane("A", "glm-5.2", diff_text="+x\n")
    b = _lane("B", "glm-4.6", diff_text="+y\n")
    _finish(a, 0.001, 2, order=1)
    _finish(b, 0.002, 3, order=2)
    co = Cohort([a, b], task="fix")

    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_show_results()
        await pilot.pause()
        assert isinstance(app.screen, ResultsScreen)


@pytest.mark.asyncio
async def test_show_results_noops_before_any_run():
    from chimera.tui.multiplex import MultiplexApp

    co = Cohort([_lane("A", "glm-5.2"), _lane("B", "glm-4.6")], task=None)
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_show_results()          # nothing has run yet
        await pilot.pause()
        assert not isinstance(app.screen, ResultsScreen)  # no overlay pushed


@pytest.mark.asyncio
async def test_diff_lands_scrolled_to_top_on_lane_switch():
    """A rebuilt diff view must land at the TOP (auto_scroll left the old
    view at the bottom after every lane/file/split switch)."""
    from textual.app import App
    from textual.widgets import RichLog

    big = "diff --git a/x b/x\n" + "".join(f"+line {i}\n" for i in range(120))
    a = _lane("A", "glm-5.2", diff_text=big)
    b = _lane("B", "glm-4.6", diff_text=big)
    _finish(a, 0.001, 2, order=1)
    _finish(b, 0.002, 3, order=2)
    co = Cohort([a, b], task="fix")

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ResultsScreen(co))

    app = _Host()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        screen = app.screen
        log = screen.query_one("#diff-body", RichLog)
        assert log.max_scroll_y > 0            # diff overflows the pane
        assert log.scroll_y == 0               # initial view starts at top
        screen.action_next_lane()              # switch to lane B
        await pilot.pause()
        assert log.scroll_y == 0               # still lands at the top
