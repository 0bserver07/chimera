"""Follow-mode (anchored) scrolling of the shared TranscriptLog (R-VIEW-1).

The contract: new content pins the view to the tail; the user scrolling up
detaches (the view freezes where they read); returning to the bottom — or
submitting their own input — re-attaches. A plain RichLog force-scrolls on
every write, which is the "wonky scroll" bug these tests pin down.
"""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from rich.text import Text  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402

from chimera.tui.logview import TranscriptLog  # noqa: E402


class LogHost(App):
    def compose(self) -> ComposeResult:
        yield TranscriptLog()


def _fill(log, n, prefix="line"):
    for i in range(n):
        log.write(Text(f"{prefix} {i}"))


@pytest.mark.asyncio
async def test_follows_tail_while_at_bottom():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        assert log.max_scroll_y > 0                    # content overflows
        assert log.scroll_y == log.max_scroll_y        # pinned to the tail


@pytest.mark.asyncio
async def test_user_scroll_up_freezes_view_during_stream():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        # User scrolls to the top (wheel/keys/scrollbar all route through
        # scroll_to, which releases the anchor).
        log.scroll_to(y=0, animate=False)
        await pilot.pause()
        _fill(log, 30, "more")                         # turn keeps streaming
        await pilot.pause()
        assert log.scroll_y == 0                       # view stays put


@pytest.mark.asyncio
async def test_returning_to_bottom_reattaches():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        log.scroll_to(y=0, animate=False)              # detach
        await pilot.pause()
        log.scroll_end(animate=False, immediate=True)  # user returns to tail
        await pilot.pause()
        _fill(log, 20, "tail")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y        # following again


@pytest.mark.asyncio
async def test_jump_to_tail_repins_after_detach():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        log.scroll_to(y=0, animate=False)              # user reads the top
        await pilot.pause()
        log.jump_to_tail()                             # their own input lands
        _fill(log, 5, "echo")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y


@pytest.mark.asyncio
async def test_clear_restarts_pinned():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        log.scroll_to(y=0, animate=False)              # detached…
        await pilot.pause()
        log.clear()                                    # …but /clear resets
        _fill(log, 40, "fresh")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y


@pytest.mark.asyncio
async def test_click_while_following_does_not_kill_follow():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        await pilot.mouse_down(log)                    # press detaches…
        await pilot.mouse_up(log)                      # …plain click at tail
        await pilot.pause()
        _fill(log, 10, "after-click")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y        # still following


@pytest.mark.asyncio
async def test_click_while_reading_scrolled_up_stays_detached():
    app = LogHost()
    async with app.run_test(size=(60, 12)) as pilot:
        log = app.query_one(TranscriptLog)
        _fill(log, 50)
        await pilot.pause()
        log.scroll_to(y=0, animate=False)              # user reads the top
        await pilot.pause()
        await pilot.mouse_down(log)                    # click mid-read must
        await pilot.mouse_up(log)                      # NOT yank to the tail
        await pilot.pause()
        _fill(log, 10, "streaming")
        await pilot.pause()
        assert log.scroll_y == 0


@pytest.mark.asyncio
async def test_multiplex_lane_uses_transcript_log_and_follows():
    from types import SimpleNamespace

    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Lane, LaneConfig
    from chimera.tui.multiplex import LanePane, MultiplexApp

    cfg = LaneConfig(lane_id="A", label="A", model="glm-5.2", preset="coding_agent")
    lane = Lane(cfg, driver=SimpleNamespace(), workspace=None)
    app = MultiplexApp(Cohort([lane], task=None))
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        log = pane.query_one(TranscriptLog)            # the wiring
        for i in range(60):
            pane.note(f"event {i}")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y        # streaming follows
        log.scroll_to(y=0, animate=False)              # user scrolls up
        await pilot.pause()
        for i in range(20):
            pane.note(f"late {i}")
        await pilot.pause()
        assert log.scroll_y == 0                       # not yanked
        pane.echo_user("new task")                     # own input re-pins
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y
