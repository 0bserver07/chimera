"""The policy packs are loadable by name: entry-point discovery + load.

Pins the shipped-in-the-package posture: `PluginManager.discover()` finds
the three packs through the ``chimera.plugins`` entry-point group declared
in pyproject, `load(name)` activates them, and the top-level
``chimera.plugins`` namespace re-exports the classes.
"""
from __future__ import annotations

import pytest

from chimera.plugins.base import BasePlugin
from chimera.plugins.manager import PluginManager
from chimera.plugins.registry import PluginExtensionRegistry

PACK_NAMES = {"plan-gate", "redactor", "delegate-spawner"}


@pytest.fixture(autouse=True)
def _clean_registry():
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


def test_discover_lists_the_bundled_packs():
    discovered = set(PluginManager().discover())
    assert PACK_NAMES <= discovered


def test_load_by_name_activates_and_unload_withdraws():
    manager = PluginManager()
    plugin = manager.load("plan-gate")
    assert plugin.name == "plan-gate"
    assert len(PluginExtensionRegistry.get_interceptors("tool_call")) == 1
    assert len(PluginExtensionRegistry.get_interceptors("context")) == 1

    manager.unload("plan-gate")
    assert PluginExtensionRegistry.get_interceptors("tool_call") == []
    assert PluginExtensionRegistry.get_interceptors("context") == []


def test_all_three_packs_load_together():
    manager = PluginManager()
    for name in sorted(PACK_NAMES):
        manager.load(name)

    assert set(manager.plugins) == PACK_NAMES
    # plan-gate's gate + delegate-spawner's rewrite share the tool_call seam.
    assert len(PluginExtensionRegistry.get_interceptors("tool_call")) == 2
    assert len(PluginExtensionRegistry.get_interceptors("provider_request")) == 1
    assert len(PluginExtensionRegistry.get_interceptors("tool_result")) == 1
    assert len(PluginExtensionRegistry.get_interceptors("context")) == 1

    for name in sorted(PACK_NAMES):
        manager.unload(name)
    for seam in ("provider_request", "tool_call", "tool_result", "context"):
        assert PluginExtensionRegistry.get_interceptors(seam) == []


def test_packs_are_reexported_from_the_plugins_namespace():
    from chimera.plugins import (
        DelegateSpawnerPlugin,
        PlanGatePlugin,
        RedactorPlugin,
    )

    for cls in (DelegateSpawnerPlugin, PlanGatePlugin, RedactorPlugin):
        assert issubclass(cls, BasePlugin)
