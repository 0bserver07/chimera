"""Tests for chimera.agents.loader — FileAgentDef, AgentLoader, AgentFactory."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.agents.loader import AgentFactory, AgentLoader, FileAgentDef


# ---------------------------------------------------------------------------
# FileAgentDef.from_file
# ---------------------------------------------------------------------------

class TestFileAgentDef:
    def test_from_file_with_frontmatter(self, tmp_path: Path) -> None:
        md_file = tmp_path / "reviewer.md"
        md_file.write_text("""\
---
name: code-reviewer
description: Reviews code
tools: [bash, read_file]
loop: react
max_iterations: 15
triggers: [review, lint]
skills: [python-patterns]
model: claude-sonnet-4-6
---
You are a code reviewer.""")

        result = FileAgentDef.from_file(md_file, source="project")
        assert result.name == "code-reviewer"
        assert result.description == "Reviews code"
        assert result.tools == ["bash", "read_file"]
        assert result.loop == "react"
        assert result.max_iterations == 15
        assert result.triggers == ["review", "lint"]
        assert result.skills == ["python-patterns"]
        assert result.model == "claude-sonnet-4-6"
        assert result.system_prompt == "You are a code reviewer."
        assert result.source == "project"
        assert result.file_path == str(md_file)

    def test_from_file_no_frontmatter(self, tmp_path: Path) -> None:
        md_file = tmp_path / "simple.md"
        md_file.write_text("Just a simple agent.")

        result = FileAgentDef.from_file(md_file, source="user")
        assert result.name == "simple"
        assert result.system_prompt == "Just a simple agent."
        assert result.source == "user"
        assert result.tools == []
        assert result.triggers == []

    def test_from_file_incomplete_frontmatter(self, tmp_path: Path) -> None:
        md_file = tmp_path / "partial.md"
        md_file.write_text("---\nname: partial\n---")

        result = FileAgentDef.from_file(md_file, source="builtin")
        assert result.name == "partial"
        assert result.system_prompt == ""

    def test_from_file_defaults(self, tmp_path: Path) -> None:
        md_file = tmp_path / "defaults.md"
        md_file.write_text("---\nname: my-agent\n---\nHello world.")

        result = FileAgentDef.from_file(md_file)
        assert result.loop == "react"
        assert result.max_iterations == 50
        assert result.model is None

    def test_from_file_uses_stem_as_name(self, tmp_path: Path) -> None:
        md_file = tmp_path / "my-cool-agent.md"
        md_file.write_text("---\ndescription: Cool\n---\nBe cool.")

        result = FileAgentDef.from_file(md_file)
        assert result.name == "my-cool-agent"


# ---------------------------------------------------------------------------
# AgentLoader
# ---------------------------------------------------------------------------

class TestAgentLoader:
    def test_load_from_project_dir(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "alpha.md").write_text("---\nname: alpha\ntriggers: [a]\n---\nAgent alpha.")
        (agents_dir / "beta.md").write_text("---\nname: beta\ntriggers: [b]\n---\nAgent beta.")

        loader = AgentLoader(project_root=str(tmp_path))
        agents = loader.load_all()
        assert "alpha" in agents
        assert "beta" in agents
        assert agents["alpha"].source == "project"

    def test_get_lazy_loads(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.md").write_text("---\nname: test\n---\nTest.")

        loader = AgentLoader(project_root=str(tmp_path))
        result = loader.get("test")
        assert result is not None
        assert result.name == "test"

    def test_get_missing(self, tmp_path: Path) -> None:
        loader = AgentLoader(project_root=str(tmp_path))
        assert loader.get("nonexistent") is None

    def test_list_agents(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "one.md").write_text("---\nname: one\n---\nOne.")
        (agents_dir / "two.md").write_text("---\nname: two\n---\nTwo.")

        loader = AgentLoader(project_root=str(tmp_path))
        agents = loader.list_agents()
        names = [a.name for a in agents]
        assert "one" in names
        assert "two" in names

    def test_find_by_trigger(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text(
            "---\nname: reviewer\ntriggers: [review, lint]\n---\nReview."
        )
        (agents_dir / "fixer.md").write_text(
            "---\nname: fixer\ntriggers: [fix, bug]\n---\nFix."
        )

        loader = AgentLoader(project_root=str(tmp_path))
        results = loader.find_by_trigger("review")
        assert len(results) == 1
        assert results[0].name == "reviewer"

    def test_find_by_trigger_case_insensitive(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "agent.md").write_text(
            "---\nname: agent\ntriggers: [Review]\n---\nReview agent."
        )

        loader = AgentLoader(project_root=str(tmp_path))
        results = loader.find_by_trigger("review")
        assert len(results) == 1

    def test_priority_project_over_user(self, tmp_path: Path) -> None:
        # User agents dir
        user_dir = tmp_path / "user_home" / ".chimera" / "agents"
        user_dir.mkdir(parents=True)
        (user_dir / "shared.md").write_text("---\nname: shared\n---\nUser version.")

        # Project agents dir
        project_dir = tmp_path / "project" / ".chimera" / "agents"
        project_dir.mkdir(parents=True)
        (project_dir / "shared.md").write_text("---\nname: shared\n---\nProject version.")

        loader = AgentLoader(project_root=str(tmp_path / "project"))
        # Override USER_DIR for testing
        loader.USER_DIR = str(user_dir)
        agents = loader.load_all()

        assert agents["shared"].source == "project"
        assert agents["shared"].system_prompt == "Project version."

    def test_malformed_files_skipped(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        # A valid agent
        (agents_dir / "good.md").write_text("---\nname: good\n---\nGood agent.")
        # A malformed file (binary junk)
        (agents_dir / "bad.md").write_bytes(b"\x00\x01\x02\x03")

        loader = AgentLoader(project_root=str(tmp_path))
        agents = loader.load_all()
        assert "good" in agents

    def test_empty_dir(self, tmp_path: Path) -> None:
        loader = AgentLoader(project_root=str(tmp_path))
        agents = loader.load_all()
        assert agents == {}


# ---------------------------------------------------------------------------
# AgentFactory
# ---------------------------------------------------------------------------

class TestAgentFactory:
    def test_create_resolves_tools(self) -> None:
        mock_provider = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "bash"

        factory = AgentFactory(
            provider=mock_provider,
            tool_registry={"bash": mock_tool},
        )

        agent_def = FileAgentDef(
            name="test",
            system_prompt="Test agent",
            tools=["bash", "nonexistent_tool"],
            loop="react",
            max_iterations=5,
        )

        agent = factory.create(agent_def)
        assert agent.name == "test"
        # Only the bash tool should be resolved (nonexistent is skipped)
        assert len(agent.tools) == 1

    def test_create_injects_skills(self) -> None:
        from chimera.config.skills import Skill

        mock_provider = MagicMock()
        mock_skill_registry = MagicMock()
        mock_skill_registry.get.return_value = Skill(
            name="test-skill", content="Skill content here"
        )

        factory = AgentFactory(
            provider=mock_provider,
            tool_registry={},
            skill_registry=mock_skill_registry,
        )

        agent_def = FileAgentDef(
            name="test",
            system_prompt="Base prompt",
            skills=["test-skill"],
        )

        agent = factory.create(agent_def)
        # The system prompt should contain the skill content
        assert "Skill content here" in agent.prompt.render()

    def test_create_with_no_skills(self) -> None:
        mock_provider = MagicMock()

        factory = AgentFactory(
            provider=mock_provider,
            tool_registry={},
        )

        agent_def = FileAgentDef(
            name="basic",
            system_prompt="Simple agent",
        )

        agent = factory.create(agent_def)
        assert agent.name == "basic"
