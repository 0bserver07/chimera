"""Tests for the stoat slash palette.

The palette is the only way to trigger the shell-mode toggle without the
hardware ``Ctrl-X`` keybinding (which not every terminal exposes), so
this test module is the primary line of defence against regressions in
``/shell`` semantics.
"""

from __future__ import annotations

from chimera.stoat.shell_mode import (
    MODE_AGENT,
    MODE_SHELL,
    ShellModeManager,
)
from chimera.stoat.slash import (
    SLASH_COMMANDS,
    SlashPalette,
    SlashResult,
    build_default_palette,
)


def _palette(model: str | None = "kimi-k2.6") -> SlashPalette:
    return build_default_palette(
        shell_mode=ShellModeManager(),
        model=model,
    )


def test_slash_commands_enumerable() -> None:
    """The canonical command list includes ``/shell``."""
    assert "/shell" in SLASH_COMMANDS
    assert "/help" in SLASH_COMMANDS
    assert "/exit" in SLASH_COMMANDS
    assert "/clear" in SLASH_COMMANDS
    assert "/model" in SLASH_COMMANDS
    assert "/cost" in SLASH_COMMANDS
    assert "/history" in SLASH_COMMANDS


def test_help_shows_palette() -> None:
    """``/help`` returns the slash palette text and keeps looping."""
    palette = _palette()
    result = palette.dispatch("/help")
    assert result.handled is True
    assert result.keep_going is True
    assert result.text is not None
    assert "/shell" in result.text


def test_exit_breaks_loop() -> None:
    """``/exit`` returns ``keep_going=False``."""
    result = _palette().dispatch("/exit")
    assert result.keep_going is False


def test_quit_aliases_exit() -> None:
    """``/quit`` is an alias for ``/exit``."""
    result = _palette().dispatch("/quit")
    assert result.keep_going is False


def test_clear_invokes_callback() -> None:
    """``/clear`` fires the configured ``on_clear`` callback."""
    fired: list[bool] = []
    palette = SlashPalette(
        shell_mode=ShellModeManager(),
        on_clear=lambda: fired.append(True),
    )
    result = palette.dispatch("/clear")
    assert fired == [True]
    assert result.text is not None
    assert "history cleared" in result.text


def test_model_show_when_arg_empty() -> None:
    """Bare ``/model`` shows the current model id."""
    palette = _palette(model="kimi-k2.6")
    result = palette.dispatch("/model")
    assert result.text is not None
    assert "kimi-k2.6" in result.text


def test_model_set_when_arg_provided() -> None:
    """``/model <id>`` updates the active id."""
    palette = _palette()
    result = palette.dispatch("/model gpt-4o")
    assert palette.model == "gpt-4o"
    assert "gpt-4o" in (result.text or "")


def test_shell_toggle_flips_state() -> None:
    """``/shell`` toggles the underlying shell-mode manager."""
    palette = _palette()
    assert palette.shell_mode.mode == MODE_AGENT
    result = palette.dispatch("/shell")
    assert palette.shell_mode.mode == MODE_SHELL
    assert "shell mode" in (result.text or "")
    result2 = palette.dispatch("/shell")
    assert palette.shell_mode.mode == MODE_AGENT
    assert "agent mode" in (result2.text or "")


def test_cost_renders_running_total() -> None:
    """``/cost`` formats ``cost_usd`` with four decimals."""
    palette = _palette()
    palette.cost_usd = 1.23456
    result = palette.dispatch("/cost")
    assert "$1.2346" in (result.text or "")


def test_history_default_renders_recent_lines() -> None:
    """``/history`` prints the last few submitted lines."""
    palette = _palette()
    palette.shell_mode.record("ls")
    palette.shell_mode.set_mode(MODE_SHELL)
    palette.shell_mode.record("pwd")
    result = palette.dispatch("/history")
    text = result.text or ""
    assert "> ls" in text
    assert "$ pwd" in text


def test_history_with_explicit_count() -> None:
    """``/history N`` honors the explicit count."""
    palette = _palette()
    for ch in ("a", "b", "c", "d"):
        palette.shell_mode.record(ch)
    result = palette.dispatch("/history 2")
    text = result.text or ""
    assert text.count("\n") == 1  # exactly two lines
    assert "c" in text
    assert "d" in text


def test_history_invalid_count() -> None:
    """A non-integer count surfaces a friendly error."""
    palette = _palette()
    result = palette.dispatch("/history abc")
    assert "expected an integer" in (result.text or "")


def test_unknown_slash_returns_handled_false() -> None:
    """Unknown slashes return ``handled=False`` so callers can respond."""
    result = _palette().dispatch("/bogus")
    assert result.handled is False
    assert "unknown command" in (result.text or "")


def test_slash_result_defaults() -> None:
    """:class:`SlashResult` defaults to keep-going / handled / no text."""
    sr = SlashResult()
    assert sr.keep_going is True
    assert sr.handled is True
    assert sr.text is None


def test_build_default_palette_creates_manager_when_none() -> None:
    """The factory creates a fresh manager when none is passed."""
    palette = build_default_palette()
    assert palette.shell_mode is not None
    assert palette.shell_mode.mode == MODE_AGENT


def test_build_default_palette_respects_existing_manager() -> None:
    """A pre-built manager is reused (mode preserved)."""
    mgr = ShellModeManager(mode=MODE_SHELL)
    palette = build_default_palette(shell_mode=mgr)
    assert palette.shell_mode is mgr
    assert palette.shell_mode.mode == MODE_SHELL
