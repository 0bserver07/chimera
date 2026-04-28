"""Tests for tab-completion of user-defined custom slash commands (F7).

W4 wired ``.opencode/command/*.md`` into the otter slash registry, but the
shared :mod:`chimera.cli.slash_commands` exposed a static ``COMMAND_NAMES``
snapshot taken at import time — so any command registered after import was
invisible to readline tab completion. F7 closes that gap.

This suite covers four flavors of refresh:

1. **register() refresh.** Calling :func:`chimera.cli.slash_commands.register`
   directly mutates :data:`COMMAND_NAMES` in place so import-once consumers
   see the new entry.
2. **register_custom_commands() refresh.** Installing a
   :class:`~chimera.otter.commands.CustomCommand` on the shared registry
   bumps the same list and calls back into readline (when available).
3. **_complete_command dynamic lookup.** The completer in
   :mod:`chimera.cli.code` reads names live, so customs surface in the
   match set even when readline was set up before the customs landed.
4. **readline rebind.** When a completer is currently bound,
   :func:`register_custom_commands` re-installs it so readline drops any
   cached state (display columns, last-match list).
"""
from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

from chimera.cli import slash_commands as _shared_slash
from chimera.cli.code import _complete_command, _command_names
from chimera.otter.commands import CustomCommand, CustomCommandArg
from chimera.otter.slash import register_custom_commands


# ---------------------------------------------------------------------------
# Fixture: snapshot + restore the shared registry around each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_registry() -> Any:
    """Snapshot ``_REGISTRY`` and ``COMMAND_NAMES`` so tests don't bleed."""
    saved_registry = dict(_shared_slash._REGISTRY)
    saved_names = list(_shared_slash.COMMAND_NAMES)
    try:
        yield
    finally:
        _shared_slash._REGISTRY.clear()
        _shared_slash._REGISTRY.update(saved_registry)
        _shared_slash.COMMAND_NAMES[:] = saved_names


def _make_command(name: str, *, body: str = "rendered body") -> CustomCommand:
    """Build a minimal :class:`CustomCommand` with one positional arg."""
    return CustomCommand(
        name=name,
        description=f"user command: /{name}",
        args=[CustomCommandArg(name="target")],
        body_template=body,
        source=f"/fake/.opencode/command/{name}.md",
    )


# ---------------------------------------------------------------------------
# 1. register() keeps COMMAND_NAMES in sync
# ---------------------------------------------------------------------------

def test_register_refreshes_command_names_in_place() -> None:
    """``register(...)`` must mutate the existing list, not rebind it."""
    list_id_before = id(_shared_slash.COMMAND_NAMES)
    assert "/zonk" not in _shared_slash.COMMAND_NAMES
    _shared_slash.register("zonk", lambda s, e, a, o: None, "zap")
    assert "/zonk" in _shared_slash.COMMAND_NAMES
    # Same object identity — important for ``from ... import COMMAND_NAMES``
    # consumers that hold a reference to the list rather than re-importing.
    assert id(_shared_slash.COMMAND_NAMES) == list_id_before


def test_refresh_command_names_returns_sorted_view() -> None:
    """The public refresh hook returns a fresh sorted ``/name`` list."""
    _shared_slash.register("alpha", lambda s, e, a, o: None)
    _shared_slash.register("beta", lambda s, e, a, o: None)
    refreshed = _shared_slash.refresh_command_names()
    assert "/alpha" in refreshed
    assert "/beta" in refreshed
    # Sorted property — ``/alpha`` precedes ``/beta`` lexicographically.
    assert refreshed.index("/alpha") < refreshed.index("/beta")


# ---------------------------------------------------------------------------
# 2. register_custom_commands refreshes the completion list
# ---------------------------------------------------------------------------

def test_register_custom_commands_updates_command_names() -> None:
    """Customs land on the shared registry **and** in COMMAND_NAMES."""
    customs = [_make_command("review"), _make_command("ship")]
    installed = register_custom_commands(_shared_slash, customs)
    assert installed == 2
    assert "/review" in _shared_slash.COMMAND_NAMES
    assert "/ship" in _shared_slash.COMMAND_NAMES


def test_register_custom_commands_empty_is_noop() -> None:
    """An empty list is a no-op — no refresh, no readline call."""
    snapshot = list(_shared_slash.COMMAND_NAMES)
    installed = register_custom_commands(_shared_slash, [])
    assert installed == 0
    assert _shared_slash.COMMAND_NAMES == snapshot


# ---------------------------------------------------------------------------
# 3. _complete_command sees customs registered after readline setup
# ---------------------------------------------------------------------------

def test_complete_command_includes_custom_after_registration() -> None:
    """The completer pulls names live, so customs surface immediately."""
    # Use a unique prefix that doesn't collide with built-ins like /branch.
    register_custom_commands(_shared_slash, [_make_command("zonkers")])
    # ``state=0`` triggers the match-collection branch.
    first = _complete_command("/zonk", 0)
    assert first == "/zonkers"
    # No further matches for that prefix.
    assert _complete_command("/zonk", 1) is None


def test_complete_command_falls_back_to_static_on_error() -> None:
    """When ``_command_names`` raises, completion uses the static list."""
    sentinel: list[str] = []

    def _boom() -> list[str]:
        sentinel.append("called")
        raise RuntimeError("registry unreadable")

    with patch("chimera.cli.code._command_names", side_effect=_boom):
        # ``/help`` is in the legacy static fallback list.
        result = _complete_command("/he", 0)
        assert result == "/help"
        assert sentinel == ["called"]


def test_command_names_reflects_custom_registrations() -> None:
    """``_command_names()`` returns the union of legacy + shared registry."""
    register_custom_commands(_shared_slash, [_make_command("changelog")])
    names = _command_names()
    assert "/changelog" in names


# ---------------------------------------------------------------------------
# 4. readline rebind on registration
# ---------------------------------------------------------------------------

class _FakeReadline:
    """Minimal :mod:`readline` stand-in with completer get/set tracking."""

    def __init__(self) -> None:
        self._completer: Any = None
        self.set_calls: int = 0
        self.display_matches: list[str] = []

    # readline-compatible API used by ``_refresh_completion``.
    def get_completer(self) -> Any:
        return self._completer

    def set_completer(self, completer: Any) -> None:
        self._completer = completer
        self.set_calls += 1

    # Helper used by the test harness.
    def cycle_matches(self, text: str) -> list[str]:
        """Drive the bound completer until it returns ``None``."""
        if self._completer is None:
            return []
        out: list[str] = []
        idx = 0
        while True:
            match = self._completer(text, idx)
            if match is None:
                break
            out.append(match)
            idx += 1
        self.display_matches = out
        return out


def test_register_custom_commands_rebinds_readline_completer() -> None:
    """An already-bound completer is re-installed so readline drops cache."""
    fake = _FakeReadline()
    fake.set_completer(_complete_command)
    initial_calls = fake.set_calls

    with patch.dict(sys.modules, {"readline": fake}):
        register_custom_commands(_shared_slash, [_make_command("deploy")])

    assert fake.set_calls == initial_calls + 1
    assert fake.get_completer() is _complete_command


def test_register_custom_commands_no_readline_no_crash() -> None:
    """When readline import fails the registration still succeeds."""
    # Inject a fake module entry that raises on import-resolution. We do
    # this by stubbing ``__import__`` to fail for ``readline``.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__  # type: ignore[index]

    def _blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "readline":
            raise ImportError("readline unavailable on this platform")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_blocked):
        installed = register_custom_commands(
            _shared_slash, [_make_command("noreadline")],
        )
    assert installed == 1
    assert "/noreadline" in _shared_slash.COMMAND_NAMES


def test_register_custom_commands_no_completer_bound() -> None:
    """When ``readline.get_completer`` returns ``None`` no rebind happens."""
    fake = _FakeReadline()
    # No prior set_completer call — completer stays ``None``.
    initial_calls = fake.set_calls
    with patch.dict(sys.modules, {"readline": fake}):
        register_custom_commands(_shared_slash, [_make_command("idle")])
    # No rebind because there was no completer to refresh.
    assert fake.set_calls == initial_calls


def test_display_matches_reflects_custom_after_readline_rebind() -> None:
    """After registration + rebind, cycling readline returns ``/custom``."""
    fake = _FakeReadline()
    fake.set_completer(_complete_command)

    with patch.dict(sys.modules, {"readline": fake}):
        register_custom_commands(
            _shared_slash, [_make_command("walkthrough")],
        )

    matches = fake.cycle_matches("/walk")
    assert matches == ["/walkthrough"]
    assert fake.display_matches == ["/walkthrough"]


def test_register_custom_commands_swallows_get_completer_failure() -> None:
    """Broken ``get_completer`` shims must not propagate."""

    class _BrokenReadline:
        def get_completer(self) -> Any:
            raise RuntimeError("shim missing internal state")

        def set_completer(self, _completer: Any) -> None:  # pragma: no cover
            raise AssertionError("set_completer must not be reached")

    broken = _BrokenReadline()
    with patch.dict(sys.modules, {"readline": broken}):
        installed = register_custom_commands(
            _shared_slash, [_make_command("brittle")],
        )
    assert installed == 1
    assert "/brittle" in _shared_slash.COMMAND_NAMES


def test_register_custom_commands_swallows_set_completer_failure() -> None:
    """Broken ``set_completer`` shims must not propagate either."""

    class _SetCompleterRaises:
        def __init__(self) -> None:
            self._completer: Any = _complete_command

        def get_completer(self) -> Any:
            return self._completer

        def set_completer(self, _completer: Any) -> None:
            raise RuntimeError("readline died on rebind")

    broken = _SetCompleterRaises()
    with patch.dict(sys.modules, {"readline": broken}):
        installed = register_custom_commands(
            _shared_slash, [_make_command("flaky")],
        )
    assert installed == 1


# ---------------------------------------------------------------------------
# 5. Sanity: registry state object without refresh_command_names is fine
# ---------------------------------------------------------------------------

def test_register_custom_commands_handles_dict_state_without_refresh() -> None:
    """Dict-style fakes without ``refresh_command_names`` still register."""

    class _DictState:
        def __init__(self) -> None:
            self.commands: dict[str, Any] = {}

    state = _DictState()
    fake_readline = types.SimpleNamespace(
        get_completer=lambda: None,
        set_completer=lambda _c: None,
    )
    with patch.dict(sys.modules, {"readline": fake_readline}):
        installed = register_custom_commands(state, [_make_command("dict_cmd")])
    assert installed == 1
    assert "dict_cmd" in state.commands
    # Shared registry untouched (the dict state isn't the shared module).
    assert "/dict_cmd" not in _shared_slash.COMMAND_NAMES
