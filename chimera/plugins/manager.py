"""Plugin manager for discovering, loading, and unloading plugins."""
from __future__ import annotations

import importlib
import importlib.metadata
import sys
from typing import TYPE_CHECKING, Any

from chimera.plugins.base import BasePlugin, ComponentRegistry
from chimera.plugins.registry import PluginExtensionRegistry

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool


class PluginManager:
    """Discovers, loads, and manages Chimera plugins.

    Plugins are discovered via the ``chimera.plugins`` entry point group.
    Each entry point should resolve to a :class:`BasePlugin` subclass.

    Each plugin gets its own :class:`ComponentRegistry` so that
    aggregation methods can iterate per-plugin.

    Example:
        ```python
        manager = PluginManager()
        manager.load_all()
        print(manager.tools)  # Tools from all loaded plugins
        ```
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self._registries: dict[str, ComponentRegistry] = {}

    def discover(self) -> list[str]:
        """Discover available plugins via entry points.

        Returns:
            List of plugin entry point names.
        """
        group = importlib.metadata.entry_points(group="chimera.plugins")
        return [ep.name for ep in group]

    def _activate_atomic(self, plugin: BasePlugin, registry: ComponentRegistry) -> None:
        """Run ``plugin.activate`` with class-registry side effects owned + atomic.

        Activation runs inside a
        :meth:`~chimera.plugins.registry.PluginExtensionRegistry.owner_scope`,
        so every interceptor the plugin registers is attributed to this
        instance. If activation raises after registering part of its
        chains (a multi-seam plugin failing on a later seam, a
        ``ValueError`` from an invalid seam name), the partial
        registrations are rolled back before the error propagates: a
        failed activation leaves the interceptor registry exactly as it
        was, so no orphaned chain can outlive its owner.

        Args:
            plugin: The plugin being activated.
            registry: The per-instance component registry.

        Raises:
            BaseException: Whatever ``activate()`` raised, after rollback.
        """
        with PluginExtensionRegistry.owner_scope(plugin):
            try:
                plugin.activate(registry)
            except BaseException:
                PluginExtensionRegistry.unregister_owner(plugin)
                raise

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
        plugin: BasePlugin = plugin_cls()
        registry = ComponentRegistry()
        self._activate_atomic(plugin, registry)
        plugin._registry = registry  # type: ignore[attr-defined]  # dynamic attr for plugin bookkeeping
        self._plugins[plugin.name] = plugin
        self._registries[plugin.name] = registry
        return plugin

    def load_plugin(self, plugin: BasePlugin) -> None:
        """Load and activate an already-instantiated plugin.

        Activation is atomic with respect to the shipped interceptor
        registry (:meth:`_activate_atomic`): if ``activate()`` raises, any
        interceptor chains it registered first are rolled back and the
        plugin is not loaded — never half-applied, in either registry.

        Args:
            plugin: The plugin instance to load.

        Raises:
            ValueError: If a plugin with this name is already loaded.
        """
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' is already loaded")
        registry = ComponentRegistry()
        self._activate_atomic(plugin, registry)
        plugin._registry = registry  # type: ignore[attr-defined]  # dynamic attr for plugin bookkeeping
        self._plugins[plugin.name] = plugin
        self._registries[plugin.name] = registry

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

        Also drops the plugin's UI-surface contributions (slash commands,
        panels, status-line segments) from
        :class:`chimera.plugins.ui.UIExtensionRegistry`, matched by the
        provenance the plugin registered them with — so an unloaded
        plugin's commands genuinely disappear from the REPL and TUI
        catalogs on the next recompute (``/resync``) instead of lingering.
        Contributions registered without provenance cannot be attributed
        and are left in place.

        After ``deactivate()`` — even when it raises — every interceptor
        chain still attributed to this instance is withdrawn by owner
        (:meth:`PluginExtensionRegistry.unregister_owner`), so an unload
        always leaves zero chains owned by the plugin: a forgetful or
        dying ``deactivate()`` cannot leak policy into later turns.

        Args:
            name: The plugin name to unload.

        Raises:
            KeyError: If no plugin with this name is loaded.
        """
        if name not in self._plugins:
            raise KeyError(f"Plugin '{name}' is not loaded")
        plugin = self._plugins.pop(name)
        self._registries.pop(name, None)
        # Prune before deactivate: a raising deactivate() must not leave the
        # plugin's slash commands behind on the interactive surfaces.
        try:
            from chimera.plugins.ui import UIExtensionRegistry

            UIExtensionRegistry.unregister_plugin(name)
        except Exception:  # noqa: BLE001 - best-effort surface cleanup
            pass
        try:
            plugin.deactivate()
        finally:
            PluginExtensionRegistry.unregister_owner(plugin)

    def reload(self, name: str) -> BasePlugin:
        """Hot-reload a loaded plugin, picking up source changes.

        Deactivates the current instance, re-imports its defining module (so
        edits to the plugin's code take effect without restarting the process),
        then re-instantiates and re-activates it with a fresh
        :class:`ComponentRegistry`. Registration side effects the plugin
        performs in :meth:`~BasePlugin.activate` are re-run against the new
        registry, so reloaded tools/commands/hooks replace the old ones.

        A plain :meth:`unload` + :meth:`load` would *not* pick up code changes:
        Python caches the module in :data:`sys.modules`, so re-importing
        returns the stale class. This method calls :func:`importlib.reload` on
        the plugin's module first.

        Args:
            name: The plugin name to reload (its :attr:`BasePlugin.name`).

        Returns:
            The freshly re-instantiated plugin.

        Raises:
            KeyError: If no plugin with this name is loaded.
            RuntimeError: If the plugin's module cannot be reloaded or the
                plugin class is no longer present after reload.
        """
        if name not in self._plugins:
            raise KeyError(f"Plugin '{name}' is not loaded")
        old = self._plugins[name]
        module_name = type(old).__module__
        class_name = type(old).__name__

        module = sys.modules.get(module_name)
        if module is None:
            raise RuntimeError(
                f"Cannot reload plugin '{name}': module '{module_name}' "
                "is not imported"
            )

        # Deactivate + drop the old instance before swapping in fresh code.
        # Capture the spec first: a failed importlib.reload clobbers
        # module.__spec__ to None while searching, so the fallback must use the
        # spec we saved here, not re-read it off the module afterwards.
        original_spec = getattr(module, "__spec__", None)
        self.unload(name)
        try:
            reloaded = importlib.reload(module)
        except (ModuleNotFoundError, ImportError):
            # importlib.reload re-finds the module via sys.path finders, which
            # fails for plugins loaded from an arbitrary file (a file-location
            # spec whose directory is not importable — the dir-loader case).
            # Re-execute the module through its own loader instead, which
            # re-reads __file__ and so still picks up the source change.
            if original_spec is None or original_spec.loader is None:
                raise RuntimeError(
                    f"Cannot reload module '{module_name}' for plugin "
                    f"'{name}': no import spec/loader available"
                ) from None
            module.__spec__ = original_spec  # restore what reload nulled
            original_spec.loader.exec_module(module)
            reloaded = module
        except Exception as exc:  # noqa: BLE001 — surface as a clear reload failure
            raise RuntimeError(
                f"Failed to reload module '{module_name}' for plugin "
                f"'{name}': {exc}"
            ) from exc

        new_cls = getattr(reloaded, class_name, None)
        if new_cls is None:
            raise RuntimeError(
                f"Plugin class '{class_name}' no longer exists in "
                f"'{module_name}' after reload"
            )
        new_plugin: BasePlugin = new_cls()
        self.load_plugin(new_plugin)
        return new_plugin

    @property
    def tools(self) -> list[BaseTool]:
        """All tools registered by loaded plugins."""
        all_tools: list[BaseTool] = []
        for registry in self._registries.values():
            all_tools.extend(registry.tools)
        return all_tools

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        """All currently loaded plugins."""
        return dict(self._plugins)

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def get_all_commands(self) -> list[Any]:
        """Return commands from all loaded plugins."""
        commands: list[Any] = []
        for registry in self._registries.values():
            commands.extend(registry.commands)
        return commands

    def get_all_hooks(self, event: str | None = None) -> dict[str, list[Any]] | list[Any]:
        """Return hooks from all loaded plugins.

        If *event* is ``None``, returns a dict mapping event names to
        lists of matchers.  If *event* is given, returns just the list
        for that event (or an empty list).
        """
        hooks: dict[str, list[Any]] = {}
        for registry in self._registries.values():
            for ev, matchers in registry.hooks.items():
                if event is None or ev == event:
                    hooks.setdefault(ev, []).extend(matchers)
        if event is not None:
            return hooks.get(event, [])
        return hooks

    def get_all_skills(self) -> list[Any]:
        """Return skills from all loaded plugins."""
        skills: list[Any] = []
        for registry in self._registries.values():
            skills.extend(registry.skills)
        return skills
