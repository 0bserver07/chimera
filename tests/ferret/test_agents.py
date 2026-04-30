"""Regression tests for ``chimera ferret agents`` discovery + presets.

The subcommand surfaces the same project > user > built-in chain that
``--agent <name>`` walks (under ferret conventions: ``.codex/agent``
instead of ``.opencode/agent``). Tests cover empty discovery (built-ins
still present), project + user discovery, override semantics, the
``cmd_agents_show`` happy path, and the unknown-name miss path. They
also assert the ferret built-in preset registry exposes the documented
``build / plan / review / explore / general`` set.
"""
from __future__ import annotations

from pathlib import Path

import pytest


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
    "tools: [bash, read_file]\n"
    "model: gpt-5\n"
    "---\n"
    "You are the local project agent. Use bash and read tools.\n"
    "Be concise."
)

_GLOBAL_AGENT = (
    "---\n"
    "name: global\n"
    "description: A user-scoped test agent.\n"
    "tools: [edit_file]\n"
    "model: gpt-4o\n"
    "---\n"
    "You are the user-scope agent. Use edit only."
)

_OVERRIDE_AGENT_PROJECT = (
    "---\n"
    "name: override\n"
    "description: Project copy.\n"
    "tools: [bash]\n"
    "model: project-model\n"
    "---\n"
    "Project-version body."
)

_OVERRIDE_AGENT_USER = (
    "---\n"
    "name: override\n"
    "description: User copy.\n"
    "tools: [read_file]\n"
    "model: user-model\n"
    "---\n"
    "User-version body."
)


# ---------------------------------------------------------------------------
# 1. iter_agents — built-ins still produce a non-empty result
# ---------------------------------------------------------------------------


def test_iter_agents_builtins_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No project/user agent files → built-in registry still populates list."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    empty_cwd = tmp_path / "empty"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    from chimera.ferret.agents import iter_agents

    records = list(iter_agents())
    assert len(records) >= 1
    sources = {r.source for r in records}
    assert "builtin" in sources
    names = {r.name for r in records}
    # Ferret advertises this preset set; the registry must expose them.
    assert {"build", "plan", "review", "explore", "general"}.issubset(names)


# ---------------------------------------------------------------------------
# 2. Project agent discovery
# ---------------------------------------------------------------------------


def test_iter_agents_project_dir(tmp_path: Path) -> None:
    """A ``.codex/agent/local.md`` shows up tagged source=project."""
    _write_agent_md(tmp_path / ".codex" / "agent", "local", _LOCAL_AGENT)

    from chimera.ferret.agents import iter_agents

    records = list(iter_agents(cwd=tmp_path))
    project_records = [r for r in records if r.source == "project"]
    assert any(r.name == "local" for r in project_records)
    local = next(r for r in project_records if r.name == "local")
    assert local.model == "gpt-5"
    assert "bash" in local.tools and "read_file" in local.tools
    assert local.path is not None and local.path.name == "local.md"
    assert "local project agent" in local.system_prompt


# ---------------------------------------------------------------------------
# 3. User agent discovery
# ---------------------------------------------------------------------------


def test_iter_agents_user_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``~/.codex/agent/global.md`` shows up tagged source=user."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    _write_agent_md(fake_home / ".codex" / "agent", "global", _GLOBAL_AGENT)

    project = tmp_path / "project"
    project.mkdir()

    from chimera.ferret.agents import iter_agents

    records = list(iter_agents(cwd=project))
    user_records = [r for r in records if r.source == "user"]
    assert any(r.name == "global" for r in user_records)
    g = next(r for r in user_records if r.name == "global")
    assert g.model == "gpt-4o"
    assert g.tools == ["edit_file"]


# ---------------------------------------------------------------------------
# 4. Project overrides user — find_agent priority
# ---------------------------------------------------------------------------


def test_find_agent_project_overrides_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When project + user define the same name, project wins."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    project_root = tmp_path / "project"
    project_root.mkdir()

    _write_agent_md(
        fake_home / ".codex" / "agent", "override", _OVERRIDE_AGENT_USER
    )
    _write_agent_md(
        project_root / ".codex" / "agent", "override", _OVERRIDE_AGENT_PROJECT
    )

    from chimera.ferret.agents import find_agent

    record = find_agent("override", cwd=project_root)
    assert record is not None
    assert record.source == "project"
    assert record.model == "project-model"
    assert record.tools == ["bash"]
    assert "Project-version" in record.system_prompt


# ---------------------------------------------------------------------------
# 5. find_agent — unresolved name returns None
# ---------------------------------------------------------------------------


def test_find_agent_unknown_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown agent name → :func:`find_agent` returns ``None`` cleanly."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    monkeypatch.chdir(tmp_path)

    from chimera.ferret.agents import find_agent

    assert find_agent("definitely-not-an-agent-xyz") is None


# ---------------------------------------------------------------------------
# 6. load_ferret_agents — returns AgentConfig list with project priority
# ---------------------------------------------------------------------------


def test_load_ferret_agents_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project agents override user-scope; presets fill in the rest."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    project_root = tmp_path / "project"
    project_root.mkdir()

    _write_agent_md(
        fake_home / ".codex" / "agent", "override", _OVERRIDE_AGENT_USER
    )
    _write_agent_md(
        project_root / ".codex" / "agent", "override", _OVERRIDE_AGENT_PROJECT
    )

    from chimera.agents.config import AgentConfig
    from chimera.ferret.agents import load_ferret_agents

    configs = load_ferret_agents(project_root=project_root)
    assert all(isinstance(c, AgentConfig) for c in configs)
    by_name = {c.name: c for c in configs}

    # Override resolved to project copy.
    assert "override" in by_name
    assert by_name["override"].model == "project-model"
    assert by_name["override"].tools == ["bash"]

    # Built-in presets surface.
    for preset in ("build", "plan", "review", "explore", "general"):
        assert preset in by_name, f"missing preset: {preset}"


def test_load_ferret_agents_no_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without any .codex/agent dirs, the built-in presets still load."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    project_root = tmp_path / "project"
    project_root.mkdir()

    from chimera.ferret.agents import load_ferret_agents

    configs = load_ferret_agents(project_root=project_root)
    names = {c.name for c in configs}
    assert {"build", "plan", "review", "explore", "general"}.issubset(names)


# ---------------------------------------------------------------------------
# 7. cmd_agents_list — happy path prints table headers and built-in names
# ---------------------------------------------------------------------------


def test_cmd_agents_list_prints_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``ferret agents list`` prints a table with built-in presets."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    monkeypatch.chdir(tmp_path)

    from chimera.ferret.agents import cmd_agents_list

    rc = cmd_agents_list(no_color=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "NAME" in out and "SOURCE" in out and "DESCRIPTION" in out
    # At least one of the built-in presets renders in the table.
    assert "build" in out


# ---------------------------------------------------------------------------
# 8. cmd_agents_show — happy path
# ---------------------------------------------------------------------------


def test_cmd_agents_show_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``ferret agents show local`` prints model, tools, and prompt preview."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    _write_agent_md(tmp_path / ".codex" / "agent", "local", _LOCAL_AGENT)
    monkeypatch.chdir(tmp_path)

    from chimera.ferret.agents import cmd_agents_show

    rc = cmd_agents_show("local", no_color=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Agent: local" in out
    assert "gpt-5" in out
    assert "bash" in out and "read_file" in out
    assert "local project agent" in out


# ---------------------------------------------------------------------------
# 9. cmd_agents_show — unknown name exits 2 with a friendly hint
# ---------------------------------------------------------------------------


def test_cmd_agents_show_unknown_exits_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unknown name exits 2 with a stderr hint about the search paths."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))  # type: ignore[arg-type]
    monkeypatch.chdir(tmp_path)

    from chimera.ferret.agents import cmd_agents_show

    rc = cmd_agents_show("nonexistent-agent-xyz", no_color=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "not found" in err
    assert "nonexistent-agent-xyz" in err
    assert ".codex/agent" in err


def test_cmd_agents_show_missing_name_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``ferret agents show`` (no NAME) exits 2 with a usage hint."""
    from chimera.ferret.agents import cmd_agents_show

    rc = cmd_agents_show(None, no_color=True)
    err = capsys.readouterr().err
    assert rc == 2
    assert "AGENT_NAME" in err


# ---------------------------------------------------------------------------
# 10. format_agents_table — empty input falls back to footer
# ---------------------------------------------------------------------------


def test_format_agents_table_empty() -> None:
    """Empty record list still renders a header and the friendly footer."""
    from chimera.ferret.agents import format_agents_table

    rendered = format_agents_table([], no_color=True)
    assert "NAME" in rendered
    assert "(no agents discovered)" in rendered
