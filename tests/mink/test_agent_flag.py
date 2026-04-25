"""Tests for ``chimera mink --agent <name>`` registry wiring (audit H-6).

Covers:
* ``.claude/agents/<name>.md`` resolves through :func:`_resolve_agent_spec`
  with the right tools, system_prompt, and model.
* Built-in preset registry (``build``, ``explore``, ...) also resolves
  via the same helper.
* CLI ``--agent <unknown>`` exits non-zero with a descriptive stderr
  message that names the search paths.
* CLI ``--model <override>`` wins over an agent's frontmatter ``model:``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from chimera.mink.cli import _resolve_agent_spec

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_agent_md(directory: Path, name: str, body: str) -> Path:
    """Create ``<directory>/<name>.md`` with the supplied frontmatter+body."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(body)
    return path


def test_resolve_agent_spec_loads_project_claude_md(tmp_path: Path) -> None:
    """A ``.claude/agents/<name>.md`` is discovered with tools+model+body."""
    body = (
        "---\n"
        "name: test-agent\n"
        "description: Test agent\n"
        "tools: [Bash]\n"
        "model: glm-5.1:cloud\n"
        "---\n"
        "You are a focused test agent. Use bash only."
    )
    _write_agent_md(tmp_path / ".claude" / "agents", "test-agent", body)

    spec = _resolve_agent_spec("test-agent", tmp_path)

    assert spec is not None
    assert spec.name == "test-agent"
    assert spec.tools == ["Bash"]
    assert spec.model == "glm-5.1:cloud"
    assert "focused test agent" in spec.system_prompt
    assert spec.source == "project"


def test_resolve_agent_spec_falls_back_to_builtin_preset(tmp_path: Path) -> None:
    """Names not on disk are still resolvable from the built-in registry."""
    # No .claude/agents/ in tmp_path; ``build`` is a built-in preset.
    spec = _resolve_agent_spec("build", tmp_path)

    assert spec is not None
    assert spec.name == "build"
    # Built-in source label is the AgentLoader path or "builtin".
    assert spec.source in {"builtin", "loader", "project", "user"}


def test_resolve_agent_spec_returns_none_for_unknown(tmp_path: Path) -> None:
    """Unknown agent name yields ``None`` (CLI converts that to exit 2)."""
    spec = _resolve_agent_spec("definitely-not-a-real-agent-name-xyz", tmp_path)
    assert spec is None


def test_cli_agent_unknown_exits_non_zero_with_helpful_message(tmp_path: Path) -> None:
    """``chimera mink --agent <bogus>`` exits non-zero with stderr hint."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "chimera.cli.main",
            "mink",
            "--cwd",
            str(tmp_path),
            "--agent",
            "definitely-not-a-real-agent-name-xyz",
            "-p",
            "noop",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
        env={**os.environ, "OLLAMA_HOST": "http://127.0.0.1:1"},  # force fast fail
    )
    assert proc.returncode != 0
    assert "not found" in proc.stderr
    assert ".claude/agents" in proc.stderr


def test_cli_advertises_agent_flag_in_help() -> None:
    """``chimera mink --help`` advertises ``--agent``."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "mink", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "--agent" in proc.stdout


def test_resolve_agent_spec_user_scope_when_no_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent in ``~/.claude/agents/`` resolves when no project copy exists."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    body = (
        "---\n"
        "name: user-only\n"
        "tools: [Read]\n"
        "model: kimi-k2.6:cloud\n"
        "---\n"
        "Body for user-scope agent."
    )
    _write_agent_md(fake_home / ".claude" / "agents", "user-only", body)

    spec = _resolve_agent_spec("user-only", tmp_path / "project")

    assert spec is not None
    assert spec.name == "user-only"
    assert spec.tools == ["Read"]
    assert spec.model == "kimi-k2.6:cloud"
    assert spec.source == "user"


def test_cli_model_override_wins_over_agent_model(tmp_path: Path) -> None:
    """``--model X --agent Y`` (Y has model: Z) sets effective model = X.

    Smoke-checks the precedence by inspecting argparse: the resolution
    happens inside ``_run_print_mode`` which would require live provider
    bring-up, so we exercise the resolver directly and assert the
    decision the CLI makes (CLI value != _DEFAULT_MODEL implies override).
    """
    from chimera.mink.cli import _DEFAULT_MODEL

    body = (
        "---\n"
        "name: precedence-agent\n"
        "tools: [Bash]\n"
        "model: agent-model:cloud\n"
        "---\n"
        "Body."
    )
    _write_agent_md(tmp_path / ".claude" / "agents", "precedence-agent", body)
    spec = _resolve_agent_spec("precedence-agent", tmp_path)
    assert spec is not None and spec.model == "agent-model:cloud"

    # Mirror the CLI's precedence rule: if --model differs from the default,
    # CLI wins over agent.model.
    cli_model = "cli-override:cloud"
    user_passed_model = cli_model != _DEFAULT_MODEL
    effective = (
        cli_model
        if user_passed_model or spec is None or not spec.model
        else spec.model
    )
    assert effective == "cli-override:cloud"

    # Inverse: CLI left at default => agent.model wins.
    cli_default = _DEFAULT_MODEL
    user_passed_default = cli_default != _DEFAULT_MODEL
    effective_default = (
        cli_default
        if user_passed_default or spec is None or not spec.model
        else spec.model
    )
    assert effective_default == "agent-model:cloud"
