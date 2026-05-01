"""Smoke tests for the ``chimera shrew`` CLI scaffold (agent S1).

Covers:

* ``add_arguments`` registers the documented small-model flag surface
  (``--version``, ``--mode``, ``--model``, ``-p/--print``, ``--json``,
  ``--list-models``, ``--cwd``, ``--max-steps``, ``--allowed-tools``,
  plus the ``sessions`` / ``bench`` subcommand placeholders).
* The pinned defaults for ``--model``, ``--max-steps``, and
  ``--allowed-tools`` match the small-model coding agent posture.
* ``chimera shrew --version`` exits 0 and emits ``chimera shrew 0.5.0``.
* ``--mode`` is validated by argparse's ``choices``.
* The ``sessions`` and ``bench`` subcommand placeholders route through
  :func:`chimera.shrew.cli.run` to the right late-bound handler.
* ``--list-models`` short-circuits to the cost.PRICING registry.
* ``--mode rpc`` / ``--mode sdk`` placeholders behave (rpc forwards to
  weasel's RPC, sdk prints the embedding pointer).
* ``--mode print`` without ``-p`` is a usage error (exit 2).
* ``-p`` priority over ``--mode`` matches weasel's ergonomics.

Tests stay lightweight: no live provider, no network. The one-shot
``-p`` flow is exercised via mock to keep these tests offline.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import pytest

from chimera.shrew import cli as shrew_cli

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


# ---------------------------------------------------------------------------
# add_arguments: parser surface
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera shrew")
    shrew_cli.add_arguments(parser)
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
        "--allowed-tools",
        "--bench-limit",
    }
    missing = expected - options
    assert not missing, f"missing flags on shrew parser: {sorted(missing)}"


def test_default_model_is_qwen3_6_35b_a3b(monkeypatch) -> None:
    """``--model`` defaults to the small-model llama.cpp catalog id."""
    monkeypatch.delenv("SHREW_MODEL", raising=False)
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == "qwen3.6-35b-a3b"
    assert args.model == shrew_cli._DEFAULT_MODEL  # noqa: SLF001


def test_default_model_honors_env_override(monkeypatch) -> None:
    """``$SHREW_MODEL`` overrides the small-model default."""
    monkeypatch.setenv("SHREW_MODEL", "qwen3.5-9b")
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == "qwen3.5-9b"


def test_default_max_steps_is_thirty() -> None:
    """``--max-steps`` defaults to 30 (smaller than mink/otter's 50)."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.max_steps == 30
    assert args.max_steps == shrew_cli._DEFAULT_MAX_STEPS  # noqa: SLF001


def test_max_steps_can_be_overridden() -> None:
    parser = _build_parser()
    args = parser.parse_args(["--max-steps", "10"])
    assert args.max_steps == 10


def test_default_allowed_tools_is_restricted_subset() -> None:
    """``--allowed-tools`` defaults to the small-model restricted subset."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.allowed_tools == "Read,Write,Edit,Bash"
    assert args.allowed_tools == shrew_cli._DEFAULT_ALLOWED_TOOLS  # noqa: SLF001


def test_allowed_tools_empty_string_opts_back_to_full() -> None:
    """``--allowed-tools=''`` lets the full default tool group through."""
    parser = _build_parser()
    args = parser.parse_args(["--allowed-tools", ""])
    assert args.allowed_tools == ""


def test_add_arguments_mode_choices() -> None:
    """``--mode`` rejects values outside the documented set."""
    parser = _build_parser()
    for mode in ("interactive", "print", "rpc", "sdk"):
        args = parser.parse_args(["--mode", mode])
        assert args.mode == mode
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
    args = parser.parse_args(["bench", "aider-polyglot"])
    assert args.subcommand == "bench"
    assert args.sub_action == "aider-polyglot"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus-subcommand"])


def test_add_arguments_sub_action_choices() -> None:
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sessions", "delete"])


def test_add_arguments_json_default_false() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.json_output is False
    args = parser.parse_args(["--json"])
    assert args.json_output is True


def test_add_arguments_bench_limit_defaults_to_five() -> None:
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.bench_limit == 5


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_flag_prints_zero_five_zero() -> None:
    """``chimera shrew --version`` prints ``chimera shrew 0.5.0``."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera", "shrew", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "chimera shrew" in out
    assert "0.5.0" in out
    assert _SEMVER_RE.search(out)


def test_resolve_version_string() -> None:
    """The internal helper returns the minted semver."""
    assert shrew_cli._resolve_version() == "0.5.0"  # noqa: SLF001


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
    rc = shrew_cli.run(args)
    captured = capsys.readouterr()
    assert rc == 0
    # PRICING ships at least one Anthropic + one OpenAI entry.
    assert "claude-sonnet-4" in captured.out
    assert "gpt-4o" in captured.out


def test_run_list_models_helper_output(monkeypatch, capsys) -> None:
    """``_run_list_models`` writes one model per line in sorted order."""
    monkeypatch.setattr(
        "chimera.providers.cost.PRICING",
        {"alpha-1": (1.0, 1.0), "beta-2": (2.0, 2.0)},
    )
    rc = shrew_cli._run_list_models()  # noqa: SLF001
    captured = capsys.readouterr()
    assert rc == 0
    lines = [line for line in captured.out.splitlines() if line]
    assert lines == ["alpha-1", "beta-2"]


# ---------------------------------------------------------------------------
# Mode dispatch — RPC / SDK / print-without-prompt
# ---------------------------------------------------------------------------


def test_rpc_mode_late_binds_to_weasel(monkeypatch) -> None:
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
    rc = shrew_cli.run(args)
    assert rc == 0
    assert captured["args"] is args


def test_sdk_mode_returns_zero(capsys) -> None:
    """``--mode sdk`` prints the embedding pointer and exits 0."""
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        print_mode=None,
        mode="sdk",
    )
    rc = shrew_cli.run(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "from chimera.weasel.sdk import Agent" in captured.err


def test_print_mode_without_prompt_is_usage_error(capsys) -> None:
    """``--mode print`` without ``-p`` is an explicit usage error."""
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        print_mode=None,
        mode="print",
    )
    rc = shrew_cli.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "requires -p PROMPT" in captured.err


# ---------------------------------------------------------------------------
# Subcommand routing
# ---------------------------------------------------------------------------


def test_sessions_subcommand_routes_to_handler(monkeypatch) -> None:
    """``run`` dispatches ``sessions`` to the S1 sessions handler."""
    captured: dict[str, object] = {}

    def _fake_dispatch(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(
        "chimera.shrew.sessions.dispatch_sessions", _fake_dispatch,
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
    rc = shrew_cli.run(args)
    assert rc == 0
    assert captured["args"] is args


def test_bench_subcommand_without_module_is_usage_error(
    monkeypatch, capsys,
) -> None:
    """When the S4 benchmarks module is missing, ``bench`` exits 2 with a hint."""
    # Force the import to fail (simulate the S4 module not being landed yet).
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict,
    ) else __builtins__.__import__

    def _no_bench(name, *a, **kw):
        if name == "chimera.shrew.benchmarks.cli":
            raise ImportError("S4 not yet wired")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _no_bench)
    args = argparse.Namespace(
        list_models=False,
        subcommand="bench",
        sub_action="aider-polyglot",
        sub_target=None,
        print_mode=None,
        mode="interactive",
    )
    rc = shrew_cli.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "aider-polyglot" in captured.err
    assert "scaffold" in captured.err or "not yet" in captured.err


# ---------------------------------------------------------------------------
# Print path: ``-p`` short-circuit takes priority over --mode
# ---------------------------------------------------------------------------


def test_print_p_priority(monkeypatch) -> None:
    """``-p PROMPT`` triggers ``_run_print_mode`` regardless of --mode."""
    called: dict[str, object] = {}

    def _fake_print(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(shrew_cli, "_run_print_mode", _fake_print)
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        sub_action=None,
        sub_target=None,
        print_mode="hello",
        mode="interactive",
    )
    rc = shrew_cli.run(args)
    assert rc == 0
    assert called["args"] is args


def test_print_mode_uses_shrew_provider_chain(monkeypatch) -> None:
    """``_run_print_mode`` builds a provider via shrew's native chain.

    This used to delegate to ``chimera.weasel.cli._run_print_mode``,
    which silently lost shrew's "llama.cpp first, Ollama next, cloud
    fallback" preference. We now build the provider locally via
    :func:`chimera.shrew.providers.build_provider` and apply skills +
    extensions before handing control to the agent.
    """
    called: dict[str, object] = {}

    class _StubProvider:
        model_name = "stub-model"
        _context_length = 32_768

        def __init__(self) -> None:
            pass

    def _fake_build_provider(args):
        called["build_provider_args"] = args
        return _StubProvider()

    class _StubResult:
        success = True
        output = "ok"

    async def _fake_async_run(self, prompt, env=None):  # noqa: ARG001
        called["agent_prompt"] = prompt
        called["agent_system"] = self.prompt.render(
            tools=[t.name for t in self.tools],
        )
        called["agent_tool_count"] = len(self.tools)
        return _StubResult()

    monkeypatch.setattr(
        "chimera.shrew.providers.build_provider", _fake_build_provider,
    )
    monkeypatch.setattr(
        "chimera.core.agent.Agent.async_run", _fake_async_run,
    )

    args = argparse.Namespace(
        print_mode="hi",
        model="qwen3.6-35b-a3b",
        max_steps=5,
        allowed_tools="Read,Write,Edit,Bash",
        cwd=None,
        json_output=False,
        vram_gb=None,
    )
    rc = shrew_cli._run_print_mode(args)  # noqa: SLF001
    assert rc == 0
    assert called["build_provider_args"] is args
    # --allowed-tools=Read,Write,Edit,Bash narrows the AGENT_TOOLS surface
    # via the friendly-alias map (Read -> read_file, etc.).
    assert called["agent_tool_count"] == 4
    # The S2 skills block + S3 small-model scaffold should both have
    # made it into the rendered system prompt.
    rendered = str(called["agent_system"])
    assert "Shrew skills" in rendered
    assert "<small-model-scaffold>" in rendered


def test_default_routes_to_repl(monkeypatch) -> None:
    """No -p / no subcommand / mode=interactive lands on the shrew REPL."""
    called: dict[str, object] = {}

    def _fake_repl(args):
        called["args"] = args
        return 0

    monkeypatch.setattr("chimera.shrew.repl.run", _fake_repl)
    args = argparse.Namespace(
        list_models=False,
        subcommand=None,
        sub_action=None,
        sub_target=None,
        print_mode=None,
        mode="interactive",
        cwd=None,
        model="qwen3.6-35b-a3b",
        max_steps=30,
    )
    rc = shrew_cli.run(args)
    assert rc == 0
    assert called["args"] is args


# ---------------------------------------------------------------------------
# Live --help / --version parity (subprocess path)
# ---------------------------------------------------------------------------


def test_help_lists_small_model_surface() -> None:
    """``--help`` exits 0 and lists load-bearing shrew flags."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera", "shrew", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for needle in (
        "--mode",
        "--model",
        "--print",
        "--json",
        "--list-models",
        "--allowed-tools",
        "--max-steps",
    ):
        assert needle in out, f"missing {needle!r} in --help output"


# ---------------------------------------------------------------------------
# Trademark hygiene — no upstream brand names in CLI module source
# ---------------------------------------------------------------------------


def test_cli_source_has_no_upstream_brand_name() -> None:
    """The cli module body must not name the upstream small-model coding agent."""
    src = (
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        + "/chimera/shrew/cli.py"
    )
    with open(src, encoding="utf-8") as f:
        body = f.read()
    # The brand name is "little-coder" without quotes; ``~/.little-coder/``
    # appears in the docstring as a filesystem path fact (allowed). What is
    # forbidden is naming the brand as a product. We assert that "little-coder"
    # only appears as part of the path mention — never as a standalone product
    # claim like "the little-coder agent".
    occurrences = body.lower().count("little-coder")
    # Allow at most one mention (the path fact in the module docstring).
    assert occurrences <= 1, (
        f"shrew/cli.py mentions 'little-coder' {occurrences}× — "
        "trademark hygiene allows at most one filesystem-path mention."
    )
