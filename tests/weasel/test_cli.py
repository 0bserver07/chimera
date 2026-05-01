"""Smoke tests for the ``chimera weasel`` CLI scaffold (agent W1).

Covers:

* ``add_arguments`` registers the documented minimal flag surface
  (``--version``, ``--mode``, ``--model``, ``-p/--print``, ``--json``,
  ``--list-models``, ``--cwd``, ``--max-steps``, plus the ``sessions``
  subcommand placeholder).
* ``chimera weasel --version`` exits 0 and emits ``chimera weasel
  0.5.0``.
* ``--mode`` is validated by argparse's ``choices``.
* The ``sessions`` subcommand placeholder routes through
  :func:`chimera.weasel.cli.run` to the W1 list/show handler.
* ``--list-models`` short-circuits and prints model identifiers from
  :data:`chimera.providers.cost.PRICING`.
* ``--mode rpc`` / ``--mode sdk`` placeholders return distinct exit codes
  (2 and 0 respectively) without crashing.
* ``--mode print`` without ``-p`` is a usage error (exit 2).

Tests stay lightweight: no live provider, no network. The one-shot
``-p`` flow needs the provider stack which is exercised by the broader
mink/otter integration suites.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys

import pytest

from chimera.weasel import cli as weasel_cli

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


# ---------------------------------------------------------------------------
# add_arguments: parser surface
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera weasel")
    weasel_cli.add_arguments(parser)
    return parser


def test_add_arguments_registers_core_flags() -> None:
    """``add_arguments`` exposes every flag the spec promises."""
    parser = _build_parser()
    options: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 — argparse internals are stable.
        options.update(action.option_strings)
    expected = {
        "--version",
        "--mode",
        "--model",
        "-p",
        "--print",
        "--json",
        "--list-models",
        "--cwd",
        "--max-steps",
    }
    missing = expected - options
    assert not missing, f"missing flags on weasel parser: {sorted(missing)}"


def test_add_arguments_default_model_uses_env_then_fallback(monkeypatch) -> None:
    """``--model`` defaults to ``$WEASEL_MODEL`` then ``_DEFAULT_MODEL``."""
    monkeypatch.delenv("WEASEL_MODEL", raising=False)
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == weasel_cli._DEFAULT_MODEL  # noqa: SLF001
    monkeypatch.setenv("WEASEL_MODEL", "gpt-4o")
    parser2 = _build_parser()
    args2 = parser2.parse_args([])
    assert args2.model == "gpt-4o"


def test_add_arguments_mode_choices() -> None:
    """``--mode`` rejects values outside the documented set."""
    parser = _build_parser()
    args = parser.parse_args(["--mode", "interactive"])
    assert args.mode == "interactive"
    args = parser.parse_args(["--mode", "rpc"])
    assert args.mode == "rpc"
    args = parser.parse_args(["--mode", "print"])
    assert args.mode == "print"
    args = parser.parse_args(["--mode", "sdk"])
    assert args.mode == "sdk"
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "bogus"])


def test_add_arguments_default_mode_is_interactive() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.mode == "interactive"


def test_add_arguments_subcommand_choices() -> None:
    """The positional ``SUBCOMMAND`` slot only accepts the documented names."""
    parser = _build_parser()
    args = parser.parse_args(["sessions", "list"])
    assert args.subcommand == "sessions"
    assert args.sub_action == "list"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus-subcommand"])


def test_add_arguments_sub_action_is_free_form() -> None:
    """``sub_action`` no longer enforces choices at parse time.

    ``share <session-id>`` puts an arbitrary id in slot 2, so we let
    the dispatcher (:func:`chimera.weasel.sessions.dispatch_sessions`)
    reject unknown actions instead of argparse. This test pins that
    contract: the parser accepts ``"sessions delete"`` and the bogus
    action surfaces only when dispatched.
    """
    parser = _build_parser()
    args = parser.parse_args(["sessions", "delete"])
    assert args.subcommand == "sessions"
    assert args.sub_action == "delete"


def test_add_arguments_json_default_false() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.json_output is False
    args = parser.parse_args(["--json"])
    assert args.json_output is True


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_flag_prints_zero_five_zero() -> None:
    """``chimera weasel --version`` prints ``chimera weasel 0.5.0``."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera", "weasel", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "chimera weasel" in out
    assert "0.5.0" in out
    assert _SEMVER_RE.search(out)


def test_resolve_version_string() -> None:
    """The internal helper returns the minted semver."""
    assert weasel_cli._resolve_version() == "0.5.0"  # noqa: SLF001


# ---------------------------------------------------------------------------
# --list-models
# ---------------------------------------------------------------------------


def test_list_models_prints_known_models(capsys) -> None:
    """``--list-models`` short-circuits to the cost.PRICING registry."""
    args = argparse.Namespace(
        list_models=True,
        subcommand=None,
        print_mode=None,
        mode="interactive",
    )
    rc = weasel_cli.run(args)
    captured = capsys.readouterr()
    assert rc == 0
    # PRICING ships at least one Anthropic + one OpenAI entry.
    assert "claude-sonnet-4" in captured.out
    assert "gpt-4o" in captured.out


# ---------------------------------------------------------------------------
# Mode dispatch — RPC / SDK / print-without-prompt placeholders
# ---------------------------------------------------------------------------


def test_rpc_mode_dispatches_to_run_rpc_server(monkeypatch) -> None:
    """``--mode rpc`` forwards to :func:`chimera.weasel.rpc.run_rpc_server`."""
    captured: dict[str, object] = {}

    def _fake_rpc(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr("chimera.weasel.rpc.run_rpc_server", _fake_rpc)
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        print_mode=None,
        mode="rpc",
    )
    rc = weasel_cli.run(args)
    assert rc == 0
    assert captured["args"] is args


def test_sdk_mode_returns_zero() -> None:
    """``--mode sdk`` prints the embedding pointer and exits 0."""
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        print_mode=None,
        mode="sdk",
    )
    rc = weasel_cli.run(args)
    assert rc == 0


def test_print_mode_without_prompt_is_usage_error() -> None:
    """``--mode print`` without ``-p`` is an explicit usage error."""
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        print_mode=None,
        mode="print",
    )
    rc = weasel_cli.run(args)
    assert rc == 2


# ---------------------------------------------------------------------------
# Sessions subcommand routing
# ---------------------------------------------------------------------------


def test_sessions_subcommand_routes_to_handler(monkeypatch) -> None:
    """``run`` dispatches ``sessions`` to the W1 sessions handler."""
    captured: dict[str, object] = {}

    def _fake_dispatch(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(
        "chimera.weasel.sessions.dispatch_sessions", _fake_dispatch,
    )
    args = argparse.Namespace(
        list_models=False,
        subcommand="sessions",
        sub_action="list",
        sub_target=None,
        json_output=False,
        print_mode=None,
        mode="interactive",
    )
    rc = weasel_cli.run(args)
    assert rc == 0
    assert captured["args"] is args


# ---------------------------------------------------------------------------
# Print path: ``-p`` short-circuit takes priority over --mode
# ---------------------------------------------------------------------------


def test_print_p_priority(monkeypatch) -> None:
    """``-p PROMPT`` triggers ``_run_print_mode`` regardless of --mode."""
    called: dict[str, object] = {}

    def _fake_print(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(weasel_cli, "_run_print_mode", _fake_print)
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        sub_action=None,
        sub_target=None,
        print_mode="hello",
        mode="interactive",
    )
    rc = weasel_cli.run(args)
    assert rc == 0
    assert called["args"] is args


def test_default_routes_to_repl(monkeypatch) -> None:
    """No -p / no subcommand / mode=interactive lands on the REPL."""
    called: dict[str, object] = {}

    def _fake_repl(args):
        called["args"] = args
        return 0

    monkeypatch.setattr("chimera.weasel.repl.run", _fake_repl)
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        sub_action=None,
        sub_target=None,
        print_mode=None,
        mode="interactive",
        cwd=None,
        model="claude-sonnet-4-6",
        max_steps=50,
    )
    rc = weasel_cli.run(args)
    assert rc == 0
    assert called["args"] is args


# ---------------------------------------------------------------------------
# Live --version parity (for the via-subprocess path)
# ---------------------------------------------------------------------------


def test_help_lists_minimal_surface() -> None:
    """``--help`` exits 0 and lists load-bearing weasel flags."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera", "weasel", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for needle in ("--mode", "--model", "--print", "--json", "--list-models"):
        assert needle in out, f"missing {needle!r} in --help output"


def test_run_list_models_helper_output(monkeypatch, capsys) -> None:
    """``_run_list_models`` writes one model per line in sorted order."""
    monkeypatch.setattr(
        "chimera.providers.cost.PRICING",
        {"alpha-1": (1.0, 1.0), "beta-2": (2.0, 2.0)},
    )
    rc = weasel_cli._run_list_models()  # noqa: SLF001
    captured = capsys.readouterr()
    assert rc == 0
    lines = [line for line in captured.out.splitlines() if line]
    assert lines == ["alpha-1", "beta-2"]


# ---------------------------------------------------------------------------
# Extension wiring (W3 -> _run_print_mode integration)
# ---------------------------------------------------------------------------


def test_activate_extensions_returns_empty_when_no_dot_weasel(tmp_path) -> None:
    """No project ``.weasel/extensions/`` => empty tools and hooks."""
    tools, hooks = weasel_cli._activate_extensions(str(tmp_path))  # noqa: SLF001
    assert tools == []
    assert hooks == []


def test_activate_extensions_collects_tool_from_project_extension(tmp_path) -> None:
    """A project-scope extension contributes its tools through activation."""
    import json

    ext_root = tmp_path / ".weasel" / "extensions" / "demo"
    ext_root.mkdir(parents=True)
    (ext_root / "manifest.json").write_text(
        json.dumps({"name": "demo", "version": "0.0.1", "main": "ext.py"}),
    )
    # A tiny BaseTool subclass; the loader recognises module-level ``TOOLS``.
    (ext_root / "ext.py").write_text(
        "from chimera.core.tool import BaseTool\n"
        "class _T(BaseTool):\n"
        "    name = 'demo_tool'\n"
        "    description = 'demo'\n"
        "    parameters = {'type': 'object', 'properties': {}, 'required': []}\n"
        "    def execute(self, **kwargs):\n"
        "        return 'ok'\n"
        "TOOLS = [_T()]\n",
    )
    tools, hooks = weasel_cli._activate_extensions(str(tmp_path))  # noqa: SLF001
    assert [t.name for t in tools] == ["demo_tool"]
    assert hooks == []


def test_activate_extensions_collects_hooks(tmp_path) -> None:
    """A manifest-declared hook lands in the hooks list."""
    import json

    ext_root = tmp_path / ".weasel" / "extensions" / "hooks-demo"
    ext_root.mkdir(parents=True)
    (ext_root / "manifest.json").write_text(
        json.dumps({
            "name": "hooks-demo",
            "version": "0.0.1",
            "hooks": [
                {"command": "echo pre", "event_type": "PreToolUse"},
            ],
        }),
    )
    tools, hooks = weasel_cli._activate_extensions(str(tmp_path))  # noqa: SLF001
    assert tools == []
    assert len(hooks) == 1
    assert hooks[0].command == "echo pre"
    assert hooks[0].event_type == "PreToolUse"


def test_activate_extensions_swallows_discovery_errors(monkeypatch, tmp_path, capsys) -> None:
    """A loader exception is logged to stderr; the print path keeps going."""
    def _boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr(
        "chimera.weasel.extensions.load_weasel_extensions", _boom,
    )
    tools, hooks = weasel_cli._activate_extensions(str(tmp_path))  # noqa: SLF001
    captured = capsys.readouterr()
    assert tools == []
    assert hooks == []
    assert "extension discovery failed" in captured.err


def test_run_list_models_handles_import_failure(monkeypatch, capsys) -> None:
    """Missing ``cost`` module surfaces a stderr message and exits 1."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _bad_import(name, *a, **kw):
        if name == "chimera.providers.cost":
            raise RuntimeError("boom")
        return real_import(name, *a, **kw)

    monkeypatch.setitem(sys.modules, "chimera.providers.cost", None)

    # Force a fresh import error by deleting the module reference.
    sys.modules.pop("chimera.providers.cost", None)
    monkeypatch.setattr("builtins.__import__", _bad_import)
    buf = io.StringIO()
    monkeypatch.setattr("sys.stderr", buf)
    rc = weasel_cli._run_list_models()  # noqa: SLF001
    monkeypatch.setattr("sys.stderr", sys.__stderr__)
    assert rc == 1
    assert "could not load model registry" in buf.getvalue()
