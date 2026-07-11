"""Live-tail region + reasoning heartbeat (R-REN-6 made visible, R-FOLD-1),
and the ephemera contract (R-VIEW-3).

The pane-layer :class:`LiveRegion` shows the uncommitted markdown tail and the
thinking heartbeat below the transcript log. It is display state only: these
tests prove nothing it shows ever reaches ``Lane.record`` or the committed
sink stream, that its height is capped (top-ellipsized), that it hides when
empty, and that the log's follow-mode contract (R-VIEW-1) is untouched.
"""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from types import SimpleNamespace  # noqa: E402

from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.live_region import (  # noqa: E402
    LIVE_TAIL_MAX_LINES,
    LiveRegion,
    TailCrop,
)
from chimera.tui.logview import TranscriptLog  # noqa: E402
from chimera.tui.multiplex import LanePane, MultiplexApp  # noqa: E402
from chimera.tui.render import LaneTranscript, heartbeat_line, plain  # noqa: E402


def _chunk(text):
    return LoopEvent(LoopEventType.assistant_chunk, text, 0)


def _think(text):
    return LoopEvent(LoopEventType.thinking_chunk, text, 0)


def _assistant(content=""):
    return LoopEvent(LoopEventType.assistant, SimpleNamespace(content=content), 0)


class FakeClock:
    """Injectable monotonic clock — no real sleeps anywhere in this file."""

    def __init__(self, start=100.0):
        self.t = start

    def now(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _lane(lane_id="A"):
    cfg = LaneConfig(lane_id=lane_id, label=lane_id, model="glm-5.2")
    driver = SimpleNamespace(clear=lambda: None, tools=[])
    return Lane(cfg, driver=driver, workspace=None)


def _app(n=1):
    lanes = [_lane(chr(65 + i)) for i in range(n)]
    return MultiplexApp(Cohort(lanes, task=None)), lanes


# -- the tail region ---------------------------------------------------------
@pytest.mark.asyncio
async def test_tail_appears_during_stream_and_empties_on_flush():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        region = pane.query_one(LiveRegion)
        log = pane.query_one(TranscriptLog)
        assert region.display is False              # hidden when empty (start)

        pane.feed(_chunk("Intro paragraph.\n\n"))   # a completed block…
        await pilot.pause()
        assert region.display is False              # …commits: nothing live
        assert log.lines                            # it reached the log

        pane.feed(_chunk("live tail"))              # an unterminated block
        await pilot.pause()
        assert region.display is True
        assert region.tail_text == "live tail"

        pane.feed(_assistant("Intro paragraph.\n\nlive tail"))  # flush
        await pilot.pause()
        assert region.display is False              # emptied on commit
        assert region.tail_text == ""


@pytest.mark.asyncio
async def test_tail_view_trims_partial_closing_fence():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        region = pane.query_one(LiveRegion)
        pane.feed(_chunk("```py\ncode\n``"))        # close fence half-streamed
        await pilot.pause()
        # live_tail_view drops the partial fence so the render never shrinks.
        assert region.tail_text == "```py\ncode\n"


@pytest.mark.asyncio
async def test_height_cap_in_app():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        region = pane.query_one(LiveRegion)
        body = "\n".join(f"line {i}" for i in range(30))
        pane.feed(_chunk("```\n" + body + "\n"))    # open fence: all stays live
        await pilot.pause()
        assert region.display is True
        assert region.size.height <= LIVE_TAIL_MAX_LINES  # layout never jumps


def test_tail_crop_top_ellipsis_keeps_live_edge():
    console = Console(width=40)
    crop = TailCrop(Text("\n".join(f"line {i}" for i in range(20))), max_lines=6)
    with console.capture() as cap:
        console.print(crop)
    out = cap.get().splitlines()
    assert len(out) == 6                            # capped
    assert "… +15 lines" in out[0]                  # top-ellipsized, counted
    assert out[-1].strip() == "line 19"             # newest line stays visible


def test_tail_crop_passes_short_content_through():
    console = Console(width=40)
    crop = TailCrop(Text("one\ntwo"), max_lines=6)
    with console.capture() as cap:
        console.print(crop)
    assert cap.get().splitlines() == ["one", "two"]


# -- the reasoning heartbeat (R-FOLD-1) --------------------------------------
def test_heartbeat_line_format():
    # The shipped format: elapsed · honest char count · live rate pulse.
    assert heartbeat_line(5.0, 1200, frame=2) == "∴ Thinking ··· 5s · ~1.2k chars · 240 chars/s"
    # Below one second the rate would be noise — omitted.
    assert heartbeat_line(0.0, 42, frame=0) == "∴ Thinking ·   0s · 42 chars"
    # The frames cycle and keep a constant width (no line jitter).
    widths = {len(heartbeat_line(5.0, 1200, frame=f)) for f in range(12)}
    assert len(widths) == 1


def test_heartbeat_elapsed_monotonic_with_fake_clock():
    clock = FakeClock()
    sink = []
    t = LaneTranscript(sink.append, clock=clock.now)
    t.handle(_think("abc"))
    assert t.thinking_active and t.thinking_chars == 3
    assert t.thinking_elapsed == 0.0
    clock.advance(1.5)
    e1 = t.thinking_elapsed
    t.handle(_think("defg"))                        # more chunks keep the start
    clock.advance(2.0)
    e2 = t.thinking_elapsed
    assert 0.0 < e1 < e2 == 3.5                     # strictly monotonic
    assert t.thinking_chars == 7
    t.commit()
    assert not t.thinking_active
    assert t.thinking_elapsed == 0.0 and t.thinking_chars == 0
    # The committed trace carries elapsed + size + the reveal affordance…
    plains = [plain(r) for r in sink]
    assert "∴ thought for 3s (7 chars) — Ctrl+E to show" in plains
    # …and the reveal behavior is intact.
    assert t.reveal_last() is True
    assert any("abcdefg" in plain(r) for r in sink)


@pytest.mark.asyncio
async def test_heartbeat_in_pane_and_trace_on_completion():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        region = pane.query_one(LiveRegion)
        clock = FakeClock()
        pane._transcript._clock = clock.now

        pane.feed(_think("x" * 1200))
        await pilot.pause()
        assert region.display is True
        assert region.heartbeat_text.startswith("∴ Thinking")
        assert "~1.2k chars" in region.heartbeat_text
        assert "chars/s" not in region.heartbeat_text   # sub-second: no rate

        clock.advance(5.0)
        app._pulse_live()                               # app-level tick
        assert "5s" in region.heartbeat_text
        assert "240 chars/s" in region.heartbeat_text

        committed = []
        orig = pane._transcript._sink
        pane._transcript._sink = lambda r: (committed.append(r), orig(r))[-1]
        pane.feed(_chunk("answer\n\n"))                 # thinking block ends
        await pilot.pause()
        assert region.display is False                  # heartbeat + tail gone
        assert region.heartbeat_text == ""
        plains = [plain(r) for r in committed]
        assert any("∴ thought for 5s (~1.2k chars) — Ctrl+E to show" == p for p in plains)
        assert not any("Thinking" in p for p in plains)  # heartbeat never lands


@pytest.mark.asyncio
async def test_single_app_pulse_drives_all_panes():
    app, _ = _app(2)
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()
        assert app._live_timer is not None              # ONE app-level timer
        panes = list(app.query(LanePane))
        regions = [p.query_one(LiveRegion) for p in panes]
        clock = FakeClock()
        for pane in panes:
            pane._transcript._clock = clock.now
            pane.feed(_think("mm"))
        await pilot.pause()
        before = [r.heartbeat_text for r in regions]
        assert all(t.startswith("∴ Thinking") for t in before)
        app._pulse_live()                               # one tick, every pane
        after = [r.heartbeat_text for r in regions]
        assert after[0] != before[0]                    # animation advanced
        assert after[0] == after[1]                     # panes stay in phase


# -- interaction with follow-mode (R-VIEW-1 must be unaffected) ---------------
@pytest.mark.asyncio
async def test_live_region_updates_while_log_detached():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        region = pane.query_one(LiveRegion)
        log = pane.query_one(TranscriptLog)
        for i in range(60):
            pane.note(f"event {i}")
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y        # following
        log.scroll_to(y=0, animate=False)              # user scrolls up
        await pilot.pause()
        pane.feed(_chunk("Committed block.\n\n"))      # stream keeps going
        pane.feed(_chunk("still-live tail"))
        await pilot.pause()
        assert log.scroll_y == 0                       # view not yanked
        assert region.display is True                  # …but the live region
        assert region.tail_text == "still-live tail"   # keeps updating (fixed)


@pytest.mark.asyncio
async def test_follow_mode_still_pins_while_anchored():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        log = pane.query_one(TranscriptLog)
        for i in range(60):
            pane.note(f"event {i}")
        await pilot.pause()
        pane.feed(_chunk("More prose that commits.\n\n"))  # live region appears…
        pane.feed(_chunk("tail"))
        await pilot.pause()
        assert log.scroll_y == log.max_scroll_y        # …without breaking follow


# -- the ephemera contract (R-VIEW-3) -----------------------------------------
@pytest.mark.asyncio
async def test_committed_stream_byte_identical_with_widget_present():
    events = [
        _think("alpha "),
        _think("beta"),
        _chunk("First block.\n\n"),
        _chunk("Second "),
        _chunk("block tail"),
        _assistant("First block.\n\nSecond block tail"),
    ]
    clock = FakeClock()
    headless = []
    reference = LaneTranscript(headless.append, clock=clock.now)  # widget absent

    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        pane._transcript._clock = clock.now
        committed = []
        orig = pane._transcript._sink
        pane._transcript._sink = lambda r: (committed.append(r), orig(r))[-1]
        for ev in events:                              # same stream, lockstep
            reference.handle(ev)
            pane.feed(ev)
            clock.advance(0.1)
        reference.commit()
        pane.commit()
        await pilot.pause()
        assert [plain(r) for r in committed] == [plain(r) for r in headless]
        # Nothing ephemeral leaked into the committed stream.
        joined = "\n".join(plain(r) for r in committed)
        assert "Thinking" not in joined and "chars/s" not in joined
        assert pane.query_one(LiveRegion).display is False


def test_live_region_never_reaches_lane_record():
    lane = _lane()
    for ev in [
        _think("secret reasoning"),
        _chunk("Public block.\n\n"),
        _chunk("public tail"),
        _assistant("Public block.\n\npublic tail"),
    ]:
        lane.record(ev)
    text = lane.transcript_text()
    assert "Public block." in text and "public tail" in text
    # No heartbeat, no trace, no reasoning: the persisted transcript carries
    # only committed content (R-VIEW-3 is structural at Lane.record).
    assert "Thinking" not in text
    assert "thought for" not in text
    assert "∴" not in text
    assert "secret reasoning" not in text


# -- lane counts & lifecycle ---------------------------------------------------
@pytest.mark.asyncio
async def test_multi_lane_regions_are_independent():
    app, _ = _app(2)
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.pause()
        pane_a, pane_b = list(app.query(LanePane))
        pane_a.feed(_chunk("only lane A has a tail"))
        await pilot.pause()
        assert pane_a.query_one(LiveRegion).display is True
        assert pane_b.query_one(LiveRegion).display is False


@pytest.mark.asyncio
async def test_clear_action_drops_live_region():
    app, _ = _app(1)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = app.query_one(LanePane)
        region = pane.query_one(LiveRegion)
        pane.feed(_chunk("uncommitted tail"))
        await pilot.pause()
        assert region.display is True
        app.action_clear_lane()                        # single-lane Ctrl+L
        await pilot.pause()
        assert region.display is False and region.tail_text == ""


@pytest.mark.asyncio
async def test_turn_end_through_driver_clears_region_and_persists_clean():
    class ThinkingDriver:
        context_window = 1_000_000
        tools: list = []

        def __init__(self):
            self.cancelled = False

        async def send(self, text):
            yield _think("pondering the task ")
            yield _think("carefully")
            yield _chunk("The answer.\n\n")
            yield _chunk("A trailing tail")
            yield _assistant("The answer.\n\nA trailing tail")

        def steer(self, text):  # pragma: no cover - routing may probe these
            pass

        def cancel(self):
            self.cancelled = True

        def clear(self):
            pass

        def queue_follow_up(self, text):  # pragma: no cover
            pass

    cfg = LaneConfig(lane_id="A", label="A", model="glm-5.2")
    lane = Lane(cfg, driver=ThinkingDriver(), workspace=None)
    app = MultiplexApp(Cohort([lane], task=None))
    async with app.run_test(size=(80, 24)) as pilot:
        from chimera.tui.prompt import PromptArea

        app.query_one("#prompt", PromptArea).value = "go"
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        region = app.query_one(LiveRegion)
        assert region.display is False                 # cleared on turn end
        assert region.tail_text == "" and region.heartbeat_text == ""
        text = lane.transcript_text()                  # persisted transcript
        assert "The answer." in text and "A trailing tail" in text
        assert "Thinking" not in text and "∴" not in text
        assert "pondering" not in text
