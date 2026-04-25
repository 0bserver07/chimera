"""Regression tests for ``chimera mink agents list / show <name>``.

The subcommand surfaces the same project > user > built-in chain that
``--agent <name>`` walks, so listing and resolution stay in sync. Tests
cover empty discovery (built-ins still present), project + user
discovery, the ``show`` happy path, and the ``show`` miss-exits-2 path.
"""
from __future__ import annotations

from pathlib import Path

import pytest



# WHY: chimera.mink.cli imports rich (mink extra). Skip when not installed.
pytest.importorskip("rich")
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_agent_md(directory: Path, name: str, body: str) -> Path:
    """Materialize ``<directory>/<name>.md`` with the given body."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(body)
    return path


_LOCAL_AGENT = (
    "---\n"
    "name: local\n"
    "description: A project-scoped test agent.\n"
    "tools: [Bash, Read]\n"
    "model: glm-5.1:cloud\n"
    "---\n"
    "You are the local project agent. Use bash and read tools.\n"
    "Be concise."
)

_GLOBAL_AGENT = (
    "---\n"
    "name: global\n"
    "description: A user-scoped test agent.\n"
    "tools: [Edit]\n"
    "model: kimi-k2.6:cloud\n"
    "---\n"
    "You are the user-scope agent. Use edit only."
)


# ---------------------------------------------------------------------------
# 1. list_empty — built-ins still produce a non-empty result
# ---------------------------------------------------------------------------


def test_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No project/user agent files → built-in registry still populates list."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    empty_cwd = tmp_path / "empty"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    from chimera.mink.agents import iter_agents

    records = list(iter_agents())
    # Built-in registry ships at least the five preset agents
    # (build/explore/general/plan/review), so the listing is never empty
    # even on a fresh machine.
    assert len(records) >= 1
    sources = {r.source for r in records}
    assert "builtin" in sources
    names = {r.name for r in records}
    # At least one of the canonical built-in presets must be present.
    assert names & {"build", "explore", "general", "plan", "review"}


# ---------------------------------------------------------------------------
# 2. list_includes_project_agent
# ---------------------------------------------------------------------------


def test_list_includes_project_agent(tmp_path: Path) -> None:
    """A ``.claude/agents/local.md`` shows up tagged source=project."""
    _write_agent_md(tmp_path / ".claude" / "agents", "local", _LOCAL_AGENT)

    from chimera.mink.agents import iter_agents

    records = list(iter_agents(cwd=tmp_path))
    project_records = [r for r in records if r.source == "project"]
    assert any(r.name == "local" for r in project_records)
    local = next(r for r in project_records if r.name == "local")
    assert local.model == "glm-5.1:cloud"
    assert "Bash" in local.tools and "Read" in local.tools
    assert local.path is not None and local.path.name == "local.md"


# ---------------------------------------------------------------------------
# 3. list_includes_user_agent
# ---------------------------------------------------------------------------


def test_list_includes_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``~/.claude/agents/global.md`` shows up tagged source=user."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    _write_agent_md(fake_home / ".claude" / "agents", "global", _GLOBAL_AGENT)

    from chimera.mink.agents import iter_agents

    # Use a project dir that has no .claude/agents/ so the only on-disk
    # match is the user-scope one we just wrote.
    project = tmp_path / "project"
    project.mkdir()
    records = list(iter_agents(cwd=project))
    user_records = [r for r in records if r.source == "user"]
    assert any(r.name == "global" for r in user_records)
    g = next(r for r in user_records if r.name == "global")
    assert g.model == "kimi-k2.6:cloud"
    assert g.tools == ["Edit"]


# ---------------------------------------------------------------------------
# 4. show_existing — agents show <name> prints model + tools + first line
# ---------------------------------------------------------------------------


def test_show_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``mink agents show local`` prints model, tools, and prompt preview."""
    # Pin both HOME and cwd so find_agent's project-scope lookup hits our
    # tmp_path agent file.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    _write_agent_md(tmp_path / ".claude" / "agents", "local", _LOCAL_AGENT)
    monkeypatch.chdir(tmp_path)

    from chimera.mink.cli import _run_agents_show

    rc = _run_agents_show("local", no_color=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Agent: local" in out
    assert "glm-5.1:cloud" in out
    assert "Bash" in out and "Read" in out
    # First body line of _LOCAL_AGENT is "You are the local project agent..."
    assert "local project agent" in out


# ---------------------------------------------------------------------------
# 5. show_unknown_exits_2 — friendly miss path
# ---------------------------------------------------------------------------


def test_show_unknown_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown agent name exits 2 with a stderr hint about the search paths."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    monkeypatch.chdir(tmp_path)

    from chimera.mink.cli import _run_agents_show

    rc = _run_agents_show("nonexistent-agent-xyz", no_color=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "not found" in err
    assert "nonexistent-agent-xyz" in err
    assert ".claude/agents" in err
