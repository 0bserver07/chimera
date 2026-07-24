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


def test_default_isolation_rule():
    """One lane defaults to inplace (daily-driver); 2+ isolate; explicit wins."""
    from chimera.tui.multiplex import default_isolation

    assert default_isolation(1, None) == "inplace"
    assert default_isolation(2, None) == "auto"
    assert default_isolation(3, None) == "auto"
    assert default_isolation(1, "worktree") == "worktree"  # explicit wins
    assert default_isolation(2, "inplace") == "inplace"    # explicit wins (even if unwise)


def test_single_model_launches_multiplexer_inplace(tmp_path, monkeypatch):
    """A single --models entry is a full multiplexer lane, not the Phase-1 app.

    With inplace isolation nothing is provisioned or torn down: the lane's
    workspace IS the project dir, and a launch failure must leave it untouched.
    """
    import sys as _sys

    import chimera.assembly.driver as driver_mod
    from chimera.tui.multiplex import run_multiplexer

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "keep.txt").write_text("precious\n")

    seen: dict = {}

    class Boom(RuntimeError):
        pass

    def capturing_driver(*args, **kwargs):
        seen.update(kwargs)
        raise Boom("stop after capture")

    monkeypatch.setattr(driver_mod, "AgentDriver", capturing_driver)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True)

    with pytest.raises(Boom):
        run_multiplexer(models="glm-5.2", project_dir=str(proj), isolation="inplace")

    # single lane accepted; its workspace is the real tree, left untouched
    assert seen["model"] == "glm-5.2"
    assert seen["project_dir"] == str(proj)
    assert (proj / "keep.txt").read_text() == "precious\n"


# -- in-TUI cohort resume (/cohorts picker + /resume) --------------------

def _persist_cohort(tmp_root, model="glm-5.2", task="earlier race"):
    """Persist a small real cohort artifact and return its id."""
    d = FakeDriver(model)
    co = _cohort([d], task=task)
    co.persist(root=tmp_root)
    return co.cohort_id


@pytest.mark.asyncio
async def test_cohorts_command_opens_picker(tmp_path):
    from chimera.tui.multiplex import CohortPickerScreen, MultiplexApp

    root = tmp_path / "cohorts"
    saved_id = _persist_cohort(root)
    app = MultiplexApp(_cohort([FakeDriver("m1")]), persist_root=str(root))
    async with app.run_test() as pilot:
        from chimera.tui.prompt import PromptArea
        app.query_one("#prompt", PromptArea).value = "/cohorts"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CohortPickerScreen)
        # the saved cohort is listed
        from textual.widgets import OptionList
        picker = app.screen.query_one(OptionList)
        assert picker.option_count == 1
        assert picker.get_option_at_index(0).id == saved_id
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CohortPickerScreen)


@pytest.mark.asyncio
async def test_resume_command_requests_and_exits(tmp_path):
    from chimera.tui.multiplex import MultiplexApp

    root = tmp_path / "cohorts"
    saved_id = _persist_cohort(root)
    app = MultiplexApp(_cohort([FakeDriver("m1")]), persist_root=str(root))
    async with app.run_test() as pilot:
        from chimera.tui.prompt import PromptArea
        app.query_one("#prompt", PromptArea).value = f"/resume {saved_id}"
        await pilot.press("enter")
        await pilot.pause()
    assert app.resume_request == saved_id


@pytest.mark.asyncio
async def test_resume_unknown_id_stays_running(tmp_path):
    from chimera.tui.multiplex import MultiplexApp

    root = tmp_path / "cohorts"
    _persist_cohort(root)
    app = MultiplexApp(_cohort([FakeDriver("m1")]), persist_root=str(root))
    async with app.run_test() as pilot:
        from chimera.tui.prompt import PromptArea
        app.query_one("#prompt", PromptArea).value = "/resume nope-1234"
        await pilot.press("enter")
        await pilot.pause()
        assert app.is_running
        assert app.resume_request is None


@pytest.mark.asyncio
async def test_resume_refused_while_lanes_busy(tmp_path):
    from chimera.tui.multiplex import MultiplexApp

    root = tmp_path / "cohorts"
    saved_id = _persist_cohort(root)
    co = _cohort([FakeDriver("m1")])
    app = MultiplexApp(co, persist_root=str(root))
    async with app.run_test() as pilot:
        co.lanes[0].telemetry.liveness = Liveness.RUNNING
        from chimera.tui.prompt import PromptArea
        app.query_one("#prompt", PromptArea).value = f"/resume {saved_id}"
        await pilot.press("enter")
        await pilot.pause()
        assert app.is_running
        assert app.resume_request is None


@pytest.mark.asyncio
async def test_picker_selection_requests_resume(tmp_path):
    from chimera.tui.multiplex import MultiplexApp

    root = tmp_path / "cohorts"
    saved_id = _persist_cohort(root)
    app = MultiplexApp(_cohort([FakeDriver("m1")]), persist_root=str(root))
    async with app.run_test() as pilot:
        from chimera.tui.prompt import PromptArea
        app.query_one("#prompt", PromptArea).value = "/resume"  # bare → picker
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # select the highlighted (only) cohort
        await pilot.pause()
    assert app.resume_request == saved_id


def test_run_cohort_loop_switches_cohorts(tmp_path, monkeypatch):
    """The runner persists + tears down each cohort, then relaunches on the
    requested one; a session with no request ends the loop."""
    import chimera.tui.multiplex as mux

    root = tmp_path / "cohorts"
    first = _cohort([FakeDriver("m1")], task="first")
    second = _cohort([FakeDriver("m2")], task="second")

    class FakeWS:
        def __init__(self):
            self.cleaned = 0
        def cleanup_all(self):
            self.cleaned += 1

    ws1, ws2 = FakeWS(), FakeWS()
    built = []

    class FakeApp:
        def __init__(self, cohort, **kwargs):
            self._cohort = cohort
            built.append(cohort)
            # first app requests a switch; second exits plainly
            self.resume_request = second.cohort_id if cohort is first else None
        def run(self, **kwargs):  # accept mouse=... like the real App.run
            pass

    monkeypatch.setattr(mux, "MultiplexApp", FakeApp)
    monkeypatch.setattr(
        mux, "_load_saved_cohort",
        lambda cid, **kw: (second, ws2) if cid == second.cohort_id else (_ for _ in ()).throw(FileNotFoundError(cid)),
    )

    out = mux._run_cohort_loop(first, ws1, persist_root=str(root))

    assert built == [first, second]          # relaunched on the requested cohort
    assert ws1.cleaned == 1 and ws2.cleaned == 1
    assert (root / first.cohort_id / "manifest.json").exists()
    assert (root / second.cohort_id / "manifest.json").exists()
    assert out == str(root / second.cohort_id)


# ---------------------------------------------------------------------------
# budgets (#170)
# ---------------------------------------------------------------------------

def test_parse_lane_specs_budget_field():
    from chimera.tui.multiplex import parse_lane_specs

    specs = parse_lane_specs(["glm-5.2:coding_agent:plan:$0.10/20steps"])
    assert specs[0]["budget"] == "$0.10/20steps"
    assert specs[0]["loop"] == "plan"
    assert specs[0]["label"] == "glm-5.2·plan"  # budget is not a label axis


def test_parse_lane_specs_budget_with_empty_positional_fields():
    from chimera.tui.multiplex import parse_lane_specs

    specs = parse_lane_specs(["glm-5.2:::$0.05"])
    assert specs[0]["budget"] == "$0.05"
    assert specs[0]["preset"] == "coding_agent"  # empty preset -> default
    assert specs[0]["loop"] == ""


def test_parse_lane_specs_rejects_bad_budget_and_extra_fields():
    from chimera.tui.multiplex import parse_lane_specs

    with pytest.raises(ValueError):
        parse_lane_specs(["glm-5.2:::notabudget"])
    with pytest.raises(ValueError):
        parse_lane_specs(["a:b:c:d:e"])


def test_coerce_and_resolve_budgets():
    from chimera.core.budget import BudgetSpec
    from chimera.tui.multiplex import _coerce_budget, _resolve_budgets

    assert _coerce_budget(None) is None
    assert _coerce_budget("$0.10") == BudgetSpec(max_cost_usd=0.10)
    assert _coerce_budget(BudgetSpec()) is None  # unset spec collapses to None
    # Explicit args win and short-circuit config discovery.
    lane, cohort = _resolve_budgets(None, "$0.10", "$1.00")
    assert lane == BudgetSpec(max_cost_usd=0.10)
    assert cohort == BudgetSpec(max_cost_usd=1.0)


@pytest.mark.asyncio
async def test_check_cohort_budget_cancels_busy_lanes():
    import time

    from chimera.core.budget import BudgetSpec
    from chimera.tui.multiplex import MultiplexApp

    d1, d2 = FakeDriver("m1"), FakeDriver("m2")
    co = _cohort([d1, d2])
    co.budget = BudgetSpec(max_cost_usd=0.001)
    app = MultiplexApp(co)
    async with app.run_test():
        app._race_start = time.monotonic()
        co.lanes[0].telemetry.cost = 0.005          # already over the aggregate cap
        co.lanes[0].telemetry.liveness = Liveness.RUNNING
        co.lanes[1].telemetry.liveness = Liveness.RUNNING
        app._check_cohort_budget()
        assert d1.cancelled and d2.cancelled        # cooperative cancel, both busy
        assert app._cohort_cancelled == {
            "A": "cohort_budget:cost", "B": "cohort_budget:cost",
        }


@pytest.mark.asyncio
async def test_check_cohort_budget_noop_without_budget_or_race():
    import time

    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver("m1")
    co = _cohort([d])  # no cohort budget
    app = MultiplexApp(co)
    async with app.run_test():
        app._race_start = time.monotonic()
        co.lanes[0].telemetry.cost = 999.0
        co.lanes[0].telemetry.liveness = Liveness.RUNNING
        app._check_cohort_budget()
        assert not d.cancelled


@pytest.mark.asyncio
async def test_cohort_cancelled_lane_reports_cohort_reason():
    # When a cohort-cancelled lane's turn ends, its honest cohort reason wins
    # over the driver's generic terminal reason.
    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver("m1")  # returns reason="completed"
    co = _cohort([d], routing=RoutingMode.TARGETED)
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        app._cohort_cancelled[co.lanes[0].id] = "cohort_budget:cost"
        await _submit(app, pilot, "go")
        assert co.lanes[0].telemetry.terminal_reason == "cohort_budget:cost"


@pytest.mark.asyncio
async def test_budget_slash_command_multi_sets_cohort():
    from chimera.tui.multiplex import MultiplexApp

    co = _cohort([FakeDriver("m1"), FakeDriver("m2")])
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/budget $0.001")
        assert co.budget is not None and co.budget.max_cost_usd == 0.001
        await _submit(app, pilot, "/budget off")
        assert co.budget is None


@pytest.mark.asyncio
async def test_budget_slash_command_single_sets_lane():
    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver("m1")
    co = _cohort([d], routing=RoutingMode.TARGETED)  # 1 lane -> single-lane surface
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/budget $0.05")
        assert co.lanes[0].config.budget is not None
        assert co.lanes[0].config.budget.max_cost_usd == 0.05


@pytest.mark.asyncio
async def test_budget_slash_command_inspect_lists_caps():
    from chimera.core.budget import BudgetSpec
    from chimera.tui.multiplex import MultiplexApp

    co = _cohort([FakeDriver("m1")])
    co.lanes[0].config.budget = BudgetSpec(max_cost_usd=0.10)
    app = MultiplexApp(co)
    async with app.run_test():
        lines = app._budget_status_lines()
        assert any("cost $0.0000/$0.10" in ln for ln in lines)
        assert any(ln.startswith("cohort:") for ln in lines)
def test_run_cohort_loop_threads_mouse_to_app_run(tmp_path, monkeypatch):
    """Track 1A: --no-mouse (mouse=False) must reach App.run(mouse=...) so the
    terminal keeps native click-drag selection / copy / scrollback."""
    import chimera.tui.multiplex as mux

    co = _cohort([FakeDriver("m1")], task="t")
    captured: dict = {}

    class FakeWS:
        def cleanup_all(self) -> None:
            pass

    class FakeApp:
        def __init__(self, cohort, **kwargs):
            self.resume_request = None  # exit after one iteration

        def run(self, **kwargs):
            captured["mouse"] = kwargs.get("mouse")

    monkeypatch.setattr(mux, "MultiplexApp", FakeApp)
    mux._run_cohort_loop(co, FakeWS(), mouse=False, persist_root=str(tmp_path))
    assert captured["mouse"] is False
