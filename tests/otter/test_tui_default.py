"""Tests for the wave-11 A9 default-TUI dispatch logic in
:mod:`chimera.otter.cli`.

The dispatch decision tree (see :func:`chimera.otter.cli.run`) is:

    1. ``--no-tui`` / ``CHIMERA_NO_TUI=1``  → readline REPL
    2. ``--tui``                            → textual TUI
    3. stdout is a TTY *and* textual is
       importable, no overrides              → textual TUI + stderr hint
    4. otherwise (non-TTY or no extra)      → readline REPL

These tests do not exercise the live TUI event loop — see
``tests/otter/test_tui.py`` for the textual harness coverage. Here we
only verify the ``run()`` dispatch picks the correct branch by
monkeypatching the two terminal sinks (``_dispatch_tui`` and
``_run_readline_repl``) and asserting which one fired.
"""
from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

from chimera.otter import cli as otter_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**overrides: Any) -> argparse.Namespace:
    """Build a minimal otter argparse namespace for ``run()``.

    Only the fields the dispatch path reads need to be present; tests can
    override any of them via kwargs.
    """
    base: dict[str, Any] = {
        "subcommand": None,
        "print_mode": None,
        "tui": False,
        "no_tui": False,
        "model": "glm-5",
        "max_steps": 10,
        "cwd": None,
        "no_lsp": True,
        "no_rules": True,
        "no_mcp": True,
        "no_plugins": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture()
def patched_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[argparse.Namespace]]:
    """Replace both terminal sinks with recorders.

    Returns a dict with two keys (``tui`` and ``readline``) each holding a
    list of namespaces the dispatch routed there. Asserting on the
    lengths of those lists tells us which branch ``run()`` chose.
    """
    calls: dict[str, list[argparse.Namespace]] = {"tui": [], "readline": []}

    def _fake_tui(args: argparse.Namespace) -> int:
        calls["tui"].append(args)
        return 0

    def _fake_readline(args: argparse.Namespace) -> int:
        calls["readline"].append(args)
        return 0

    monkeypatch.setattr(otter_cli, "_dispatch_tui", _fake_tui)
    monkeypatch.setattr(otter_cli, "_run_readline_repl", _fake_readline)
    # Reset the module-level sentinel so each test exercises the probe
    # branch with the value it just monkeypatched.
    monkeypatch.setattr(otter_cli, "_TEXTUAL_AVAILABLE", None, raising=False)
    return calls


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def test_no_tui_flag_parses() -> None:
    """``chimera otter --no-tui`` parses without raising."""
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    parsed = parser.parse_args(["--no-tui"])
    assert getattr(parsed, "no_tui", False) is True


def test_no_tui_default_is_false() -> None:
    """Default ``no_tui`` namespace value is ``False``."""
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    parsed = parser.parse_args([])
    assert getattr(parsed, "no_tui", False) is False


# ---------------------------------------------------------------------------
# Dispatch decision tree
# ---------------------------------------------------------------------------


def test_no_tui_flag_routes_to_readline(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
) -> None:
    """``--no-tui`` → readline path, even on a TTY with textual installed."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: True)
    # Force isatty=True so we can confirm --no-tui beats the auto-launch.
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.delenv("CHIMERA_NO_TUI", raising=False)

    rc = otter_cli.run(_make_args(no_tui=True))

    assert rc == 0
    assert len(patched_dispatch["readline"]) == 1
    assert len(patched_dispatch["tui"]) == 0


def test_tui_flag_routes_to_textual(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
) -> None:
    """``--tui`` → textual path, even on a non-TTY."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: True)
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.delenv("CHIMERA_NO_TUI", raising=False)

    rc = otter_cli.run(_make_args(tui=True))

    assert rc == 0
    assert len(patched_dispatch["tui"]) == 1
    assert len(patched_dispatch["readline"]) == 0


def test_auto_tui_when_tty_and_textual_available(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """TTY + textual available + no flags → textual path + stderr hint."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: True)
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.delenv("CHIMERA_NO_TUI", raising=False)

    rc = otter_cli.run(_make_args())

    assert rc == 0
    assert len(patched_dispatch["tui"]) == 1
    assert len(patched_dispatch["readline"]) == 0
    err = capsys.readouterr().err
    assert "TUI activated" in err
    assert "--no-tui" in err
    assert "CHIMERA_NO_TUI=1" in err


def test_non_tty_falls_back_to_readline(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-TTY stdout (CI / pipes) → readline path; no TUI hint emitted."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: True)
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.delenv("CHIMERA_NO_TUI", raising=False)

    rc = otter_cli.run(_make_args())

    assert rc == 0
    assert len(patched_dispatch["readline"]) == 1
    assert len(patched_dispatch["tui"]) == 0
    assert "TUI activated" not in capsys.readouterr().err


def test_tty_without_textual_falls_back_to_readline(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """TTY but textual missing → readline path; no auto-TUI hint."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: False)
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.delenv("CHIMERA_NO_TUI", raising=False)

    rc = otter_cli.run(_make_args())

    assert rc == 0
    assert len(patched_dispatch["readline"]) == 1
    assert len(patched_dispatch["tui"]) == 0
    # The auto-launch hint must not fire when the TUI cannot launch.
    assert "TUI activated" not in capsys.readouterr().err


def test_chimera_no_tui_env_routes_to_readline(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
) -> None:
    """``CHIMERA_NO_TUI=1`` overrides auto-launch on a TTY with textual."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: True)
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.setenv("CHIMERA_NO_TUI", "1")

    rc = otter_cli.run(_make_args())

    assert rc == 0
    assert len(patched_dispatch["readline"]) == 1
    assert len(patched_dispatch["tui"]) == 0


def test_chimera_no_tui_env_other_value_does_not_opt_out(
    monkeypatch: pytest.MonkeyPatch,
    patched_dispatch: dict[str, list[argparse.Namespace]],
) -> None:
    """``CHIMERA_NO_TUI=0`` (or any non-``1``) does not force readline."""
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: True)
    fake_stdout = io.StringIO()
    fake_stdout.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(otter_cli.sys, "stdout", fake_stdout)
    monkeypatch.setenv("CHIMERA_NO_TUI", "0")

    rc = otter_cli.run(_make_args())

    assert rc == 0
    # ``0`` is not the documented opt-out value, so we still take the
    # auto-launch branch.
    assert len(patched_dispatch["tui"]) == 1
    assert len(patched_dispatch["readline"]) == 0


# ---------------------------------------------------------------------------
# Probe helper
# ---------------------------------------------------------------------------


def test_textual_available_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_textual_available`` caches its first probe result."""
    # Reset the sentinel so the first call performs the real import probe.
    monkeypatch.setattr(otter_cli, "_TEXTUAL_AVAILABLE", None, raising=False)
    first = otter_cli._textual_available()
    # Now flip the sentinel to ``False`` and confirm the cached value is
    # returned without re-probing.
    monkeypatch.setattr(otter_cli, "_TEXTUAL_AVAILABLE", False, raising=False)
    assert otter_cli._textual_available() is False
    # And to ``True``.
    monkeypatch.setattr(otter_cli, "_TEXTUAL_AVAILABLE", True, raising=False)
    assert otter_cli._textual_available() is True
    # The first probe returned a real bool — keep the assertion lax so
    # this test passes whether or not the [tui] extra is installed.
    assert first in (True, False)


def test_textual_available_real_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``[tui]`` is installed in the test env, the probe is True."""
    pytest.importorskip("textual")
    monkeypatch.setattr(otter_cli, "_TEXTUAL_AVAILABLE", None, raising=False)
    assert otter_cli._textual_available() is True
