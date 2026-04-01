"""Tests for PluginManager aggregation methods (get_all_commands, get_all_hooks, get_all_skills)."""
from __future__ import annotations

import pytest

from chimera.plugins.base import BasePlugin, ComponentRegistry
from chimera.plugins.manager import PluginManager


# ---------------------------------------------------------------------------
# Test plugins
# ---------------------------------------------------------------------------


class AlphaPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "alpha"

    def activate(self, registry: ComponentRegistry) -> None:
        registry.register_command({"name": "alpha-cmd"})
        registry.register_hook("PreToolUse", {"pattern": "bash*"})
        registry.register_skill({"name": "alpha-skill"})


class BetaPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "beta"

    def activate(self, registry: ComponentRegistry) -> None:
        registry.register_command({"name": "beta-cmd-1"})
        registry.register_command({"name": "beta-cmd-2"})
        registry.register_hook("PreToolUse", {"pattern": "write*"})
        registry.register_hook("PostToolUse", {"pattern": "*"})
        registry.register_skill({"name": "beta-skill"})


class EmptyPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "empty"

    def activate(self, registry: ComponentRegistry) -> None:
        pass  # Registers nothing


# ---------------------------------------------------------------------------
# Tests: get_all_commands
# ---------------------------------------------------------------------------


class TestGetAllCommands:
    def test_no_plugins_returns_empty(self):
        manager = PluginManager()
        assert manager.get_all_commands() == []

    def test_single_plugin_commands(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        commands = manager.get_all_commands()
        assert len(commands) == 1
        assert commands[0]["name"] == "alpha-cmd"

    def test_multiple_plugins_commands(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        manager.load_plugin(BetaPlugin())
        commands = manager.get_all_commands()
        assert len(commands) == 3
        names = {c["name"] for c in commands}
        assert names == {"alpha-cmd", "beta-cmd-1", "beta-cmd-2"}

    def test_empty_plugin_no_commands(self):
        manager = PluginManager()
        manager.load_plugin(EmptyPlugin())
        assert manager.get_all_commands() == []


# ---------------------------------------------------------------------------
# Tests: get_all_hooks
# ---------------------------------------------------------------------------


class TestGetAllHooks:
    def test_no_plugins_returns_empty(self):
        manager = PluginManager()
        assert manager.get_all_hooks() == {}

    def test_single_plugin_hooks(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        hooks = manager.get_all_hooks()
        assert "PreToolUse" in hooks
        assert len(hooks["PreToolUse"]) == 1

    def test_multiple_plugins_hooks_merged(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        manager.load_plugin(BetaPlugin())
        hooks = manager.get_all_hooks()
        # Alpha registers 1 PreToolUse, Beta registers 1 PreToolUse + 1 PostToolUse
        assert len(hooks["PreToolUse"]) == 2
        assert len(hooks["PostToolUse"]) == 1

    def test_filter_by_event(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        manager.load_plugin(BetaPlugin())
        pre_hooks = manager.get_all_hooks(event="PreToolUse")
        assert isinstance(pre_hooks, list)
        assert len(pre_hooks) == 2

    def test_filter_by_nonexistent_event(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        result = manager.get_all_hooks(event="NoSuchEvent")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: get_all_skills
# ---------------------------------------------------------------------------


class TestGetAllSkills:
    def test_no_plugins_returns_empty(self):
        manager = PluginManager()
        assert manager.get_all_skills() == []

    def test_single_plugin_skills(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        skills = manager.get_all_skills()
        assert len(skills) == 1
        assert skills[0]["name"] == "alpha-skill"

    def test_multiple_plugins_skills(self):
        manager = PluginManager()
        manager.load_plugin(AlphaPlugin())
        manager.load_plugin(BetaPlugin())
        skills = manager.get_all_skills()
        assert len(skills) == 2
        names = {s["name"] for s in skills}
        assert names == {"alpha-skill", "beta-skill"}

    def test_empty_plugin_no_skills(self):
        manager = PluginManager()
        manager.load_plugin(EmptyPlugin())
        assert manager.get_all_skills() == []
