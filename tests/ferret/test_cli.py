"""Smoke tests for the ``chimera ferret`` CLI scaffold (agent FF1).

Covers:

* ``add_arguments`` registers the documented flag surface
  (``--version``, ``--model``, ``-p/--print``, ``--output-format``,
  ``--no-color`` / ``--no-rich``, ``--allowed-tools``, ``--no-save`` /
  ``--run-id``, ``--sandbox``, ``--approval``, ``--config``, ``--http``,
  plus the subcommand placeholders).
* ``chimera ferret --version`` exits 0 and emits the expected version.
* ``chimera ferret --help`` exits 0 and lists the load-bearing flags.
* Subcommand placeholders (``serve`` / ``share`` / ``agents`` /
  ``bench``) route through :func:`chimera.ferret.cli.run` and exit 2.
* ``--output-format`` is validated by argparse's ``choices`` (rejects
  unknown formats with exit 2).
* ``--sandbox`` and ``--approval`` are restricted to the documented
  enum values.

Tests stay lightweight: no live provider, no network. The one-shot
``-p`` flow needs the provider stack and is exercised separately by
the broader otter integration suite — here we only verify the parser
+ dispatch contract.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import pytest

from chimera.ferret import cli as ferret_cli

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


# ---------------------------------------------------------------------------
# add_arguments: parser surface
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera ferret")
    ferret_cli.add_arguments(parser)
    return parser


def test_add_arguments_registers_core_flags() -> None:
    """``add_arguments`` exposes every flag the spec promises."""
    parser = _build_parser()
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
        "--sandbox",
        "--approval",
        "--config",
        "--http",
        "--host",
        "--port",
        "--auth-token",
    }
    missing = expected - options
    assert not missing, f"missing flags on ferret parser: {sorted(missing)}"


def test_add_arguments_default_model_uses_env_then_fallback(monkeypatch) -> None:
    """``--model`` defaults to ``$FERRET_MODEL`` then ``_DEFAULT_MODEL``."""
    monkeypatch.delenv("FERRET_MODEL", raising=False)
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == ferret_cli._DEFAULT_MODEL  # noqa: SLF001

    monkeypatch.setenv("FERRET_MODEL", "gpt-4o")
    parser2 = _build_parser()
    args2 = parser2.parse_args([])
    assert args2.model == "gpt-4o"


def test_default_model_is_gpt5() -> None:
    """The OpenAI-flagship default is ``gpt-5`` per the SPEC chain."""
    assert ferret_cli._DEFAULT_MODEL == "gpt-5"  # noqa: SLF001


def test_add_arguments_output_format_choices() -> None:
    """``--output-format`` rejects values outside the documented set."""
    parser = _build_parser()
    args = parser.parse_args(["--output-format", "json"])
    assert args.output_format == "json"
    args = parser.parse_args(["--output-format", "stream-json"])
    assert args.output_format == "stream-json"
    with pytest.raises(SystemExit):
        parser.parse_args(["--output-format", "bogus"])


def test_add_arguments_sandbox_choices() -> None:
    """``--sandbox`` accepts only the three documented modes."""
    parser = _build_parser()
    # Default
    args = parser.parse_args([])
    assert args.sandbox == "read-only"
    # Each valid mode parses
    for mode in ("read-only", "workspace-write", "workspace-write-network"):
        args = parser.parse_args(["--sandbox", mode])
        assert args.sandbox == mode
    with pytest.raises(SystemExit):
        parser.parse_args(["--sandbox", "bogus"])


def test_add_arguments_approval_choices() -> None:
    """``--approval`` accepts only ``read-only``, ``auto``, ``full``."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.approval == "read-only"
    for preset in ("read-only", "auto", "full"):
        args = parser.parse_args(["--approval", preset])
        assert args.approval == preset
    with pytest.raises(SystemExit):
        parser.parse_args(["--approval", "yolo"])


def test_add_arguments_subcommand_choices() -> None:
    """The positional ``SUBCOMMAND`` slot only accepts documented names."""
    parser = _build_parser()
    args = parser.parse_args(["sessions", "list"])
    assert args.subcommand == "sessions"
    assert args.sub_action == "list"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus-subcommand"])


def test_add_arguments_http_default_false() -> None:
    """``--http`` defaults to False; ACP is the IDE-first default."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.http is False
    args = parser.parse_args(["serve", "--http"])
    assert args.http is True


def test_add_arguments_config_path_default_none() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.config_path is None
    args = parser.parse_args(["--config", "/tmp/foo.toml"])
    assert args.config_path == "/tmp/foo.toml"


def test_add_arguments_no_save_and_run_id() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--no-save", "--run-id", "fixture-001"])
    assert args.no_save is True
    assert args.run_id == "fixture-001"


def test_add_arguments_allowed_tools_default_empty() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.allowed_tools == ""


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


def test_ferret_version_flag_prints_expected() -> None:
    """``chimera ferret --version`` exits 0 and prints ``chimera ferret 0.5.0``."""
    proc = _run("ferret", "--version")
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    assert "ferret" in combined, combined
    assert _SEMVER_RE.search(combined), combined
    # The exact contract from the SPEC.
    assert "chimera ferret 0.5.0" in combined, combined


def test_ferret_help_lists_core_flags() -> None:
    proc = _run("ferret", "--help")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for flag in (
        "--model",
        "--print",
        "--output-format",
        "--allowed-tools",
        "--no-rich",
        "--no-color",
        "--no-save",
        "--run-id",
        "--sandbox",
        "--approval",
        "--config",
    ):
        assert flag in out, f"missing {flag} in --help output"


# ---------------------------------------------------------------------------
# run() dispatch routing — subcommand stubs
# ---------------------------------------------------------------------------


def _ns(**overrides: object) -> argparse.Namespace:
    """Build a Namespace seeded with parser defaults plus *overrides*."""
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_run_dispatches_serve_subcommand_acp_default(capsys, monkeypatch) -> None:
    """``ferret serve`` (no ``--http``) routes through the IDE-first ACP path.

    Wave-6 wires :func:`chimera.ferret.ide.maybe_serve_ide_acp` into
    ``_dispatch_serve``; we patch the helper to return a sentinel rc so
    the test asserts the wiring without actually running the JSON-RPC
    loop on stdin/stdout.
    """
    from chimera.ferret import cli as _cli

    captured_args: list = []

    def _fake(args):
        captured_args.append(args)
        return 7

    monkeypatch.setattr(
        "chimera.ferret.ide.maybe_serve_ide_acp", _fake, raising=False
    )
    rc = _cli.run(_ns(subcommand="serve"))
    assert rc == 7
    assert captured_args, "maybe_serve_ide_acp was not called"


def test_run_dispatches_serve_subcommand_http_when_flag_set(
    capsys, monkeypatch
) -> None:
    """``ferret serve --http`` routes through the HTTP server.

    F1/W8 wires the HTTP transport: the dispatch helper delegates to
    :func:`chimera.otter.server.serve_http` with a ferret-built
    ``agent_factory``. We stub the otter entry point to a sentinel so
    the test asserts the wiring without binding any port.
    """
    captured_kwargs: list = []

    def _fake_serve(*_a, **kw):
        captured_kwargs.append(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve, raising=True
    )
    rc = ferret_cli.run(_ns(subcommand="serve", http=True))
    assert rc == 0
    assert captured_kwargs, "serve_http was not invoked"
    captured = capsys.readouterr()
    assert "[ferret]" in captured.err and "://" in captured.err


def test_run_dispatches_sessions_subcommand(capsys) -> None:
    """``ferret sessions list`` is wired to the FF1 handler."""
    rc = ferret_cli.run(_ns(subcommand="sessions", sub_action="list"))
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert output


def test_run_dispatches_share_subcommand(capsys) -> None:
    rc = ferret_cli.run(_ns(subcommand="share", sub_action="run-42"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "ferret share" in captured.err


def test_run_dispatches_agents_subcommand(capsys) -> None:
    rc = ferret_cli.run(
        _ns(subcommand="agents", sub_action="show", sub_target="reviewer")
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "ferret agents" in captured.err


def test_run_dispatches_bench_subcommand(capsys) -> None:
    rc = ferret_cli.run(_ns(subcommand="bench", sub_action="humaneval"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "ferret bench" in captured.err


def test_run_with_no_args_emits_usage_hint(capsys) -> None:
    """Bare ``chimera ferret`` (no -p, no subcommand) exits 2 with hint.

    The REPL entry path attempts to delegate to ``run_code`` but in
    this test environment (no provider configured) it should surface
    a clean error rather than crash. Either rc==2 (scaffold path)
    or rc==1 (provider error) is acceptable; both indicate the user
    needs to do something.
    """
    rc = ferret_cli.run(_ns(_quiet_run_dir=True))
    assert rc in (1, 2)


# ---------------------------------------------------------------------------
# Allowed-tools filtering
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_filter_allowed_tools_passthrough_on_empty() -> None:
    tools = [_FakeTool("read"), _FakeTool("bash")]
    assert ferret_cli._filter_allowed_tools(tools, "") == tools  # noqa: SLF001


def test_filter_allowed_tools_case_insensitive() -> None:
    tools = [_FakeTool("read"), _FakeTool("bash"), _FakeTool("write")]
    kept = ferret_cli._filter_allowed_tools(tools, "Read,BASH")  # noqa: SLF001
    kept_names = sorted(t.name for t in kept)
    assert kept_names == ["bash", "read"]


def test_filter_allowed_tools_rejects_unknown() -> None:
    tools = [_FakeTool("read")]
    with pytest.raises(ferret_cli._UnknownAllowedTool):  # noqa: SLF001
        ferret_cli._filter_allowed_tools(tools, "read,nope")  # noqa: SLF001


# ---------------------------------------------------------------------------
# Trademark hygiene
# ---------------------------------------------------------------------------


def test_help_does_not_name_upstream_brand() -> None:
    """``chimera ferret --help`` must not name the upstream OpenAI-flagship brand."""
    proc = _run("ferret", "--help")
    assert proc.returncode == 0, proc.stderr
    out = (proc.stdout + proc.stderr).lower()
    # The literal upstream brand name must never appear in user-visible
    # output. ``codex`` as a path fragment (``~/.codex/config.toml``) is
    # explicitly allowed by the SPEC because it's a filesystem fact.
    # We assert the brand-y forms are absent in surrounding context.
    assert " codex " not in f" {out} "
    assert "openai codex" not in out
