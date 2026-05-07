"""Tests for the stoat Ctrl-X chord input adapter.

The adapter has two backends — :mod:`prompt_toolkit` when installed and
``input()`` otherwise. The tests below pin behaviour we can drive
without a real TTY:

* The probe :func:`prompt_toolkit_available` returns a bool.
* The chord handlers (``_handle_plan_chord`` / ``_handle_shell_chord`` /
  ``_handle_help_chord``) flip the right managers and fire the right
  callbacks. They're invoked directly so the tests don't need a
  prompt_toolkit event-loop.
* When ``force_fallback=True``, :meth:`InputAdapter.read_line` routes
  through the user-supplied ``input()`` replacement (we monkeypatch
  ``builtins.input``).
* The fallback hint is emitted to stderr exactly once when
  prompt_toolkit isn't installed.
* The prompt prefix follows the active posture (plan > shell > agent).
"""

from __future__ import annotations

import builtins
import io

import pytest

from chimera.stoat.keybindings import (
    CHORD_HELP_TEXT,
    ChordCallbacks,
    InputAdapter,
    build_input_adapter,
    prompt_toolkit_available,
)
from chimera.stoat.plan_mode import PlanModeManager
from chimera.stoat.shell_mode import MODE_AGENT, MODE_SHELL, ShellModeManager


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def test_prompt_toolkit_available_returns_bool() -> None:
    """The probe returns a real bool; either backend is acceptable."""
    val = prompt_toolkit_available()
    assert isinstance(val, bool)


# ---------------------------------------------------------------------------
# Adapter construction + posture-aware prompt
# ---------------------------------------------------------------------------


def test_adapter_force_fallback_disables_prompt_toolkit_session() -> None:
    """``force_fallback=True`` skips the prompt_toolkit probe."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        force_fallback=True,
    )
    assert adapter._available is False  # implementation detail, but pinning matters
    assert adapter._session is None


def test_adapter_prompt_follows_shell_mode() -> None:
    """The rendered prompt prefix matches the active shell-mode toggle."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    adapter = InputAdapter(shell_mode=sm, plan_mode=pm, force_fallback=True)

    assert adapter.current_prompt() == sm.agent_prompt
    sm.toggle()
    assert adapter.current_prompt() == sm.shell_prompt


def test_adapter_prompt_plan_overrides_shell() -> None:
    """Plan mode wins over shell mode when both are active."""
    sm = ShellModeManager(mode=MODE_SHELL)
    pm = PlanModeManager(active=True)
    adapter = InputAdapter(shell_mode=sm, plan_mode=pm, force_fallback=True)
    assert adapter.current_prompt() == pm.plan_prompt


# ---------------------------------------------------------------------------
# Chord handlers (drive directly — no prompt_toolkit loop required)
# ---------------------------------------------------------------------------


def test_chord_plan_toggles_manager_and_fires_callback() -> None:
    """``Ctrl-X p`` flips plan mode + invokes ``on_plan_toggle``."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    seen: list[bool] = []
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        callbacks=ChordCallbacks(on_plan_toggle=seen.append),
        force_fallback=True,
    )

    adapter._handle_plan_chord()
    assert pm.is_active() is True
    assert seen == [True]

    adapter._handle_plan_chord()
    assert pm.is_active() is False
    assert seen == [True, False]


def test_chord_shell_toggles_manager_and_fires_callback() -> None:
    """``Ctrl-X s`` flips shell mode + invokes ``on_shell_toggle``."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    seen: list[str] = []
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        callbacks=ChordCallbacks(on_shell_toggle=seen.append),
        force_fallback=True,
    )

    adapter._handle_shell_chord()
    assert sm.is_shell_mode() is True
    assert seen == [MODE_SHELL]

    adapter._handle_shell_chord()
    assert sm.is_agent_mode() is True
    assert seen == [MODE_SHELL, MODE_AGENT]


def test_chord_shell_disables_plan_mode_first() -> None:
    """Entering shell mode while plan-mode is active leaves plan mode."""
    sm = ShellModeManager()
    pm = PlanModeManager(active=True)
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        force_fallback=True,
    )

    adapter._handle_shell_chord()
    assert pm.is_active() is False
    assert sm.is_shell_mode() is True


def test_chord_help_invokes_callback_with_help_text() -> None:
    """``Ctrl-X h`` calls ``on_help`` with the canonical blurb."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    seen: list[str] = []
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        callbacks=ChordCallbacks(on_help=seen.append),
        force_fallback=True,
    )

    adapter._handle_help_chord()
    assert seen == [CHORD_HELP_TEXT]


def test_chord_help_falls_back_to_stderr_when_no_callback() -> None:
    """Without ``on_help``, the chord blurb lands on the stderr stream."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    err = io.StringIO()
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        stderr=err,
        force_fallback=True,
    )

    adapter._handle_help_chord()
    assert "Ctrl-X chord" in err.getvalue()


# ---------------------------------------------------------------------------
# Fallback ``read_line`` flow
# ---------------------------------------------------------------------------


def test_read_line_uses_input_in_fallback_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdlib backend reads via ``builtins.input`` and returns the line."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    adapter = InputAdapter(shell_mode=sm, plan_mode=pm, force_fallback=True)

    monkeypatch.setattr(builtins, "input", lambda _prompt: "hello world")

    line = adapter.read_line()
    assert line == "hello world"


def test_fallback_hint_emitted_only_when_prompt_toolkit_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-line stderr hint fires iff ``prompt_toolkit`` isn't installed."""
    sm = ShellModeManager()
    pm = PlanModeManager()
    err = io.StringIO()
    adapter = InputAdapter(
        shell_mode=sm,
        plan_mode=pm,
        stderr=err,
        force_fallback=True,
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: "x")

    # When prompt_toolkit *is* installed in the dev env, we don't want
    # the hint — pin the test to behave correctly either way.
    pt_available = prompt_toolkit_available()
    adapter.read_line()
    if pt_available:
        assert err.getvalue() == ""
    else:
        assert "prompt_toolkit" in err.getvalue()
        # And the hint is only emitted once, not on every read.
        first_emission = err.getvalue()
        adapter.read_line()
        # Stderr must not have grown after the second read_line call.
        assert err.getvalue() == first_emission


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_build_input_adapter_creates_plan_manager_when_missing() -> None:
    """``build_input_adapter`` falls back to a fresh inactive PlanModeManager."""
    adapter = build_input_adapter(
        shell_mode=ShellModeManager(),
        force_fallback=True,
    )
    assert isinstance(adapter, InputAdapter)
    assert adapter.plan_mode.is_active() is False


def test_build_input_adapter_honors_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """``STOAT_NO_CHORD=1`` forces the fallback path even when prompt_toolkit installs."""
    monkeypatch.setenv("STOAT_NO_CHORD", "1")
    adapter = build_input_adapter(
        shell_mode=ShellModeManager(),
        plan_mode=PlanModeManager(),
    )
    assert adapter._available is False


def test_build_input_adapter_passes_callbacks_through() -> None:
    """User-supplied callbacks reach the adapter."""
    plan_calls: list[bool] = []
    cb = ChordCallbacks(on_plan_toggle=plan_calls.append)
    adapter = build_input_adapter(
        shell_mode=ShellModeManager(),
        plan_mode=PlanModeManager(),
        callbacks=cb,
        force_fallback=True,
    )
    adapter._handle_plan_chord()
    assert plan_calls == [True]
