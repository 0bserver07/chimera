"""Tests for chimera.plugins — extended plugin registry and directory loader."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from chimera.agents.config import AgentConfig
from chimera.config.skills import Skill
from chimera.plugins.base import BasePlugin, ComponentRegistry, Hook, MCPServerConfig
from chimera.plugins.dir_loader import DirectoryPluginLoader
from chimera.plugins.registry import PluginExtensionRegistry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the extension registry between tests."""
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


# ---------------------------------------------------------------------------
# PluginExtensionRegistry
# ---------------------------------------------------------------------------

class TestPluginExtensionRegistry:
    def test_register_and_get_agent(self) -> None:
        config = AgentConfig(name="test-agent", description="test", system_prompt="hello")
        PluginExtensionRegistry.register_agent("test-agent", config)
        result = PluginExtensionRegistry.get_agent("test-agent")
        assert result is config

    def test_get_agent_missing(self) -> None:
        assert PluginExtensionRegistry.get_agent("nonexistent") is None

    def test_get_all_agents(self) -> None:
        c1 = AgentConfig(name="a1", description="", system_prompt="")
        c2 = AgentConfig(name="a2", description="", system_prompt="")
        PluginExtensionRegistry.register_agent("a1", c1)
        PluginExtensionRegistry.register_agent("a2", c2)
        all_agents = PluginExtensionRegistry.get_all_agents()
        assert len(all_agents) == 2
        assert "a1" in all_agents
        assert "a2" in all_agents

    def test_register_strategy(self) -> None:
        class MyStrategy:
            pass

        PluginExtensionRegistry.register_strategy("my-strat", MyStrategy)
        assert PluginExtensionRegistry.get_strategy("my-strat") is MyStrategy

    def test_get_all_strategies(self) -> None:
        class S1:
            pass
        class S2:
            pass

        PluginExtensionRegistry.register_strategy("s1", S1)
        PluginExtensionRegistry.register_strategy("s2", S2)
        assert len(PluginExtensionRegistry.get_all_strategies()) == 2

    def test_register_middleware(self) -> None:
        class MW:
            pass

        PluginExtensionRegistry.register_middleware(MW)
        assert MW in PluginExtensionRegistry.get_all_middleware()

    def test_register_skill(self) -> None:
        skill = Skill(name="test-skill", content="some content")
        PluginExtensionRegistry.register_skill(skill)
        assert PluginExtensionRegistry._skills["test-skill"] is skill

    def test_register_mcp_server(self) -> None:
        config = MCPServerConfig(command=["node", "server.js"])
        PluginExtensionRegistry.register_mcp_server("my-server", config)
        servers = PluginExtensionRegistry.get_all_mcp_servers()
        assert "my-server" in servers
        assert servers["my-server"].command == ["node", "server.js"]

    def test_register_hook(self) -> None:
        hook = Hook(command="echo hello", event_type="tool_start")
        PluginExtensionRegistry.register_hook("tool_start", hook)
        hooks = PluginExtensionRegistry.get_hooks("tool_start")
        assert len(hooks) == 1
        assert hooks[0].command == "echo hello"

    def test_get_hooks_empty(self) -> None:
        assert PluginExtensionRegistry.get_hooks("nonexistent") == []

    def test_register_constraint(self) -> None:
        class TestConstraint:
            pass

        PluginExtensionRegistry.register_constraint("test", TestConstraint)
        assert PluginExtensionRegistry._constraints["test"] is TestConstraint

    def test_reset(self) -> None:
        config = AgentConfig(name="x", description="", system_prompt="")
        PluginExtensionRegistry.register_agent("x", config)
        PluginExtensionRegistry._reset()
        assert PluginExtensionRegistry.get_all_agents() == {}


# ---------------------------------------------------------------------------
# BasePlugin extension
# ---------------------------------------------------------------------------

class TestBasePluginExtension:
    def test_activate_calls_all_register_methods(self) -> None:
        calls: list[str] = []

        class TestPlugin(BasePlugin):
            @property
            def name(self) -> str:
                return "test"

            def register_tools(self, registry: ComponentRegistry) -> None:
                calls.append("tools")

            def register_agents(self, registry: ComponentRegistry) -> None:
                calls.append("agents")

            def register_strategies(self, registry: ComponentRegistry) -> None:
                calls.append("strategies")

            def register_hooks(self, registry: ComponentRegistry) -> None:
                calls.append("hooks")

        plugin = TestPlugin()
        plugin.activate(ComponentRegistry())
        assert "tools" in calls
        assert "agents" in calls
        assert "strategies" in calls
        assert "hooks" in calls


# ---------------------------------------------------------------------------
# DirectoryPluginLoader
# ---------------------------------------------------------------------------

class TestDirectoryPluginLoader:
    def _create_plugin_dir(self, base: Path) -> Path:
        """Create a plugin directory structure for testing."""
        plugin_dir = base / "my-plugin"
        plugin_dir.mkdir(parents=True)

        # manifest
        manifest = {"name": "my-plugin", "version": "1.0.0", "description": "Test plugin"}
        (plugin_dir / "plugin.json").write_text(json.dumps(manifest))

        # agents
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        agent_md = """\
---
name: test-agent
description: A test agent
tools: [bash]
loop: react
max_steps: 10
---
You are a test agent."""
        (agents_dir / "test-agent.md").write_text(agent_md)

        # mcp
        mcp_config = {
            "servers": {
                "my-server": {
                    "command": ["node", "index.js"],
                    "args": ["--port", "3000"],
                }
            }
        }
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_config))

        # hooks
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        hooks_data = [
            {"command": "echo test", "event_type": "tool_start", "timeout": 5}
        ]
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_data))

        return plugin_dir

    def test_load_plugin_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = self._create_plugin_dir(Path(tmpdir))
            loader = DirectoryPluginLoader()
            plugin = loader.load(plugin_dir)

            assert plugin.name == "my-plugin"
            assert plugin.version == "1.0.0"

    def test_activate_registers_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = self._create_plugin_dir(Path(tmpdir))
            loader = DirectoryPluginLoader()
            plugin = loader.load(plugin_dir)
            plugin.activate(ComponentRegistry())

            agent = PluginExtensionRegistry.get_agent("test-agent")
            assert agent is not None
            assert agent.name == "test-agent"
            assert agent.description == "A test agent"

    def test_activate_registers_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = self._create_plugin_dir(Path(tmpdir))
            loader = DirectoryPluginLoader()
            plugin = loader.load(plugin_dir)
            plugin.activate(ComponentRegistry())

            servers = PluginExtensionRegistry.get_all_mcp_servers()
            assert "my-server" in servers
            assert servers["my-server"].command == ["node", "index.js"]

    def test_activate_registers_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = self._create_plugin_dir(Path(tmpdir))
            loader = DirectoryPluginLoader()
            plugin = loader.load(plugin_dir)
            plugin.activate(ComponentRegistry())

            hooks = PluginExtensionRegistry.get_hooks("tool_start")
            assert len(hooks) == 1
            assert hooks[0].command == "echo test"

    def test_load_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir) / "bare-plugin"
            plugin_dir.mkdir()
            loader = DirectoryPluginLoader()
            plugin = loader.load(plugin_dir)
            assert plugin.name == "bare-plugin"

    def test_load_with_no_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_dir = Path(tmpdir) / "empty-plugin"
            plugin_dir.mkdir()
            (plugin_dir / "plugin.json").write_text('{"name": "empty"}')
            loader = DirectoryPluginLoader()
            plugin = loader.load(plugin_dir)
            plugin.activate(ComponentRegistry())
            # Should not crash
            assert PluginExtensionRegistry.get_all_agents() == {}


# ---------------------------------------------------------------------------
# Hook and MCPServerConfig dataclasses
# ---------------------------------------------------------------------------

class TestHook:
    def test_defaults(self) -> None:
        hook = Hook(command="echo hi", event_type="start")
        assert hook.working_dir is None
        assert hook.timeout == 30
        assert hook.env == {}


class TestMCPServerConfig:
    def test_defaults(self) -> None:
        config = MCPServerConfig(command=["node"])
        assert config.args == []
        assert config.env == {}
