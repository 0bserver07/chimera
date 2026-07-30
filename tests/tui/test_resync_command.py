"""The TUI ``/resync`` command: focused-lane hot-swap through the pilot.

Drives ``MultiplexApp`` with Textual's run_test harness: the command reaches
the focused lane's driver, refuses while the lane is busy (nothing invoked),
degrades honestly on a driver without the seam (external lanes), and renders
the shared :class:`~chimera.assembly.resync.ResyncReport` lines into the
transcript on both the single-lane and multi-lane surfaces.
"""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from chimera.assembly.resync import BUSY_MESSAGE, KindDelta, ResyncReport  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig, Liveness  # noqa: E402
from chimera.tui.routing import RoutingMode  # noqa: E402


class ResyncFakeDriver:
    """Driver stand-in exposing the hot-swap seam."""

    context_window = 1_000_000

    def __init__(self, model="glm-5.2", report: ResyncReport | None = None):
        self.model = model
        self.tools: list = []
        self.total_cost = 0.0
        self.history: list = []
        self.resync_calls = 0
        self._report = report or ResyncReport(
            deltas=[
                KindDelta(kind="plugins", refreshed=["hotswap"]),
                KindDelta(kind="skills", added=["demo-skill"]),
            ],
            notes=["system prompt is reassembled every turn"],
        )

    def resync_resources(self) -> ResyncReport:
        self.resync_calls += 1
        return self._report

    async def send(self, text):
        if False:  # pragma: no cover - the command tests never run a turn
            yield None

    def steer(self, text):
        pass

    def queue_follow_up(self, text):
        pass

    def cancel(self):
        pass

    def clear(self):
        pass


class NoResyncDriver(ResyncFakeDriver):
    """A driver without the seam (the external-lane shape)."""

    resync_resources = None  # type: ignore[assignment]


def _cohort(drivers, routing=RoutingMode.TARGETED):
    lanes = []
    for i, d in enumerate(drivers):
        cfg = LaneConfig(lane_id=chr(65 + i), label=f"{d.model}-{i}", model=d.model)
        lanes.append(Lane(cfg, d, None))
    return Cohort(lanes, task=None, routing=routing)


def _log_lines(app, index=0):
    from textual.widgets import RichLog

    logs = list(app.query(RichLog))
    return [str(strip.text) for strip in logs[index].lines]


async def _type_command(app, pilot, text):
    from chimera.tui.prompt import PromptArea

    app.query_one("#prompt", PromptArea).value = text
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_resync_reaches_focused_lane_and_renders_report():
    from chimera.tui.multiplex import MultiplexApp

    driver = ResyncFakeDriver()
    app = MultiplexApp(_cohort([driver]))
    async with app.run_test() as pilot:
        await _type_command(app, pilot, "/resync")
        assert driver.resync_calls == 1
        text = "\n".join(_log_lines(app))
        assert "resync: plugins 1 refreshed · skills 1 added" in text
        assert "system prompt is reassembled every turn" in text


@pytest.mark.asyncio
async def test_resync_refused_while_lane_busy():
    from chimera.tui.multiplex import MultiplexApp

    driver = ResyncFakeDriver()
    cohort = _cohort([driver])
    app = MultiplexApp(cohort)
    async with app.run_test() as pilot:
        cohort.lanes[0].telemetry.liveness = Liveness.RUNNING
        await _type_command(app, pilot, "/resync")
        assert driver.resync_calls == 0  # refused before the driver was touched
        assert any(BUSY_MESSAGE in line for line in _log_lines(app))


@pytest.mark.asyncio
async def test_resync_degrades_on_driver_without_the_seam():
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_cohort([NoResyncDriver()]))
    async with app.run_test() as pilot:
        await _type_command(app, pilot, "/resync")
        assert any(
            "not supported by this lane's driver" in line for line in _log_lines(app)
        )


@pytest.mark.asyncio
async def test_resync_multi_lane_targets_only_the_focused_lane():
    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = ResyncFakeDriver("glm-5.2"), ResyncFakeDriver("glm-5.1")
    app = MultiplexApp(_cohort([d1, d2], routing=RoutingMode.BROADCAST))
    async with app.run_test() as pilot:
        await _type_command(app, pilot, "/resync")
        assert d1.resync_calls == 1 and d2.resync_calls == 0


@pytest.mark.asyncio
async def test_resync_failure_lines_render_in_transcript():
    from chimera.tui.multiplex import MultiplexApp

    report = ResyncReport(
        deltas=[KindDelta(kind="plugins", failed=[
            ("bad", "boom — previous registration restored"),
        ])],
    )
    driver = ResyncFakeDriver(report=report)
    app = MultiplexApp(_cohort([driver]))
    async with app.run_test() as pilot:
        await _type_command(app, pilot, "/resync")
        text = "\n".join(_log_lines(app))
        assert "! plugins bad: boom — previous registration restored" in text


def test_resync_is_in_both_surface_catalogs():
    from chimera.tui.commands import completion_catalog

    assert "/resync" in completion_catalog(single=True)
    assert "/resync" in completion_catalog(single=False)
