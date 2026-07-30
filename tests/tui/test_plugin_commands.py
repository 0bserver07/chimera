"""Plugin slash commands hosted by the TUI surfaces, exactly like built-ins.

Drives ``MultiplexApp`` with Textual's run_test harness on both the
single-lane surface and the multiplexer: a plugin-registered command is
listed in autocomplete, dispatches to its handler through the thin
:class:`~chimera.tui.commands.TUICommandContext` (transcript ``say``, the
focused lane's driver, busy state), disappears after an unload + ``/resync``
(catalog recomposed, dispatch refuses), and can never shadow a built-in —
the collision is rejected loudly at report time and the built-in wins.
"""
import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from chimera.assembly.resync import KindDelta, ResyncReport  # noqa: E402
from chimera.plugins.base import BasePlugin, ComponentRegistry  # noqa: E402
from chimera.plugins.manager import PluginManager  # noqa: E402
from chimera.plugins.ui import UIExtensionRegistry  # noqa: E402
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig, Liveness  # noqa: E402
from chimera.tui.routing import RoutingMode  # noqa: E402


@pytest.fixture(autouse=True)
def ui_reset():
    """Isolate every test from the process-global plugin UI registry."""
    UIExtensionRegistry._reset()
    yield
    UIExtensionRegistry._reset()


class FakeDriver:
    """Minimal driver satisfying what the command path touches."""

    context_window = 1_000_000

    def __init__(self, model="glm-5.2", report: ResyncReport | None = None):
        self.model = model
        self.tools: list = []
        self.total_cost = 0.0
        self.history: list = []
        self.resync_calls = 0
        self._report = report or ResyncReport(
            deltas=[KindDelta(kind="plugins", refreshed=["pilot"])],
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


class PilotPlugin(BasePlugin):
    """A real plugin contributing one slash command with provenance."""

    @property
    def name(self) -> str:
        return "pilot"

    def activate(self, registry: ComponentRegistry) -> None:
        UIExtensionRegistry.register_command(
            "pilot-hello",
            lambda session, env, args, out: out(f"hello from pilot ({args})"),
            help="say hello",
            plugin="pilot",
        )


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


def _prompt_commands(app):
    from chimera.tui.prompt import PromptArea

    return list(app.query_one("#prompt", PromptArea).commands)


@pytest.mark.asyncio
async def test_plugin_command_completes_and_runs_on_the_single_lane_surface():
    from chimera.tui.multiplex import MultiplexApp

    UIExtensionRegistry.register_command(
        "pilot-hello",
        lambda session, env, args, out: out(f"hello from pilot ({args})"),
        help="say hello",
        plugin="pilot",
    )
    app = MultiplexApp(_cohort([FakeDriver()]))
    async with app.run_test() as pilot:
        # listed in both autocomplete surfaces (hint catalog + Tab completion)
        assert "/pilot-hello" in app._slash_commands
        assert "/pilot-hello" in _prompt_commands(app)
        # /help renders it with its one-line description
        await _type_command(app, pilot, "/help")
        assert any("pilot-hello" in line for line in _log_lines(app))
        # dispatches to the handler; output lands in the transcript
        await _type_command(app, pilot, "/pilot-hello world")
        assert any(
            "hello from pilot (world)" in line for line in _log_lines(app)
        )


@pytest.mark.asyncio
async def test_handler_receives_the_focused_lane_context():
    from chimera.tui.multiplex import MultiplexApp

    seen = {}

    def handler(session, env, args, out):
        seen["driver"] = session.driver
        seen["lane_id"] = session.lane_id
        seen["busy"] = session.busy
        seen["single"] = session.single
        out("ok")

    UIExtensionRegistry.register_command("pilot-ctx", handler, plugin="pilot")
    driver = FakeDriver()
    cohort = _cohort([driver])
    app = MultiplexApp(cohort)
    async with app.run_test() as pilot:
        cohort.lanes[0].telemetry.liveness = Liveness.RUNNING  # mid-turn
        await _type_command(app, pilot, "/pilot-ctx")
        assert seen["driver"] is driver
        assert seen["lane_id"] == "A"
        assert seen["busy"] is True          # busy state is exposed, honestly
        assert seen["single"] is True
        assert any("ok" in line for line in _log_lines(app))


@pytest.mark.asyncio
async def test_plugin_command_disappears_after_unload_resync():
    from chimera.tui.multiplex import MultiplexApp

    manager = PluginManager()
    manager.load_plugin(PilotPlugin())

    class UnloadingDriver(FakeDriver):
        """Resync stand-in whose swap ends with the plugin unloaded."""

        def resync_resources(self) -> ResyncReport:
            self.resync_calls += 1
            manager.unload("pilot")
            return ResyncReport(
                deltas=[KindDelta(kind="plugins", removed=["pilot"])],
            )

    app = MultiplexApp(_cohort([UnloadingDriver()]))
    async with app.run_test() as pilot:
        assert "/pilot-hello" in app._slash_commands
        await _type_command(app, pilot, "/pilot-hello hi")
        assert any("hello from pilot (hi)" in line for line in _log_lines(app))

        await _type_command(app, pilot, "/resync")
        # the catalog recomposed: gone from both autocomplete surfaces,
        # and the delta was announced in the transcript
        assert "/pilot-hello" not in app._slash_commands
        assert "/pilot-hello" not in _prompt_commands(app)
        assert any(
            "slash catalog:" in line and "/pilot-hello" in line
            for line in _log_lines(app)
        )
        # dispatch agrees with the catalog: the command no longer exists
        await _type_command(app, pilot, "/pilot-hello again")
        assert any(
            "unknown command: /pilot-hello" in line for line in _log_lines(app)
        )


@pytest.mark.asyncio
async def test_plugin_command_added_mid_session_appears_after_resync():
    from chimera.tui.multiplex import MultiplexApp

    app = MultiplexApp(_cohort([FakeDriver()]))
    async with app.run_test() as pilot:
        assert "/pilot-hello" not in app._slash_commands
        # a hot-swapped plugin activation registers the command mid-session
        UIExtensionRegistry.register_command(
            "pilot-hello",
            lambda session, env, args, out: out(f"hello from pilot ({args})"),
            plugin="pilot",
        )
        await _type_command(app, pilot, "/resync")
        assert "/pilot-hello" in app._slash_commands
        assert "/pilot-hello" in _prompt_commands(app)
        assert any(
            "slash catalog: +/pilot-hello" in line for line in _log_lines(app)
        )
        await _type_command(app, pilot, "/pilot-hello now")
        assert any("hello from pilot (now)" in line for line in _log_lines(app))


@pytest.mark.asyncio
async def test_collision_is_rejected_loudly_and_the_builtin_wins():
    from chimera.tui.multiplex import MultiplexApp

    called = []
    UIExtensionRegistry.register_command(
        "help",
        lambda session, env, args, out: called.append(args),
        plugin="pilot",
    )
    app = MultiplexApp(_cohort([FakeDriver()]))
    async with app.run_test() as pilot:
        # rejected at report time, loudly, on mount
        text = "\n".join(_log_lines(app))
        assert "plugin command /help rejected" in text
        assert "built-ins win" in text
        # the shadowing token is not in autocomplete as a plugin command —
        # /help stays the built-in and dispatches as the built-in
        await _type_command(app, pilot, "/help")
        assert called == []
        assert any(line.startswith("commands:") for line in _log_lines(app))


@pytest.mark.asyncio
async def test_multi_lane_dispatch_scopes_to_the_focused_lane():
    from chimera.tui.multiplex import MultiplexApp

    seen = {}

    def handler(session, env, args, out):
        seen["driver"] = session.driver
        seen["lane_id"] = session.lane_id
        seen["single"] = session.single
        out(f"scoped to {session.lane_label}")

    UIExtensionRegistry.register_command("pilot-where", handler, plugin="pilot")
    d1, d2 = FakeDriver("glm-5.2"), FakeDriver("glm-5.1")
    app = MultiplexApp(_cohort([d1, d2], routing=RoutingMode.BROADCAST))
    async with app.run_test() as pilot:
        assert "/pilot-where" in app._slash_commands  # multi surface lists it
        await _type_command(app, pilot, "/pilot-where")
        # the handler saw the focused lane (A), not the cohort
        assert seen["driver"] is d1
        assert seen["lane_id"] == "A"
        assert seen["single"] is False
        # output scopes to the focused lane's pane only
        assert any("scoped to glm-5.2-0" in line for line in _log_lines(app, 0))
        assert not any("scoped to" in line for line in _log_lines(app, 1))
