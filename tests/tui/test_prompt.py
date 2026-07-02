"""Tests for the shared prompt widget — multi-line input + slash autocomplete
(spec §13.5/§13.6)."""
import pytest

textual = pytest.importorskip("textual")

from chimera.tui.prompt import PromptArea, complete_command, filter_commands  # noqa: E402

CATALOG = ["/clear", "/cost", "/exit", "/help", "/history", "/model", "/tools"]


# -- pure helpers ---------------------------------------------------------
def test_filter_commands_prefix_matching():
    assert filter_commands("/h", CATALOG) == ["/help", "/history"]
    assert filter_commands("/help", CATALOG) == ["/help"]
    assert filter_commands("/", CATALOG) == CATALOG


def test_filter_commands_rejects_non_commands():
    assert filter_commands("hello", CATALOG) == []
    assert filter_commands("", CATALOG) == []
    assert filter_commands("/help now", CATALOG) == []  # args started
    assert filter_commands("x\n/help", CATALOG) == []   # not a bare prefix


def test_complete_command_unique_and_common_prefix():
    assert complete_command("/he", CATALOG) == "/help "        # unique
    assert complete_command("/co", CATALOG) == "/cost "        # unique
    assert complete_command("/h", CATALOG) == "/h"             # ambiguous, no growth
    assert complete_command("/hi", CATALOG) == "/history "     # unique
    assert complete_command("/zzz", CATALOG) == "/zzz"         # no match
    assert complete_command("plain", CATALOG) == "plain"       # not a command


# -- the widget -----------------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_area_multiline_submit_history_and_tab():
    from textual.app import App, ComposeResult

    class Host(App):
        def __init__(self) -> None:
            super().__init__()
            self.submitted: list[str] = []

        def compose(self) -> ComposeResult:
            yield PromptArea(commands=CATALOG, id="prompt")

        def on_prompt_area_submitted(self, ev: PromptArea.Submitted) -> None:
            self.submitted.append(ev.value)
            ev.prompt.remember(ev.value)
            ev.prompt.value = ""

    app = Host()
    async with app.run_test() as pilot:
        p = app.query_one("#prompt", PromptArea)
        p.focus()
        await pilot.pause()

        # Ctrl+J inserts a newline; Enter submits the multi-line text.
        await pilot.press("h", "i")
        await pilot.press("ctrl+j")
        await pilot.press("y", "o")
        assert p.value == "hi\nyo"
        await pilot.press("enter")
        await pilot.pause()
        assert app.submitted == ["hi\nyo"]
        assert p.value == ""

        # Up recalls the prior submission; Down returns to the draft.
        await pilot.press("up")
        assert p.value == "hi\nyo"
        await pilot.press("down")
        assert p.value == ""

        # Tab completes a unique "/" prefix.
        p.value = "/he"
        p.move_cursor(p.document.end)
        await pilot.pause()
        await pilot.press("tab")
        assert p.value == "/help "


@pytest.mark.asyncio
async def test_prompt_area_value_mirrors_text():
    from textual.app import App, ComposeResult

    class Host(App):
        def compose(self) -> ComposeResult:
            yield PromptArea(id="prompt")

    app = Host()
    async with app.run_test():
        p = app.query_one("#prompt", PromptArea)
        p.value = "abc"
        assert p.text == "abc"
        p.text = "xyz"
        assert p.value == "xyz"
