"""T3.3 — PluginManager.reload() hot-reloads plugin source without restart."""

from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path

import pytest

from chimera.plugins.manager import PluginManager


def _write_plugin(path: Path, version: str) -> None:
    path.write_text(textwrap.dedent(f'''
        from chimera.plugins.base import BasePlugin

        class HotPlugin(BasePlugin):
            @property
            def name(self) -> str:
                return "hot"

            def register_skills(self, registry) -> None:
                registry.register_skill("{version}")
    '''))


def _load_module(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def test_reload_picks_up_source_changes(tmp_path: Path) -> None:
    mod_name = "chimera_test_hot_plugin"
    plugin_file = tmp_path / f"{mod_name}.py"
    _write_plugin(plugin_file, "v1")
    module = _load_module(mod_name, plugin_file)

    manager = PluginManager()
    try:
        manager.load_plugin(module.HotPlugin())
        assert manager.get_all_skills() == ["v1"]

        # Edit the plugin's source on disk, then hot-reload. Bump the mtime
        # into the future so the recompile isn't skipped by a cached .pyc with
        # a same-second timestamp (test rewrites happen within one second).
        _write_plugin(plugin_file, "v2")
        future = plugin_file.stat().st_mtime + 10
        os.utime(plugin_file, (future, future))
        importlib.invalidate_caches()
        new_plugin = manager.reload("hot")

        assert new_plugin.name == "hot"
        # The new code took effect — a plain unload+load would still see "v1".
        assert manager.get_all_skills() == ["v2"]
    finally:
        sys.modules.pop(mod_name, None)


def test_reload_unloaded_plugin_raises() -> None:
    manager = PluginManager()
    with pytest.raises(KeyError, match="not loaded"):
        manager.reload("nope")
