"""Integration tests for the multiplexer app via Textual's run_test harness."""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig, Liveness  # noqa: E402
from chimera.tui.routing import RoutingMode  # noqa: E402
from chimera.types import ToolCall  # noqa: E402


class _ToolRes:
    output = "ok"
    success = True


class _Result:
    def __init__(self, cost, steps, reason="completed"):
        self.reason = reason
        self.turn_count = steps
        self.cost_usd = cost
        self.usage = {"input_tokens": 100, "output_tokens": 50}
        self.messages: list = []
        self.duration_ms = 10.0


class FakeDriver:
    """Scripted AgentDriver stand-in for one lane."""

    context_window = 1_000_000

    def __init__(self, model="glm-5.2", cost=0.001, steps=2, reason="completed"):
        self.model = model
        self.tools: list = []
        self.total_cost = 0.0
        self.history: list = []
        self._cost, self._steps, self._reason = cost, steps, reason
        self.steered: list[str] = []
        self.followups: list[str] = []
        self.cancelled = False
        self.cleared = False

    async def send(self, text):
        yield LoopEvent(LoopEventType.assistant_chunk, "hi ", 0)
        yield LoopEvent(LoopEventType.assistant_chunk, "there", 0)
        yield LoopEvent(LoopEventType.assistant, type("R", (), {"content": "hi there"})(), 0)
        yield LoopEvent(
            LoopEventType.tool_use,
            ToolCall(id="1", name="read_file", arguments={"path": "a.py"}), 0,
        )
        yield LoopEvent(
            LoopEventType.tool_result,
            (ToolCall(id="1", name="read_file", arguments={}), _ToolRes()), 0,
        )
        yield LoopEvent(LoopEventType.result, _Result(self._cost, self._steps, self._reason), 0)

    def steer(self, text):
        self.steered.append(text)

    def cancel(self):
        self.cancelled = True

    def clear(self):
        self.cleared = True

    def queue_follow_up(self, text):
        self.followups.append(text)


def _cohort(drivers, routing=RoutingMode.BROADCAST, task="fix the bug"):
    lanes = []
    for i, d in enumerate(drivers):
        cfg = LaneConfig(lane_id=chr(65 + i), label=f"{d.model}-{i}", model=d.model)
        lanes.append(Lane(cfg, d, None))
    return Cohort(lanes, task=task, routing=routing)


async def _submit(app, pilot, text):
    from chimera.tui.prompt import PromptArea
    app.query_one("#prompt", PromptArea).value = text
    await pilot.press("enter")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_broadcast_runs_all_lanes_and_records_telemetry():
    from textual.widgets import RichLog

    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = FakeDriver("glm-5.2", cost=0.001, steps=2), FakeDriver("glm-5.1", cost=0.002, steps=5)
    co = _cohort([d1, d2])
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "fix the bug")

        # Both lanes ran with their own telemetry (R-ISO-2).
        assert co.lanes[0].telemetry.cost == 0.001 and co.lanes[0].telemetry.steps == 2
        assert co.lanes[1].telemetry.cost == 0.002 and co.lanes[1].telemetry.steps == 5
        assert co.all_done and co.done_count == 2
        assert co.first_finisher is not None
        # Both panes rendered content.
        logs = list(app.query(RichLog))
        assert len(logs) == 2 and all(lg.lines for lg in logs)
        # Cohort summary is shown once every lane is done.
        assert app.query_one("#summary").display is True


@pytest.mark.asyncio
async def test_targeted_mode_only_touches_focused_lane():
    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = FakeDriver("m1"), FakeDriver("m2")
    co = _cohort([d1, d2], routing=RoutingMode.TARGETED)
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "only the focused lane")
        assert co.lanes[0].telemetry.turns == 1  # focus starts at lane A
        assert co.lanes[1].telemetry.turns == 0


@pytest.mark.asyncio
async def test_broadcast_steers_running_lane_and_starts_idle_lane():
    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = FakeDriver("m1"), FakeDriver("m2")
    co = _cohort([d1, d2])
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        from chimera.tui.prompt import PromptArea

        co.lanes[0].telemetry.liveness = Liveness.RUNNING  # A is mid-turn
        app.query_one("#prompt", PromptArea).value = "go"
        await pilot.press("enter")
        await pilot.pause()
        # A is steered (not restarted); B starts a fresh turn.
        assert d1.steered == ["go"]
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert co.lanes[1].telemetry.turns == 1


@pytest.mark.asyncio
async def test_focus_cycle_and_broadcast_toggle():
    from chimera.tui.multiplex import MultiplexApp

    co = _cohort([FakeDriver("m1"), FakeDriver("m2")])
    app = MultiplexApp(co)
    async with app.run_test():
        assert app._focus_index == 0
        app.action_focus_next_lane()
        assert app._focus_index == 1
        app.action_focus_prev_lane()
        assert app._focus_index == 0

        assert app._mode is RoutingMode.BROADCAST
        app.action_toggle_broadcast()
        assert app._mode is RoutingMode.TARGETED


@pytest.mark.asyncio
async def test_narrow_terminal_degrades_to_tabs():
    from chimera.tui.multiplex import MultiplexApp

    co = _cohort([FakeDriver("m1"), FakeDriver("m2"), FakeDriver("m3")])
    app = MultiplexApp(co)
    # 60 cols / 3 lanes = 20 < MIN_PANE_WIDTH (32) → tabbed.
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        assert app._tabbed is True
        assert app.query_one("#tabstrip").display is True
        visible = [p for p in app._panes if p.display]
        assert len(visible) == 1  # only the focused pane shows


@pytest.mark.asyncio
async def test_wide_terminal_tiles_all_panes():
    from chimera.tui.multiplex import MultiplexApp

    co = _cohort([FakeDriver("m1"), FakeDriver("m2")])
    app = MultiplexApp(co)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        assert app._tabbed is False
        assert all(p.display for p in app._panes)


@pytest.mark.asyncio
async def test_cancel_all_cancels_running_lanes():
    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = FakeDriver("m1"), FakeDriver("m2")
    co = _cohort([d1, d2])
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        co.lanes[0].telemetry.liveness = Liveness.RUNNING
        co.lanes[1].telemetry.liveness = Liveness.RUNNING
        app.action_cancel_all()
        await pilot.pause()
        assert d1.cancelled and d2.cancelled


@pytest.mark.asyncio
async def test_initial_task_auto_broadcasts():
    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = FakeDriver("m1", steps=2), FakeDriver("m2", steps=3)
    co = _cohort([d1, d2], task="auto go")
    app = MultiplexApp(co, initial_task="auto go")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert co.lanes[0].telemetry.turns == 1
        assert co.lanes[1].telemetry.turns == 1


@pytest.mark.asyncio
async def test_slash_commands_do_not_crash():
    from chimera.tui.prompt import PromptArea

    from chimera.tui.multiplex import MultiplexApp

    co = _cohort([FakeDriver("m1"), FakeDriver("m2")])
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        for cmd in ("/help", "/model", "/cost", "/tools", "/summary", "/target", "/broadcast"):
            app.query_one("#prompt", PromptArea).value = cmd
            await pilot.press("enter")
            await pilot.pause()
        assert app.is_running


@pytest.mark.asyncio
async def test_command_palette_does_not_crash():
    """Regression: SLASH_COMMANDS must not shadow Textual's App.COMMANDS.

    A class attr named ``COMMANDS`` is Textual's command-palette provider
    registry; filling it with strings made Ctrl+P raise
    ``TypeError: 'str' object is not callable`` and crash the app.
    """
    from textual.app import App

    from chimera.tui.multiplex import MultiplexApp

    # the catalog must live under a non-colliding name
    assert MultiplexApp.COMMANDS == App.COMMANDS
    assert all(isinstance(c, str) for c in MultiplexApp.SLASH_COMMANDS)

    co = _cohort([FakeDriver("m1"), FakeDriver("m2")])
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")   # opens the palette — must not crash
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.is_running


def test_launch_failure_rolls_back_workspaces(tmp_path, monkeypatch):
    """Regression: a driver-construction failure must not leak lane worktrees.

    Workspaces are provisioned before the drivers are built; an exception in
    that window (bad model/preset/loop spec, provider error) previously leaked
    N worktrees + branches with no cohort artifact explaining them.
    """
    import subprocess
    import sys as _sys

    import chimera.assembly.driver as driver_mod
    from chimera.tui.multiplex import run_multiplexer

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    class Boom(RuntimeError):
        pass

    def exploding_driver(*args, **kwargs):
        raise Boom("driver construction failed")

    monkeypatch.setattr(driver_mod, "AgentDriver", exploding_driver)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True)

    with pytest.raises(Boom):
        run_multiplexer(models="glm-5.2,glm-4.6", project_dir=str(repo))

    worktrees = subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert len(worktrees) == 1, f"leaked worktrees: {worktrees}"
    branches = subprocess.run(
        ["git", "branch", "--list", "chimera-lane-*"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert branches == "", f"leaked branches: {branches}"
