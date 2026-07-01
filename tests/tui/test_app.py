"""Smoke tests for the Chimera TUI via Textual's run_test harness."""
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
        self.steered: list[str] = []

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


@pytest.mark.asyncio
async def test_tui_runs_a_turn_and_renders():
    from textual.widgets import Input, RichLog

    from chimera.tui.app import ChimeraTUI

    app = ChimeraTUI(FakeDriver())
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "fix the bug"
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#transcript", RichLog).lines  # something rendered


@pytest.mark.asyncio
async def test_slash_commands_do_not_crash():
    from textual.widgets import Input

    from chimera.tui.app import ChimeraTUI

    d = FakeDriver()
    app = ChimeraTUI(d)
    async with app.run_test() as pilot:
        for cmd in ("/help", "/model", "/cost", "/tools", "/clear"):
            app.query_one("#prompt", Input).value = cmd
            await pilot.press("enter")
            await pilot.pause()
        assert app.is_running  # still alive after all commands
