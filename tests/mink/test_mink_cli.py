"""Smoke tests for the ``chimera mink`` CLI and slash-command registry.

Covers:
* ``chimera cc --help`` exits 0 and advertises ``--model``,
  ``--permission-mode``, ``--print``.
* ``chimera cc -p ...`` one-shot mode against a live Ollama (skipped
  unless ``OLLAMA_HOST`` is set and reachable).
* Each command in :func:`chimera.cli.slash_commands.list_commands`
  is invoked through a stub :class:`Session` to confirm it dispatches
  without raising and that :func:`dispatch` returns ``True``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chimera.cli import slash_commands

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ollama_reachable() -> bool:
    host = os.environ.get("OLLAMA_HOST")
    if not host:
        return False
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _make_stub_session(tmp_path) -> SimpleNamespace:
    """Build a session-shaped stub safe for every registered handler.

    The handlers introspect a small surface (``provider``, ``tools``,
    ``context``, ``cost_tracker``, ``debug``, ``_yolo_mode``,
    ``clear()``, ``compact()``, ``save()``, ``fork()``,
    ``iter_chat()``, ``switch_branch()``). We use ``MagicMock`` so any
    additional attribute access returns a MagicMock (truthy, callable,
    iterable on demand) instead of raising.
    """
    provider = MagicMock()
    provider.model_name = "stub-model"

    # Use SimpleNamespace for the cost tracker so f-string formatting works
    # on real floats (MagicMock attrs blow up under ``{:.4f}``).
    cost_tracker = SimpleNamespace(
        total=0.0,
        remaining=None,
        breakdown=lambda: {},
        totals_by_model={},
        events=[],
        per_step=[],
    )

    context = MagicMock()
    context.to_messages.return_value = []
    context.size_chars.return_value = 0

    tool = MagicMock()
    tool.name = "stub_tool"
    tool.description = "stub"

    session = MagicMock()
    session.provider = provider
    session.cost_tracker = cost_tracker
    session.context = context
    session.tools = [tool]
    session.debug = False
    session._yolo_mode = False
    session._model_list = ["stub-model"]
    session._model_index = 0
    session.id = "stub-session"
    session.session_id = "stub-session"

    # Methods that handlers occasionally invoke.
    session.clear = MagicMock(return_value=None)
    session.compact = MagicMock(return_value=None)
    session.save = MagicMock(return_value=None)
    session.fork = MagicMock(return_value=None)
    session.switch_branch = MagicMock(return_value=None)
    session.iter_chat = MagicMock(return_value=iter([]))

    return session


# Handlers that need network/Ollama/git/subprocess and should be
# tested via the dispatch surface but not for behaviour.
_NEEDS_NETWORK = {"doctor", "review", "subagent"}


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_mink_help_runs():
    """``python -m chimera.cli.main mink --help`` exits 0 and lists key flags."""
    result = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "mink", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "--model" in out
    assert "--permission-mode" in out
    assert "--print" in out


@pytest.mark.skipif(not _ollama_reachable(), reason="OLLAMA_HOST unset or unreachable")
def test_mink_print_mode_smoke():
    """One-shot ``chimera mink -p`` returns within 60s and prints non-empty stdout."""
    model = os.environ.get("CHIMERA_MINK_MODEL") or os.environ.get(
        "CHIMERA_CC_MODEL", "kimi-k2.6:cloud"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chimera.cli.main",
            "mink",
            "-p",
            "say hello",
            "--model",
            model,
            "--permission-mode",
            "bypassPermissions",
            "--max-steps",
            "2",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "expected non-empty stdout"


# ---------------------------------------------------------------------------
# Slash command dispatch tests (parametrised over the registry)
# ---------------------------------------------------------------------------


_REGISTERED = [name for name, _ in slash_commands.list_commands()]
assert _REGISTERED, "slash_commands registry is empty"


_COMMAND_ARGS = {
    "subagent": "nonexistent please",
    "plugin": "list",
    "branch": "0 stub",
    "switch": "stub-leaf",
    "session": "list",
    "checkpoint": "list",
    "resume": "abc123",
}


def _command_args(name: str) -> str:
    """Return arg string that exercises the handler without side effects."""
    return _COMMAND_ARGS.get(name, "")


@pytest.mark.parametrize("name", _REGISTERED, ids=_REGISTERED)
def test_slash_command_dispatch(name, tmp_path, monkeypatch):
    """Every slash command dispatches without raising and returns True."""
    if name in {"exit", "quit"}:
        # cmd_exit raises SystemExit by design — handle separately.
        session = _make_stub_session(tmp_path)
        env = SimpleNamespace(workdir=str(tmp_path))
        out_lines: list[str] = []
        with pytest.raises(SystemExit):
            slash_commands.dispatch(f"/{name}", session, env, out_lines.append)
        return

    # Force /doctor and similar handlers to take the offline / not-available
    # branch by pointing OLLAMA_HOST at a closed port and HOME at tmp_path.
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    monkeypatch.setenv("HOME", str(tmp_path))
    # /init expects drain_steps; stub it out so we don't drive a real loop.
    monkeypatch.setattr(
        "chimera.cli.code.drain_steps",
        lambda _it: SimpleNamespace(cost=0.0, steps=0),
        raising=False,
    )

    session = _make_stub_session(tmp_path)
    env = SimpleNamespace(workdir=str(tmp_path))
    out_lines: list[str] = []

    handled = slash_commands.dispatch(
        f"/{name} {_command_args(name)}".rstrip(),
        session,
        env,
        out_lines.append,
    )

    assert handled is True, f"/{name} should be handled"
    # Output is best-effort — many handlers exit silently when their
    # underlying subsystem is unavailable. The contract under test is
    # "dispatch returns True without raising".


def test_dispatch_unknown_command_returns_false():
    session = _make_stub_session(None)
    out_lines: list[str] = []
    handled = slash_commands.dispatch(
        "/definitely-not-a-command", session, None, out_lines.append
    )
    assert handled is False
    assert any("Unknown command" in line for line in out_lines)


def test_dispatch_non_slash_returns_false():
    session = _make_stub_session(None)
    handled = slash_commands.dispatch("hello world", session, None, lambda _m: None)
    assert handled is False


def test_command_names_in_sync():
    names_from_list = {name for name, _ in slash_commands.list_commands()}
    names_from_export = {n.lstrip("/") for n in slash_commands.COMMAND_NAMES}
    assert names_from_list == names_from_export
