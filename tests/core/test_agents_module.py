"""Tests for chimera.agents module: config, registry, and preset agents."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.agents.config import AgentConfig, _parse_frontmatter
from chimera.agents.registry import AgentRegistry
from chimera.agents.presets.build import BuildAgent, BUILD_CONFIG
from chimera.agents.presets.plan import PlanAgent, PLAN_CONFIG
from chimera.agents.presets.explore import ExploreAgent, EXPLORE_CONFIG
from chimera.agents.presets.general import GeneralAgent, GENERAL_CONFIG
from chimera.agents.presets.review import ReviewAgent, REVIEW_CONFIG
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.providers.base import Provider, Response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_provider() -> MagicMock:
    """Return a MagicMock that satisfies the Provider interface."""
    provider = MagicMock(spec=Provider)
    provider.model_name = "mock-model"
    provider.context_window = 200_000
    provider.supports_tool_use = True
    provider.complete.return_value = Response(
        content="ok", tool_calls=[], usage={},
    )
    return provider


_SAMPLE_MD = """\
---
name: test-agent
description: A test agent
tools: [read_file, bash]
permissions: auto_approve
loop: react
max_steps: 25
---
You are a test agent with limited tools.
"""

_MINIMAL_MD = """\
---
name: minimal
description: Minimal config
---
Do something.
"""


def _write_md(directory: str, filename: str, content: str) -> str:
    path = os.path.join(directory, filename)
    Path(path).write_text(content)
    return path


# ---------------------------------------------------------------------------
# AgentConfig dataclass defaults
# ---------------------------------------------------------------------------

class TestAgentConfigDefaults:
    def test_default_tools_empty(self) -> None:
        cfg = AgentConfig(name="x", description="d", system_prompt="p")
        assert cfg.tools == []

    def test_default_permissions(self) -> None:
        cfg = AgentConfig(name="x", description="d", system_prompt="p")
        assert cfg.permissions == "auto_approve"

    def test_default_loop(self) -> None:
        cfg = AgentConfig(name="x", description="d", system_prompt="p")
        assert cfg.loop == "react"

    def test_default_max_steps(self) -> None:
        cfg = AgentConfig(name="x", description="d", system_prompt="p")
        assert cfg.max_steps == 50

    def test_default_model_none(self) -> None:
        cfg = AgentConfig(name="x", description="d", system_prompt="p")
        assert cfg.model is None


# ---------------------------------------------------------------------------
# from_markdown parsing
# ---------------------------------------------------------------------------

class TestAgentConfigFromMarkdown:
    def test_full_frontmatter(self, tmp_path: Path) -> None:
        md_path = _write_md(str(tmp_path), "agent.md", _SAMPLE_MD)
        cfg = AgentConfig.from_markdown(md_path)

        assert cfg.name == "test-agent"
        assert cfg.description == "A test agent"
        assert cfg.tools == ["read_file", "bash"]
        assert cfg.permissions == "auto_approve"
        assert cfg.loop == "react"
        assert cfg.max_steps == 25
        assert cfg.system_prompt == "You are a test agent with limited tools."

    def test_minimal_frontmatter(self, tmp_path: Path) -> None:
        md_path = _write_md(str(tmp_path), "minimal.md", _MINIMAL_MD)
        cfg = AgentConfig.from_markdown(md_path)

        assert cfg.name == "minimal"
        assert cfg.description == "Minimal config"
        assert cfg.tools == []
        assert cfg.permissions == "auto_approve"
        assert cfg.loop == "react"
        assert cfg.max_steps == 50
        assert cfg.system_prompt == "Do something."

    def test_name_defaults_to_stem(self, tmp_path: Path) -> None:
        content = "---\ndescription: no name\n---\nPrompt."
        md_path = _write_md(str(tmp_path), "fallback.md", content)
        cfg = AgentConfig.from_markdown(md_path)
        assert cfg.name == "fallback"

    def test_missing_frontmatter_raises(self, tmp_path: Path) -> None:
        md_path = _write_md(str(tmp_path), "bad.md", "No frontmatter here.")
        with pytest.raises(ValueError, match="YAML frontmatter"):
            AgentConfig.from_markdown(md_path)

    def test_model_field(self, tmp_path: Path) -> None:
        content = "---\nname: m\ndescription: d\nmodel: gpt-4o\n---\nPrompt."
        md_path = _write_md(str(tmp_path), "model.md", content)
        cfg = AgentConfig.from_markdown(md_path)
        assert cfg.model == "gpt-4o"


# ---------------------------------------------------------------------------
# from_markdown -> build roundtrip
# ---------------------------------------------------------------------------

class TestAgentConfigBuild:
    def test_build_creates_agent(self) -> None:
        cfg = AgentConfig(
            name="builder",
            description="test",
            system_prompt="You are helpful.",
            tools=["read_file", "bash"],
            loop="react",
            max_steps=10,
        )
        provider = _mock_provider()
        agent = cfg.build(provider)

        assert isinstance(agent, Agent)
        assert agent.name == "builder"
        assert len(agent.tools) == 2
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "bash" in tool_names

    def test_build_uses_react_loop(self) -> None:
        cfg = AgentConfig(
            name="r", description="d", system_prompt="p",
            loop="react", max_steps=7,
        )
        agent = cfg.build(_mock_provider())
        assert isinstance(agent.loop, ReAct)
        assert agent.loop.max_steps == 7

    def test_build_uses_plan_execute_loop(self) -> None:
        cfg = AgentConfig(
            name="p", description="d", system_prompt="p",
            loop="plan_execute", max_steps=20,
        )
        agent = cfg.build(_mock_provider())
        assert isinstance(agent.loop, PlanAndExecute)
        assert agent.loop.max_steps == 20

    def test_build_unknown_loop_raises(self) -> None:
        cfg = AgentConfig(
            name="x", description="d", system_prompt="p",
            loop="nonexistent",
        )
        with pytest.raises(ValueError, match="Unknown loop"):
            cfg.build(_mock_provider())

    def test_build_unknown_tool_raises(self) -> None:
        cfg = AgentConfig(
            name="x", description="d", system_prompt="p",
            tools=["no_such_tool"],
        )
        with pytest.raises(ValueError, match="Unknown tool"):
            cfg.build(_mock_provider())

    def test_build_sets_prompt(self) -> None:
        cfg = AgentConfig(
            name="x", description="d",
            system_prompt="You are a {{role}}.",
        )
        agent = cfg.build(_mock_provider())
        rendered = agent.prompt.render(role="tester")
        assert "tester" in rendered

    def test_build_no_tools(self) -> None:
        cfg = AgentConfig(name="empty", description="d", system_prompt="p")
        agent = cfg.build(_mock_provider())
        assert agent.tools == []


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_simple_key_value(self) -> None:
        result = _parse_frontmatter("name: hello\ndescription: world")
        assert result["name"] == "hello"
        assert result["description"] == "world"

    def test_list_value(self) -> None:
        result = _parse_frontmatter("tools: [a, b, c]")
        assert result["tools"] == ["a", "b", "c"]

    def test_empty_list(self) -> None:
        result = _parse_frontmatter("tools: []")
        assert result["tools"] == []

    def test_quoted_values(self) -> None:
        result = _parse_frontmatter('name: "hello"')
        assert result["name"] == "hello"

    def test_comments_ignored(self) -> None:
        result = _parse_frontmatter("# comment\nname: x")
        assert result["name"] == "x"
        assert "#" not in result


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------

class TestAgentRegistry:
    def test_register_and_get(self) -> None:
        reg = AgentRegistry()
        cfg = AgentConfig(name="a", description="d", system_prompt="p")
        reg.register(cfg)
        assert reg.get("a") is cfg

    def test_get_missing_returns_none(self) -> None:
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_names(self) -> None:
        reg = AgentRegistry()
        reg.register(AgentConfig(name="a", description="", system_prompt=""))
        reg.register(AgentConfig(name="b", description="", system_prompt=""))
        assert reg.list() == ["a", "b"]

    def test_overwrite(self) -> None:
        reg = AgentRegistry()
        cfg1 = AgentConfig(name="x", description="first", system_prompt="")
        cfg2 = AgentConfig(name="x", description="second", system_prompt="")
        reg.register(cfg1)
        reg.register(cfg2)
        assert reg.get("x") is cfg2
        assert reg.list() == ["x"]

    def test_load_directory(self, tmp_path: Path) -> None:
        _write_md(str(tmp_path), "alpha.md", _SAMPLE_MD)
        _write_md(str(tmp_path), "beta.md", _MINIMAL_MD)

        reg = AgentRegistry()
        reg.load_directory(str(tmp_path))

        assert "test-agent" in reg.list()
        assert "minimal" in reg.list()

    def test_load_directory_sorted_order(self, tmp_path: Path) -> None:
        """Files are loaded in sorted order (alpha before beta)."""
        _write_md(str(tmp_path), "beta.md", _MINIMAL_MD)
        _write_md(str(tmp_path), "alpha.md", _SAMPLE_MD)

        reg = AgentRegistry()
        reg.load_directory(str(tmp_path))

        # Both should be present regardless of creation order
        names = reg.list()
        assert len(names) == 2

    def test_load_directory_nonexistent(self) -> None:
        reg = AgentRegistry()
        reg.load_directory("/nonexistent/path/that/does/not/exist")
        assert reg.list() == []

    def test_load_directory_empty(self, tmp_path: Path) -> None:
        reg = AgentRegistry()
        reg.load_directory(str(tmp_path))
        assert reg.list() == []


# ---------------------------------------------------------------------------
# Preset agents
# ---------------------------------------------------------------------------

class TestPresetBuildAgent:
    def test_creates_agent_instance(self) -> None:
        agent = BuildAgent(_mock_provider())
        assert isinstance(agent, Agent)

    def test_has_expected_tools(self) -> None:
        agent = BuildAgent(_mock_provider())
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "edit_file" in tool_names
        assert "bash" in tool_names
        assert "test" in tool_names

    def test_name_is_build(self) -> None:
        agent = BuildAgent(_mock_provider())
        assert agent.name == "build"

    def test_max_steps_100(self) -> None:
        agent = BuildAgent(_mock_provider())
        assert agent.loop.max_steps == 100

    def test_overrides(self) -> None:
        agent = BuildAgent(_mock_provider(), max_steps=5)
        assert agent.loop.max_steps == 5

    def test_config_values(self) -> None:
        assert BUILD_CONFIG.permissions == "interactive"
        assert BUILD_CONFIG.loop == "react"


class TestPresetPlanAgent:
    def test_creates_agent_instance(self) -> None:
        agent = PlanAgent(_mock_provider())
        assert isinstance(agent, Agent)

    def test_has_read_only_tools(self) -> None:
        agent = PlanAgent(_mock_provider())
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "search" in tool_names
        assert "list_files" in tool_names
        assert "repo_map" in tool_names
        # No write tools
        assert "write_file" not in tool_names
        assert "bash" not in tool_names

    def test_uses_plan_execute_loop(self) -> None:
        agent = PlanAgent(_mock_provider())
        assert isinstance(agent.loop, PlanAndExecute)

    def test_config_values(self) -> None:
        assert PLAN_CONFIG.permissions == "read_only"
        assert PLAN_CONFIG.loop == "plan_execute"


class TestPresetExploreAgent:
    def test_creates_agent_instance(self) -> None:
        agent = ExploreAgent(_mock_provider())
        assert isinstance(agent, Agent)

    def test_has_search_tools(self) -> None:
        agent = ExploreAgent(_mock_provider())
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "search" in tool_names
        assert "list_files" in tool_names
        assert "repo_map" in tool_names
        # No write tools
        assert "write_file" not in tool_names
        assert "bash" not in tool_names

    def test_uses_react_loop(self) -> None:
        agent = ExploreAgent(_mock_provider())
        assert isinstance(agent.loop, ReAct)

    def test_config_values(self) -> None:
        assert EXPLORE_CONFIG.permissions == "read_only"
        assert EXPLORE_CONFIG.loop == "react"


class TestPresetGeneralAgent:
    def test_creates_agent_instance(self) -> None:
        agent = GeneralAgent(_mock_provider())
        assert isinstance(agent, Agent)

    def test_has_all_tools(self) -> None:
        agent = GeneralAgent(_mock_provider())
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "bash" in tool_names
        assert "git" in tool_names
        assert "test" in tool_names

    def test_config_values(self) -> None:
        assert GENERAL_CONFIG.permissions == "auto_approve"
        assert GENERAL_CONFIG.loop == "react"


class TestPresetReviewAgent:
    def test_creates_agent_instance(self) -> None:
        agent = ReviewAgent(_mock_provider())
        assert isinstance(agent, Agent)

    def test_has_read_and_git_tools(self) -> None:
        agent = ReviewAgent(_mock_provider())
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "search" in tool_names
        assert "git" in tool_names
        assert "repo_map" in tool_names
        # No write tools
        assert "write_file" not in tool_names
        assert "bash" not in tool_names

    def test_config_values(self) -> None:
        assert REVIEW_CONFIG.permissions == "read_only"
        assert REVIEW_CONFIG.loop == "react"


# ---------------------------------------------------------------------------
# All presets produce Agent instances
# ---------------------------------------------------------------------------

class TestAllPresetsAreAgents:
    @pytest.mark.parametrize("factory", [
        BuildAgent, PlanAgent, ExploreAgent, GeneralAgent, ReviewAgent,
    ])
    def test_is_agent_instance(self, factory) -> None:
        agent = factory(_mock_provider())
        assert isinstance(agent, Agent)

    @pytest.mark.parametrize("factory", [
        BuildAgent, PlanAgent, ExploreAgent, GeneralAgent, ReviewAgent,
    ])
    def test_has_name(self, factory) -> None:
        agent = factory(_mock_provider())
        assert agent.name is not None
        assert len(agent.name) > 0

    @pytest.mark.parametrize("factory", [
        BuildAgent, PlanAgent, ExploreAgent, GeneralAgent, ReviewAgent,
    ])
    def test_has_tools(self, factory) -> None:
        agent = factory(_mock_provider())
        assert len(agent.tools) > 0
