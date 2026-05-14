"""Tests for the team-role frontmatter field + ``discover_team_roles``."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from chimera.agents.config import AgentConfig
from chimera.agents.team_roles import discover_team_roles


# ---------------------------------------------------------------------------
# AgentConfig.from_markdown parses team_role
# ---------------------------------------------------------------------------


def test_parse_with_team_role(tmp_path: Path) -> None:
    """``from_markdown`` reads ``team_role`` out of the frontmatter."""
    md = tmp_path / "executor.md"
    md.write_text(
        """\
---
name: my-executor
description: Carries things out
team_role: executor
---
You are the executor.
"""
    )

    cfg = AgentConfig.from_markdown(str(md))
    assert cfg.team_role == "executor"
    assert cfg.name == "my-executor"


def test_parse_without_team_role(tmp_path: Path) -> None:
    """``team_role`` defaults to ``None`` when the frontmatter omits it."""
    md = tmp_path / "no-role.md"
    md.write_text(
        """\
---
name: just-an-agent
description: Plain agent
---
Hello.
"""
    )

    cfg = AgentConfig.from_markdown(str(md))
    assert cfg.team_role is None


# ---------------------------------------------------------------------------
# discover_team_roles surfaces the four built-in presets
# ---------------------------------------------------------------------------


_EXPECTED_BUILTIN_ROLES = {"executor", "planner", "researcher", "reviewer"}


def test_discover_finds_presets(tmp_path: Path) -> None:
    """The 4 packaged subagent profiles all appear in the discovery list.

    Uses a tmp_path workdir and a tmp home so user-side overrides don't
    leak in from the host machine running the tests.
    """
    with mock.patch.object(Path, "home", return_value=tmp_path / "no-user"):
        roles = discover_team_roles(workdir=tmp_path)

    role_names = {entry["role"] for entry in roles}
    assert _EXPECTED_BUILTIN_ROLES.issubset(role_names)

    # The presets shipped with the package — confirm source_path points
    # into chimera/agents/presets/subagents/.
    for entry in roles:
        if entry["role"] in _EXPECTED_BUILTIN_ROLES:
            assert "presets/subagents" in entry["source_path"]
            # Every preset declares a tool set, so tools is a list.
            assert isinstance(entry["tools"], list)


def test_discover_priority(tmp_path: Path) -> None:
    """A project agent overrides a built-in preset of the same team_role."""
    project_agents = tmp_path / ".chimera" / "agents"
    project_agents.mkdir(parents=True)
    override = project_agents / "my-executor.md"
    override.write_text(
        """\
---
name: my-executor
description: Project-specific executor
team_role: executor
tools: [bash]
model: glm-5
---
Override body.
"""
    )

    with mock.patch.object(Path, "home", return_value=tmp_path / "no-user"):
        roles = discover_team_roles(workdir=tmp_path)

    by_role = {entry["role"]: entry for entry in roles}
    assert by_role["executor"]["source_path"] == str(override)
    assert by_role["executor"]["description"] == "Project-specific executor"
    assert by_role["executor"]["model"] == "glm-5"
    assert by_role["executor"]["tools"] == ["bash"]


def test_discover_returns_sorted(tmp_path: Path) -> None:
    """Roles come back sorted alphabetically by role name."""
    with mock.patch.object(Path, "home", return_value=tmp_path / "no-user"):
        roles = discover_team_roles(workdir=tmp_path)

    names = [entry["role"] for entry in roles]
    assert names == sorted(names)
