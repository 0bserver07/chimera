"""Tests for ``chimera.weasel.modes`` — the four-mode dispatcher.

These tests exercise the dispatcher in isolation: no real REPL, no real
provider, no real RPC server. Each mode runner is monkeypatched to a
sentinel so we verify routing alone.
"""
from __future__ import annotations

import argparse
from typing import Any

import pytest

from chimera.weasel import modes
from chimera.weasel.modes import WeaselMode, dispatch_mode


# ---------------------------------------------------------------------------
# WeaselMode enum
# ---------------------------------------------------------------------------


def test_weaselmode_values() -> None:
    """The enum exposes exactly the four documented modes."""
    assert {m.value for m in WeaselMode} == {
        "interactive", "print", "rpc", "sdk",
    }


def test_from_args_explicit_mode() -> None:
    """Explicit ``--mode rpc`` wins over inference."""
    ns = argparse.Namespace(mode="rpc", prompt=None)
    assert WeaselMode.from_args(ns) is WeaselMode.RPC


def test_from_args_explicit_each_mode() -> None:
    """All four mode strings round-trip through the resolver."""
    for value in ("interactive", "print", "rpc", "sdk"):
        ns = argparse.Namespace(mode=value, prompt=None)
        assert WeaselMode.from_args(ns).value == value


def test_from_args_prompt_implies_print() -> None:
    """``-p "..."`` with no ``--mode`` resolves to PRINT."""
    ns = argparse.Namespace(mode=None, prompt="hello")
    assert WeaselMode.from_args(ns) is WeaselMode.PRINT


def test_from_args_default_interactive() -> None:
    """Bare ``chimera weasel`` with no args resolves to INTERACTIVE."""
    ns = argparse.Namespace(mode=None, prompt=None)
    assert WeaselMode.from_args(ns) is WeaselMode.INTERACTIVE


def test_from_args_unknown_mode_raises() -> None:
    """An unrecognised ``--mode`` value raises ``ValueError``."""
    ns = argparse.Namespace(mode="banana", prompt=None)
    with pytest.raises(ValueError, match="Unknown weasel mode"):
        WeaselMode.from_args(ns)


# ---------------------------------------------------------------------------
# dispatch_mode routing
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_runners(monkeypatch: pytest.MonkeyPatch) -> dict[WeaselMode, list[Any]]:
    """Replace each runner with a recorder so we can assert routing.

    Returns:
        Dict mapping each :class:`WeaselMode` to a list collecting the
        ``args`` it was invoked with.
    """
    calls: dict[WeaselMode, list[Any]] = {m: [] for m in WeaselMode}

    def make_stub(mode: WeaselMode, exit_code: int) -> Any:
        def stub(args: Any) -> int:
            calls[mode].append(args)
            return exit_code
        return stub

    new_runners = {
        WeaselMode.INTERACTIVE: make_stub(WeaselMode.INTERACTIVE, 0),
        WeaselMode.PRINT: make_stub(WeaselMode.PRINT, 7),
        WeaselMode.RPC: make_stub(WeaselMode.RPC, 0),
        WeaselMode.SDK: make_stub(WeaselMode.SDK, 0),
    }
    monkeypatch.setattr(modes, "_RUNNERS", new_runners)
    return calls


def test_dispatch_routes_interactive(stub_runners: dict[WeaselMode, list[Any]]) -> None:
    ns = argparse.Namespace(mode=None, prompt=None)
    assert dispatch_mode(ns) == 0
    assert len(stub_runners[WeaselMode.INTERACTIVE]) == 1
    assert stub_runners[WeaselMode.INTERACTIVE][0] is ns


def test_dispatch_routes_print(stub_runners: dict[WeaselMode, list[Any]]) -> None:
    ns = argparse.Namespace(mode=None, prompt="hi")
    assert dispatch_mode(ns) == 7
    assert len(stub_runners[WeaselMode.PRINT]) == 1


def test_dispatch_routes_rpc(stub_runners: dict[WeaselMode, list[Any]]) -> None:
    ns = argparse.Namespace(mode="rpc", prompt=None)
    assert dispatch_mode(ns) == 0
    assert len(stub_runners[WeaselMode.RPC]) == 1


def test_dispatch_routes_sdk(stub_runners: dict[WeaselMode, list[Any]]) -> None:
    ns = argparse.Namespace(mode="sdk", prompt=None)
    assert dispatch_mode(ns) == 0
    assert len(stub_runners[WeaselMode.SDK]) == 1


def test_dispatch_unknown_mode_raises() -> None:
    """Bad ``--mode`` propagates the resolver's ``ValueError``."""
    ns = argparse.Namespace(mode="banana", prompt=None)
    with pytest.raises(ValueError):
        dispatch_mode(ns)


def test_dispatch_missing_runner_returns_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the runner table is missing an entry, we surface exit code 2."""
    monkeypatch.setattr(modes, "_RUNNERS", {})
    ns = argparse.Namespace(mode="rpc", prompt=None)
    assert dispatch_mode(ns) == 2


# ---------------------------------------------------------------------------
# Late-binding fallbacks (interactive / print runners) — no W1 yet
# ---------------------------------------------------------------------------


def test_run_interactive_delegates_to_repl_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_interactive`` calls ``chimera.weasel.repl.run`` with the args."""
    captured: dict[str, Any] = {}

    def fake_run(args: Any) -> int:
        captured["args"] = args
        return 42

    from chimera.weasel import repl as real_repl
    monkeypatch.setattr(real_repl, "run", fake_run, raising=False)
    ns = argparse.Namespace(mode=None, prompt=None)
    rc = modes._run_interactive(ns)
    assert rc == 42
    assert captured["args"] is ns


def test_run_interactive_missing_entry_point(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """If repl module loads but has no run/run_repl, exit 1 + stderr msg."""
    from chimera.weasel import repl as real_repl

    # Hide both possible entry points.
    monkeypatch.delattr(real_repl, "run", raising=False)
    monkeypatch.delattr(real_repl, "run_repl", raising=False)

    rc = modes._run_interactive(argparse.Namespace())
    assert rc == 1
    captured = capsys.readouterr()
    assert "weasel:" in captured.err


def test_run_print_delegates_to_cli_run_print_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_run_print`` prefers W1's ``_run_print_mode`` when present."""
    captured: dict[str, Any] = {}

    def fake_run(args: Any) -> int:
        captured["args"] = args
        return 5

    from chimera.weasel import cli as real_cli
    monkeypatch.setattr(real_cli, "_run_print_mode", fake_run, raising=False)
    monkeypatch.delattr(real_cli, "run_print", raising=False)

    ns = argparse.Namespace(prompt="ping", json=False)
    rc = modes._run_print(ns)
    assert rc == 5
    assert captured["args"] is ns


def test_run_print_fallback_emits_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """When W1's print runner is unavailable, the JSON fallback fires."""
    from chimera.weasel import cli as real_cli

    monkeypatch.delattr(real_cli, "run_print", raising=False)
    monkeypatch.delattr(real_cli, "_run_print_mode", raising=False)

    ns = argparse.Namespace(prompt="ping", json=True)
    rc = modes._run_print(ns)
    assert rc == 1
    captured = capsys.readouterr()
    assert '"prompt": "ping"' in captured.out
    assert '"success": false' in captured.out


def test_run_sdk_prints_hint(capsys: pytest.CaptureFixture[str]) -> None:
    """The SDK mode is a no-op that points users at the import path."""
    rc = modes._run_sdk(argparse.Namespace())
    assert rc == 0
    captured = capsys.readouterr()
    assert "from chimera.weasel.sdk import Agent" in captured.err
