"""Tests for the plugin system."""
from __future__ import annotations

import pytest

from chimera.plugins.base import BasePlugin, ComponentRegistry
from chimera.plugins.manager import PluginManager
from chimera.core.tool import BaseTool
from chimera.types import ToolResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class DummyTool(BaseTool):
    name = "dummy"
    description = "A dummy tool for testing."
    parameters: dict = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="ok")


class ConcretePlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "test-plugin"

    version = "1.0.0"

    def activate(self, registry: ComponentRegistry) -> None:
        registry.register_tool(DummyTool())


class SecondPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "second-plugin"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_base_plugin_is_abstract():
    """Instantiating BasePlugin directly raises TypeError."""
    with pytest.raises(TypeError):
        BasePlugin()  # type: ignore[abstract]


def test_concrete_plugin_activate():
    """ConcretePlugin.activate registers DummyTool in the registry."""
    registry = ComponentRegistry()
    plugin = ConcretePlugin()
    plugin.activate(registry)
    assert len(registry.tools) == 1
    assert registry.tools[0].name == "dummy"


def test_plugin_manager_load():
    """load_plugin loads a concrete plugin and exposes its tools."""
    manager = PluginManager()
    manager.load_plugin(ConcretePlugin())
    assert len(manager.tools) == 1
    assert manager.tools[0].name == "dummy"


def test_plugin_manager_unload():
    """Unloading a plugin removes it from the plugins dict."""
    deactivated = []

    class TrackingPlugin(BasePlugin):
        @property
        def name(self) -> str:
            return "tracking-plugin"

        def deactivate(self) -> None:
            deactivated.append(True)

    manager = PluginManager()
    manager.load_plugin(TrackingPlugin())
    assert "tracking-plugin" in manager.plugins
    manager.unload("tracking-plugin")
    assert "tracking-plugin" not in manager.plugins
    assert deactivated == [True]


def test_plugin_registry_register_tool():
    """register_tool adds a tool to the registry's tools list."""
    registry = ComponentRegistry()
    registry.register_tool(DummyTool())
    assert len(registry.tools) == 1
    assert registry.tools[0].name == "dummy"


def test_plugin_registry_register_loop():
    """register_loop adds a loop class to the registry's loops dict."""
    registry = ComponentRegistry()
    registry.register_loop("my-loop", object)
    assert "my-loop" in registry.loops
    assert registry.loops["my-loop"] is object


def test_plugin_registry_register_provider():
    """register_provider adds a provider class to the registry's providers dict."""
    registry = ComponentRegistry()
    registry.register_provider("my-provider", object)
    assert "my-provider" in registry.providers
    assert registry.providers["my-provider"] is object


def test_discover_no_plugins():
    """discover() returns a list (empty when no chimera.plugins entry points exist)."""
    manager = PluginManager()
    result = manager.discover()
    assert isinstance(result, list)


def test_load_all():
    """load_all returns empty list when there are no entry points."""
    manager = PluginManager()
    loaded = manager.load_all()
    # In the test environment there are no chimera.plugins entry points
    assert isinstance(loaded, list)


def test_duplicate_plugin_name():
    """Loading the same plugin name twice raises ValueError."""
    manager = PluginManager()
    manager.load_plugin(ConcretePlugin())
    with pytest.raises(ValueError, match="already loaded"):
        manager.load_plugin(ConcretePlugin())
