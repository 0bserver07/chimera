"""Plugin manager for discovering, loading, and unloading plugins."""
from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

from chimera.plugins.base import BasePlugin, PluginRegistry

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool


class PluginManager:
    """Discovers, loads, and manages Chimera plugins.

    Plugins are discovered via the ``chimera.plugins`` entry point group.
    Each entry point should resolve to a :class:`BasePlugin` subclass.

    Example:
        ```python
        manager = PluginManager()
        manager.load_all()
        print(manager.tools)  # Tools from all loaded plugins
        ```
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self._registry = PluginRegistry()

    def discover(self) -> list[str]:
        """Discover available plugins via entry points.

        Returns:
            List of plugin entry point names.
        """
        group = importlib.metadata.entry_points(group="chimera.plugins")
        return [ep.name for ep in group]

    def load(self, name: str) -> BasePlugin:
        """Load and activate a plugin by entry point name.

        Args:
            name: The entry point name of the plugin.

        Returns:
            The loaded plugin instance.

        Raises:
            ValueError: If a plugin with this name is already loaded.
            KeyError: If no entry point with this name exists.
        """
        if name in self._plugins:
            raise ValueError(f"Plugin '{name}' is already loaded")
        matches = list(importlib.metadata.entry_points(group="chimera.plugins", name=name))
        if not matches:
            raise KeyError(f"No plugin entry point named '{name}'")
        plugin_cls = matches[0].load()
        plugin = plugin_cls()
        plugin.activate(self._registry)
        self._plugins[plugin.name] = plugin
        return plugin

    def load_plugin(self, plugin: BasePlugin) -> None:
        """Load and activate an already-instantiated plugin.

        Args:
            plugin: The plugin instance to load.

        Raises:
            ValueError: If a plugin with this name is already loaded.
        """
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' is already loaded")
        plugin.activate(self._registry)
        self._plugins[plugin.name] = plugin

    def load_all(self) -> list[BasePlugin]:
        """Discover and load all available plugins.

        Returns:
            List of loaded plugin instances.
        """
        loaded = []
        for name in self.discover():
            plugin = self.load(name)
            loaded.append(plugin)
        return loaded

    def unload(self, name: str) -> None:
        """Deactivate and remove a loaded plugin.

        Args:
            name: The plugin name to unload.

        Raises:
            KeyError: If no plugin with this name is loaded.
        """
        if name not in self._plugins:
            raise KeyError(f"Plugin '{name}' is not loaded")
        plugin = self._plugins.pop(name)
        plugin.deactivate()

    @property
    def tools(self) -> list[BaseTool]:
        """All tools registered by loaded plugins."""
        return self._registry.tools

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        """All currently loaded plugins."""
        return dict(self._plugins)
