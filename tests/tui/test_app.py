"""The single-agent TUI surface — a one-lane multiplexer (issue #172).

Bare ``chimera code --tui`` runs :class:`MultiplexApp` on one inplace lane;
this file pins that surface's behaviors plus the back-compat contract of the
deprecated ``chimera.tui.app`` shim (importable one release, delegating
``run_tui``).
"""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.types import ToolCall  # noqa: E402


class _Resp:
    content = "hi there"


class _ToolRes:
    output = "file contents"
    success = True


class _Result:
    reason = "completed"
    turn_count = 2
    cost_usd = 0.0012
    messages: list = []


class FakeDriver:
    """Minimal AgentDriver stand-in that streams a scripted turn."""

    model = "glm-5.2"
    context_window = 1_000_000

    def __init__(self) -> None:
        self.tools: list = []
        self.total_cost = 0.0
        self.history: list = []
        self.cancelled = False
        self.cleared = False
        self.steered: list[str] = []
        self.followups: list[str] = []

    async def send(self, text):
        yield LoopEvent(LoopEventType.assistant_chunk, "hi ", 0)
        yield LoopEvent(LoopEventType.assistant_chunk, "there", 0)
        yield LoopEvent(LoopEventType.assistant, _Resp(), 0)
        yield LoopEvent(
            LoopEventType.tool_use,
            ToolCall(id="1", name="read_file", arguments={"path": "a.py"}), 0,
        )
        yield LoopEvent(
            LoopEventType.tool_result,
            (ToolCall(id="1", name="read_file", arguments={}), _ToolRes()), 0,
        )
        yield LoopEvent(LoopEventType.result, _Result(), 0)

    def steer(self, text: str) -> None:
        self.steered.append(text)

    def cancel(self) -> None:
        self.cancelled = True

    def clear(self) -> None:
        self.history = []
        self.cleared = True

    def queue_follow_up(self, text: str) -> None:
        self.followups.append(text)


# -- deprecated shim: chimera.tui.app ------------------------------------
# Behavior coverage (turn rendering, slash commands, palette) migrated to
# the one-lane MultiplexApp tests below; the shim keeps only its
# back-compat contract for one deprecation cycle.

def test_chimera_tui_is_deprecated_but_importable():
    """One release of back-compat: the class imports, constructs with a
    DeprecationWarning, and still doesn't shadow Textual's palette registry."""
    from textual.app import App

    from chimera.tui.app import ChimeraTUI

    assert ChimeraTUI.COMMANDS == App.COMMANDS
    assert all(isinstance(c, str) for c in ChimeraTUI.SLASH_COMMANDS)
    with pytest.warns(DeprecationWarning, match="one-lane multiplexer"):
        ChimeraTUI(FakeDriver())


def test_run_tui_delegates_to_the_one_lane_multiplexer(monkeypatch, tmp_path):
    """run_tui keeps its signature but routes to run_single_agent."""
    import chimera.tui.multiplex as mux
    from chimera.tui.app import run_tui

    calls: dict = {}
    monkeypatch.setattr(
        mux, "run_single_agent", lambda **kw: calls.update(kw),
    )
    run_tui(model="m-x", project_dir=str(tmp_path), preset="minimal", max_turns=3)
    assert calls == {
        "model": "m-x",
        "project_dir": str(tmp_path),
        "preset": "minimal",
        "max_turns": 3,
    }


def test_bare_tui_cli_routes_to_single_agent(monkeypatch, tmp_path):
    """`chimera code --tui` (no --models) constructs the one-lane multiplexer
    path — chimera/tui/app.py is no longer load-bearing for the CLI."""
    import argparse

    import chimera.tui.multiplex as mux
    from chimera.cli import code as code_cli

    calls: dict = {}
    monkeypatch.setattr(
        mux, "run_single_agent", lambda **kw: calls.update(kw),
    )

    args = argparse.Namespace(
        mode="interactive", preset=None, model="glm-5.2",
        workdir=str(tmp_path), models="", tui=True,
        resume=None, list_cohorts=False, export=None,
        isolation=None, lane_cap=None, max_turns=None, print_mode=None,
    )
    assert code_cli.run_code(args) == 0
    assert calls["model"] == "glm-5.2"
    assert calls["preset"] == "coding_agent"
    assert calls["project_dir"] == str(tmp_path)
    assert calls["task"] is None


# =========================================================================
# The single-agent surface as a ONE-LANE MULTIPLEXER (issue #172).
#
# Bare ``chimera code --tui`` runs MultiplexApp with one inplace lane; these
# tests pin every user-visible behavior the retired single-agent app had —
# turn rendering, mid-turn steering, Ctrl+C cancel/quit, Ctrl+L clear,
# Ctrl+E reasoning, slash commands, autocomplete, prompt focus — plus the
# single-lane chrome degradation and the run_single_agent construction rules.
# =========================================================================

def _single_cohort(driver, task=None):
    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Lane, LaneConfig
    from chimera.tui.routing import RoutingMode

    cfg = LaneConfig(lane_id="A", label=driver.model, model=driver.model)
    return Cohort(
        [Lane(cfg, driver, None)], task=task, routing=RoutingMode.TARGETED,
    )


async def _submit(app, pilot, text):
    from chimera.tui.prompt import PromptArea

    app.query_one("#prompt", PromptArea).value = text
    await pilot.press("enter")
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.asyncio
async def test_single_lane_runs_a_turn_and_renders():
    from textual.widgets import RichLog

    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver()
    co = _single_cohort(d)
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "fix the bug")
        assert app.query_one(RichLog).lines  # something rendered
        assert co.lanes[0].telemetry.turns == 1


@pytest.mark.asyncio
async def test_single_lane_typing_while_running_steers():
    from chimera.tui.lane import Liveness
    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver()
    co = _single_cohort(d)
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        from chimera.tui.prompt import PromptArea

        co.lanes[0].telemetry.liveness = Liveness.RUNNING  # mid-turn
        app.query_one("#prompt", PromptArea).value = "also check the tests"
        await pilot.press("enter")
        await pilot.pause()
        assert d.steered == ["also check the tests"]


@pytest.mark.asyncio
async def test_single_lane_ctrl_c_cancels_then_exits():
    from chimera.tui.lane import Liveness
    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver()
    co = _single_cohort(d)
    app = MultiplexApp(co)
    async with app.run_test() as pilot:
        # Running: Ctrl+C cancels the turn, does NOT quit.
        co.lanes[0].telemetry.liveness = Liveness.RUNNING
        exits: list[bool] = []
        app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
        app.action_cancel_all()
        await pilot.pause()
        assert d.cancelled and not exits
        # Idle: Ctrl+C quits.
        co.lanes[0].telemetry.liveness = Liveness.DONE
        app.action_cancel_all()
        await pilot.pause()
        assert exits


@pytest.mark.asyncio
async def test_single_lane_ctrl_l_clears_and_ctrl_o_is_disabled():
    from textual.widgets import RichLog

    from chimera.tui.multiplex import MultiplexApp

    d = FakeDriver()
    app = MultiplexApp(_single_cohort(d))
    async with app.run_test() as pilot:
        # Ctrl+O is the multi-lane clear key; single mode disables it.
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert not d.cleared
        # Ctrl+L (ported from the single-agent app) clears, even with the
        # prompt focused, and confirms in the transcript.
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert d.cleared
        assert app.query_one(RichLog).lines  # "(conversation cleared)" note


@pytest.mark.asyncio
async def test_single_lane_ctrl_e_toggles_reasoning():
    from chimera.tui.multiplex import LanePane, MultiplexApp

    app = MultiplexApp(_single_cohort(FakeDriver()))
    async with app.run_test() as pilot:
        pane = app.query_one(LanePane)
        assert app._show_reasoning is False
        # Must fire from the (always-focused) prompt: the binding takes
        # priority over TextArea's own ctrl+e (cursor-to-line-end).
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert app._show_reasoning is True
        assert pane._transcript is not None
        assert pane._transcript.show_reasoning is True
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert app._show_reasoning is False


@pytest.mark.asyncio
async def test_single_lane_chrome_degrades():
    from chimera.tui.multiplex import LanePane, MultiplexApp
    from chimera.tui.prompt import PromptArea

    d = FakeDriver()
    app = MultiplexApp(_single_cohort(d))
    # Narrow enough that N>1 would go tabbed — one lane never does.
    async with app.run_test(size=(30, 20)) as pilot:
        await pilot.pause()
        assert app._tabbed is False
        assert app.query_one("#tabstrip").display is False
        pane = app.query_one(LanePane)
        assert pane.has_class("single")
        assert str(app.query_one(".lane-header").styles.display) == "none"
        # App-style status line: model · tools · cost · state.
        status = app._global_status_text()
        assert d.model in status and "tools" in status and "$" in status
        assert "lanes:" not in status  # no cohort noise
        assert app.query_one("#prompt", PromptArea).placeholder == "Ask, or /help …"
        assert app.title == "Chimera TUI"


@pytest.mark.asyncio
async def test_single_lane_slash_commands_do_not_crash():
    from chimera.tui.multiplex import MultiplexApp
    from chimera.tui.prompt import PromptArea

    d = FakeDriver()
    app = MultiplexApp(_single_cohort(d))
    async with app.run_test() as pilot:
        for cmd in ("/help", "/model", "/cost", "/tools", "/clear", "/summary"):
            app.query_one("#prompt", PromptArea).value = cmd
            await pilot.press("enter")
            await pilot.pause()
        assert app.is_running
        assert d.cleared  # /clear reached the driver


@pytest.mark.asyncio
async def test_single_lane_command_catalog_drops_routing_modes():
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_single_cohort(FakeDriver()))
    assert "/broadcast" not in app._slash_commands
    assert "/target" not in app._slash_commands
    # Every single-agent command from the retired app is still present.
    for cmd in ("/help", "/model", "/cost", "/tools", "/clear", "/exit", "/quit"):
        assert cmd in app._slash_commands
    # The richer multiplexer surface stays available to one lane.
    for cmd in ("/cohorts", "/resume", "/results", "/summary", "/export"):
        assert cmd in app._slash_commands


@pytest.mark.asyncio
async def test_single_lane_autocomplete_hint_and_prompt_focus():
    from textual.widgets import Static

    from chimera.tui.multiplex import MultiplexApp
    from chimera.tui.prompt import PromptArea

    app = MultiplexApp(_single_cohort(FakeDriver()))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.focused, PromptArea)  # prompt focused on mount
        app.query_one("#prompt", PromptArea).value = "/mo"
        await pilot.pause()
        hint = app.query_one("#hint", Static)
        assert hint.display is True
        assert "/model" in str(hint.render())


@pytest.mark.asyncio
async def test_multi_lane_chrome_and_bindings_unchanged():
    from chimera.tui.multiplex import MultiplexApp
    from chimera.tui.routing import RoutingMode

    from chimera.tui.cohort import Cohort
    from chimera.tui.lane import Lane, LaneConfig

    def lane(i):
        d = FakeDriver()
        return Lane(LaneConfig(lane_id=chr(65 + i), label=f"m{i}", model=d.model), d, None)

    app = MultiplexApp(Cohort([lane(0), lane(1)], routing=RoutingMode.BROADCAST))
    assert app._single is False
    assert "/broadcast" in app._slash_commands and "/target" in app._slash_commands
    # The single-lane Ctrl+L alias is disabled with 2+ lanes; the multi-lane
    # chrome actions stay live.
    assert app.check_action("clear_lane", ()) is False
    for action in ("focus_prev_lane", "toggle_broadcast", "cancel_focused", "clear_focused"):
        assert app.check_action(action, ()) is True
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Broadcast" in app.query_one("#prompt").placeholder
        assert app.title == "Chimera Multiplexer"


def test_run_single_agent_builds_one_lane_inplace_cohort(tmp_path, monkeypatch):
    """Bare --tui constructs a one-lane inplace multiplexer, model verbatim."""
    import sys as _sys

    import chimera.assembly.driver as driver_mod
    import chimera.tui.multiplex as mux
    from chimera.tui.routing import RoutingMode

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "keep.txt").write_text("precious\n")

    seen_driver: dict = {}
    captured: dict = {}

    class CapturingDriver:
        def __init__(self, **kwargs):
            seen_driver.update(kwargs)
            self.tools: list = []
            self.history: list = []

    def fake_loop(cohort, workspaces, **kwargs):
        captured["cohort"] = cohort
        captured["workspaces"] = workspaces
        captured["kwargs"] = kwargs
        return "cohort-dir"

    monkeypatch.setattr(driver_mod, "AgentDriver", CapturingDriver)
    monkeypatch.setattr(mux, "_run_cohort_loop", fake_loop)
    monkeypatch.setattr(_sys.stdout, "isatty", lambda: True)

    out = mux.run_single_agent(
        model="prov:tagged-model",  # colons must survive (no lane-spec parse)
        project_dir=str(proj),
        preset="coding_agent",
        task="fix it",
        max_turns=7,
    )

    assert out == "cohort-dir"
    # Model string reached the driver verbatim; extra kwargs forwarded.
    assert seen_driver["model"] == "prov:tagged-model"
    assert seen_driver["preset"] == "coding_agent"
    assert seen_driver["max_turns"] == 7

    co = captured["cohort"]
    assert len(co.lanes) == 1
    assert co.isolation == "inplace"
    assert co.routing is RoutingMode.TARGETED
    lane = co.lanes[0]
    assert lane.label == "prov:tagged-model"
    assert lane.workspace is not None and lane.workspace.strategy == "inplace"
    # Inplace: the lane's workspace IS the project tree, untouched.
    assert lane.workspace.path == proj.resolve()
    assert (proj / "keep.txt").read_text() == "precious\n"
    assert captured["kwargs"]["initial_task"] == "fix it"
    assert captured["kwargs"]["max_turns"] == 7
