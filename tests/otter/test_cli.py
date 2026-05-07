"""Smoke tests for the ``chimera otter`` CLI scaffold (agent O1).

Covers:

* ``add_arguments`` registers the documented flag surface
  (``--version``, ``--model``, ``-p/--print``, ``--output-format``,
  ``--no-color`` / ``--no-rich``, ``--allowed-tools``, ``--no-save`` /
  ``--run-id``, plus the subcommand placeholders).
* ``chimera otter --version`` exits 0 and emits a semver string.
* ``chimera otter --help`` exits 0 and lists the load-bearing flags.
* Subcommand placeholders (``serve`` / ``sessions`` / ``share`` /
  ``agents``) route through :func:`chimera.otter.cli.run` and exit 2
  (the conventional usage code for "not implemented yet" stubs).
* ``--output-format`` is validated by argparse's ``choices`` (rejects
  unknown formats with exit 2).
* The ``select_handler`` reuse contract holds: every output-format
  spelling otter accepts is also accepted by
  :func:`chimera.cli.output_format.select_handler`.

Tests stay lightweight: no live provider, no network, no real Ollama.
The one-shot ``-p`` flow needs the provider stack, which is exercised
by the broader mink/otter integration suites; here we only verify the
parser + dispatch contract.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import pytest

from chimera.otter import cli as otter_cli

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


# ---------------------------------------------------------------------------
# add_arguments: parser surface
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera otter")
    otter_cli.add_arguments(parser)
    return parser


def test_add_arguments_registers_core_flags() -> None:
    """``add_arguments`` exposes every flag the spec promises."""
    parser = _build_parser()
    # argparse stores option strings on each Action; flatten the list.
    options: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 — argparse internals are stable.
        options.update(action.option_strings)
    expected = {
        "--version",
        "--model",
        "-p",
        "--print",
        "--output-format",
        "--max-steps",
        "--cwd",
        "--allowed-tools",
        "--no-rich",
        "--no-color",
        "--no-save",
        "--run-id",
    }
    missing = expected - options
    assert not missing, f"missing flags on otter parser: {sorted(missing)}"


def test_add_arguments_default_model_uses_env_then_fallback(monkeypatch) -> None:
    """``--model`` defaults to ``$OTTER_MODEL`` then ``_DEFAULT_MODEL``."""
    monkeypatch.delenv("OTTER_MODEL", raising=False)
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == otter_cli._DEFAULT_MODEL  # noqa: SLF001 — module-level constant.

    # Now seed the env var and rebuild — argparse evaluates the default once
    # at parser construction, so we have to rebuild to observe the new env.
    monkeypatch.setenv("OTTER_MODEL", "gpt-4o")
    parser2 = _build_parser()
    args2 = parser2.parse_args([])
    assert args2.model == "gpt-4o"


def test_add_arguments_output_format_choices() -> None:
    """``--output-format`` rejects values outside the documented set."""
    parser = _build_parser()
    args = parser.parse_args(["--output-format", "json"])
    assert args.output_format == "json"
    args = parser.parse_args(["--output-format", "stream-json"])
    assert args.output_format == "stream-json"
    with pytest.raises(SystemExit):
        parser.parse_args(["--output-format", "bogus"])


def test_add_arguments_subcommand_choices() -> None:
    """The positional ``SUBCOMMAND`` slot only accepts the documented names."""
    parser = _build_parser()
    args = parser.parse_args(["sessions", "list"])
    assert args.subcommand == "sessions"
    assert args.sub_action == "list"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus-subcommand"])


def test_add_arguments_allowed_tools_default_empty() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.allowed_tools == ""


def test_add_arguments_no_save_and_run_id() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--no-save", "--run-id", "fixture-001"])
    assert args.no_save is True
    assert args.run_id == "fixture-001"


# ---------------------------------------------------------------------------
# CLI process-level: --version + --help
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )


def test_otter_version_flag_exits_zero_and_prints_semver() -> None:
    """``chimera otter --version`` exits 0 and stdout/stderr contains a semver."""
    proc = _run("otter", "--version")
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    assert "otter" in combined, combined
    assert _SEMVER_RE.search(combined), combined


def test_otter_help_lists_core_flags() -> None:
    proc = _run("otter", "--help")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for flag in ("--model", "--print", "--output-format", "--allowed-tools",
                 "--no-rich", "--no-color", "--no-save", "--run-id"):
        assert flag in out, f"missing {flag} in --help output"


# ---------------------------------------------------------------------------
# run() dispatch routing — subcommand stubs
# ---------------------------------------------------------------------------


def _ns(**overrides: object) -> argparse.Namespace:
    """Build a Namespace seeded with parser defaults plus *overrides*.

    Why: ``otter_cli.run`` reads ``getattr(args, ...)`` for every flag, so
    constructing a fresh Namespace from the parser keeps every default in
    sync with the CLI surface tests above.
    """
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_run_dispatches_serve_subcommand(monkeypatch) -> None:
    """``otter serve`` is wired by O14; smoke-test dispatch routing.

    Stub ``serve_http`` to a no-op returning 0 so we can assert dispatch
    routing reached the HTTP path without binding any port (default 5173
    can collide on shared CI runners; even port 0 risks fd churn here).
    """
    from chimera.otter import server as otter_server

    monkeypatch.setattr(otter_server, "serve_http", lambda *a, **kw: 0)
    rc = otter_cli.run(
        _ns(subcommand="serve", port=0, host="127.0.0.1", auth_token=None)
    )
    assert rc == 0


def test_run_dispatches_sessions_subcommand(capsys) -> None:
    """``otter sessions list`` is wired to O3's handler.

    The handler returns 0 on an empty result set (no eventlog dirs in
    the test environment) and prints a friendly "no sessions found"
    line to stdout. The wave-1 scaffold returned 2 with stderr text;
    this test now tracks the real-handler contract.
    """
    rc = otter_cli.run(_ns(subcommand="sessions", sub_action="list"))
    assert rc == 0
    captured = capsys.readouterr()
    # Either an empty-set message or a table header — both are acceptable
    # signals that the real handler ran.
    output = captured.out + captured.err
    assert output  # something was printed


def test_run_dispatches_share_subcommand(capsys) -> None:
    rc = otter_cli.run(_ns(subcommand="share", sub_action="run-42"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "otter share" in captured.err


def test_run_dispatches_agents_subcommand(capsys) -> None:
    """``otter agents show <unknown>`` exits 2 via the wired handler.

    Once O10's real agents handler is wired in, the stub's ``otter
    agents:`` prefix is gone — the real error text now mentions the
    unresolved name and the search paths. We keep the rc==2 contract
    and assert on the unresolved name plus a search-path fragment.
    """
    rc = otter_cli.run(_ns(subcommand="agents", sub_action="show", sub_target="reviewer"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "reviewer" in captured.err
    assert ".opencode/agent" in captured.err or "not found" in captured.err


def test_run_with_no_args_routes_to_readline_repl(monkeypatch) -> None:
    """Bare ``chimera otter`` (no -p, no subcommand) routes to the REPL.

    Wave-11 A9 replaced the legacy "REPL not yet wired" placeholder with
    a dispatch that picks the readline REPL on non-TTY stdout (the
    pytest case) and the textual TUI on a TTY when ``[tui]`` is
    available. We monkeypatch the readline sink so the test does not
    actually drop into ``input()`` waiting on stdin.
    """
    calls: list[object] = []

    def _fake_readline(args: object) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(otter_cli, "_run_readline_repl", _fake_readline)
    # Force the auto-launch probe to "no textual" so this test stays
    # deterministic regardless of whether the [tui] extra is installed
    # in the developer's environment. pytest also captures stdout so
    # ``isatty()`` is already False, but belt-and-braces.
    monkeypatch.setattr(otter_cli, "_textual_available", lambda: False)
    monkeypatch.delenv("CHIMERA_NO_TUI", raising=False)

    rc = otter_cli.run(_ns())
    assert rc == 0
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Allowed-tools filtering
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_filter_allowed_tools_passthrough_on_empty() -> None:
    tools = [_FakeTool("read"), _FakeTool("bash")]
    assert otter_cli._filter_allowed_tools(tools, "") == tools  # noqa: SLF001


def test_filter_allowed_tools_case_insensitive() -> None:
    tools = [_FakeTool("read"), _FakeTool("bash"), _FakeTool("write")]
    kept = otter_cli._filter_allowed_tools(tools, "Read,BASH")  # noqa: SLF001
    kept_names = sorted(t.name for t in kept)
    assert kept_names == ["bash", "read"]


def test_filter_allowed_tools_rejects_unknown() -> None:
    tools = [_FakeTool("read")]
    with pytest.raises(otter_cli._UnknownAllowedTool):  # noqa: SLF001
        otter_cli._filter_allowed_tools(tools, "read,nope")  # noqa: SLF001


# ---------------------------------------------------------------------------
# Output-format reuse contract
# ---------------------------------------------------------------------------


def test_output_format_choices_match_select_handler() -> None:
    """Every otter output format must be accepted by ``select_handler``.

    Otter's ``--output-format`` choices are a strict subset of what
    :func:`chimera.cli.output_format.select_handler` knows, so we
    exercise the contract directly.
    """
    from chimera.cli.output_format import select_handler

    for fmt in otter_cli._VALID_OUTPUT_FORMATS:  # noqa: SLF001
        handler = select_handler(fmt)
        assert handler is not None, f"select_handler returned None for {fmt!r}"


# ---------------------------------------------------------------------------
# Run id helpers
# ---------------------------------------------------------------------------


def test_make_run_id_starts_with_otter_prefix() -> None:
    rid = otter_cli._make_run_id()  # noqa: SLF001
    assert rid.startswith("otter-")
    # Shape: ``otter-<YYYYMMDDTHHMMSS>-<8 hex chars>`` where the timestamp
    # block is 15 chars (8 date + ``T`` + 6 time). Total = 6 + 15 + 1 + 8 = 30.
    assert len(rid) == 6 + 15 + 1 + 8, rid
    parts = rid.split("-")
    assert len(parts) == 3, parts
    assert parts[0] == "otter"
    assert len(parts[1]) == 15
    assert len(parts[2]) == 8
