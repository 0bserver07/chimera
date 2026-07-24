"""Themes wired into the renderer and the app (R-THEME-1..4).

The pure schema/detection/quantization tests live in ``test_theme.py``; this
file pins the *wiring*: the renderer paints from slots, the app exports design
tokens, ``/theme`` switches with live preview, and — the additive pin — an
unconfigured app renders byte-identically to the pre-theme code.
"""
from __future__ import annotations

import pytest

pytest.importorskip("rich")

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.tui.render import (  # noqa: E402
    LaneTranscript,
    format_event,
    heartbeat_line,
)
from chimera.tui.theme import BUILTIN_THEMES, Palette, ThemeSettings  # noqa: E402
from chimera.types import ToolCall  # noqa: E402


class _Res:
    def __init__(self, output="out", success=True):
        self.output = output
        self.success = success


def _styles(renderable) -> list[str]:
    """The style strings rich applied, in span order."""
    return [str(span.style) for span in renderable.spans]


# -- renderer -------------------------------------------------------------
def test_default_palette_is_byte_identical_to_the_hardcoded_styles():
    ev = LoopEvent(
        LoopEventType.tool_use, ToolCall(id="1", name="bash", arguments={"cmd": "ls"}), 0,
    )
    before = format_event(ev, [])
    after = format_event(ev, [], palette=Palette())
    assert [t.plain for t in before] == [t.plain for t in after]
    assert _styles(before[0]) == _styles(after[0]) == ["yellow", "bold yellow", "dim"]


def test_a_theme_repaints_tool_rows_and_results():
    palette = Palette(BUILTIN_THEMES["chimera"], mode="dark", depth="truecolor")
    ev = LoopEvent(
        LoopEventType.tool_use, ToolCall(id="1", name="bash", arguments={}), 0,
    )
    out = format_event(ev, [], palette=palette)
    assert _styles(out[0])[0] == "#e5b567"  # tool.icon → $amber
    err = format_event(LoopEvent(LoopEventType.error, "boom", 0), [], palette=palette)
    assert _styles(err[0]) == [] and str(err[0].style) == "#f07178"


def test_no_color_palette_strips_color_but_keeps_structure():
    palette = Palette(BUILTIN_THEMES["chimera"], mode="dark", depth="none")
    out = format_event(
        LoopEvent(LoopEventType.tool_use, ToolCall(id="1", name="bash", arguments={}), 0),
        [], palette=palette,
    )
    # rich drops empty spans; what survives is structure only, never a color.
    assert _styles(out[0]) == ["bold"]


def test_transcript_palette_is_swappable_live():
    sink: list = []
    transcript = LaneTranscript(sink.append, markdown=False)
    transcript.palette = Palette(BUILTIN_THEMES["chimera"])
    transcript.handle(LoopEvent(LoopEventType.error, "nope", 0))
    assert str(sink[-1].style) == "#f07178"


def test_reasoning_trace_uses_the_theme_slot():
    sink: list = []
    transcript = LaneTranscript(
        sink.append, markdown=False, palette=Palette(BUILTIN_THEMES["mono"]),
    )
    transcript.handle(LoopEvent(LoopEventType.thinking_chunk, "hmm", 0))
    transcript.commit()
    assert "dim" in str(sink[-1].style)


def test_heartbeat_freezes_when_animations_are_off():
    frames = {heartbeat_line(5.0, 100, f, animate=False) for f in range(6)}
    assert len(frames) == 1
    assert "···" in frames.pop()
    assert len({heartbeat_line(5.0, 100, f) for f in range(6)}) > 1


def test_elision_marker_advertises_both_affordances():
    ev = LoopEvent(
        LoopEventType.tool_result,
        (ToolCall(id="1", name="read", arguments={}), _Res("\n".join(f"l{i}" for i in range(40)))),
        0,
    )
    plain_marker = format_event(ev, [], elide=True)[0].plain
    assert "…" in plain_marker and "expands" not in plain_marker
    hinted = format_event(ev, [], elide=True, expand_hint="ctrl+x", full_hint="f2")[0].plain
    assert "(ctrl+x expands · f2 full transcript)" in hinted
    only_expand = format_event(ev, [], elide=True, expand_hint="ctrl+x")[0].plain
    assert "(ctrl+x expands)" in only_expand  # byte-identical to the shipped form


# -- app wiring -----------------------------------------------------------
pytest.importorskip("textual")

from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402


class _Driver:
    model = "glm-5.2"
    context_window = 1000
    tools: list = []
    total_cost = 0.0
    history: list = []

    async def send(self, text):  # pragma: no cover - not exercised here
        if False:
            yield None

    def steer(self, text): ...
    def cancel(self): ...
    def clear(self): ...
    def queue_follow_up(self, text): ...


def _app(**kwargs):
    from chimera.tui.multiplex import MultiplexApp

    lane = Lane(LaneConfig(lane_id="A", label="a", model="glm-5.2"), _Driver(), None)
    return MultiplexApp(Cohort([lane], task="t"), **kwargs)


@pytest.mark.asyncio
async def test_default_app_exports_no_theme_css_variables():
    app = _app()
    async with app.run_test():
        assert app._palette.name == "default"
        variables = app.get_css_variables()
        # Nothing of ours leaked in: the framework's own tokens are untouched.
        assert variables == {**variables, **app._palette.css_variables()}
        assert app._palette.css_variables() == {}


@pytest.mark.asyncio
async def test_configured_theme_reaches_panes_and_css_variables():
    settings = ThemeSettings.resolve(
        {"theme": "chimera"}, env={"COLORTERM": "truecolor"},
    )
    app = _app(theme_settings=settings)
    async with app.run_test():
        assert app.get_css_variables()["primary"] == "#8f7ff0"
        pane = app._panes[0]
        assert pane._transcript.palette is not None
        assert pane._transcript.palette.name == "chimera"


@pytest.mark.asyncio
async def test_theme_command_lists_switches_and_rejects():
    app = _app()
    async with app.run_test() as pilot:
        app._handle_command("/theme list")
        await pilot.pause()
        app._handle_command("/theme chimera")
        await pilot.pause()
        assert app._palette.name == "chimera"
        assert app._panes[0]._transcript.palette.name == "chimera"
        app._handle_command("/theme nope")
        await pilot.pause()
        text = "\n".join(
            "".join(seg.text for seg in line) for line in app._panes[0].query_one(
                "RichLog"
            ).lines
        )
        assert "unknown theme" in text
        assert "chimera" in text


@pytest.mark.asyncio
async def test_theme_picker_previews_live_and_restores_on_cancel():
    app = _app()
    async with app.run_test() as pilot:
        app._handle_command("/theme")
        await pilot.pause()
        from chimera.tui.multiplex import ThemePickerScreen

        assert isinstance(app.screen, ThemePickerScreen)
        # Moving the highlight previews immediately.
        app.screen.action_cursor_move(1)
        await pilot.pause()
        assert app._palette.name != "default"
        await pilot.press("escape")
        await pilot.pause()
        assert app._palette.name == "default"  # restore-on-cancel


@pytest.mark.asyncio
async def test_animations_off_slows_the_pulse_and_freezes_the_glyph():
    settings = ThemeSettings.resolve({"animations": False}, env={})
    app = _app(theme_settings=settings)
    async with app.run_test():
        assert app._panes[0]._animations is False
        assert app._theme_settings.animations is False


@pytest.mark.asyncio
async def test_bad_theme_config_is_reported_not_fatal():
    settings = ThemeSettings.resolve({"theme": "ghost"}, env={})
    app = _app(theme_settings=settings)
    async with app.run_test() as pilot:
        await pilot.pause()
        text = "\n".join(
            "".join(seg.text for seg in line)
            for line in app._panes[0].query_one("RichLog").lines
        )
        assert "unknown theme" in text
        assert app._palette.name == "default"
