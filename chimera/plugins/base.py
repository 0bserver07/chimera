"""Plugin base classes and registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool


class PluginRegistry:
    """Registry passed to plugins during activation.

    Plugins use this to register tools, loop classes, and provider classes
    that they provide.
    """

    def __init__(self) -> None:
        self._tools: list[BaseTool] = []
        self._loops: dict[str, type] = {}
        self._providers: dict[str, type] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """Register a tool provided by a plugin.

        Args:
            tool: A BaseTool instance to add.
        """
        self._tools.append(tool)

    def register_loop(self, name: str, loop_class: type) -> None:
        """Register a loop class provided by a plugin.

        Args:
            name: Unique name for the loop.
            loop_class: The loop class to register.
        """
        self._loops[name] = loop_class

    def register_provider(self, name: str, provider_class: type) -> None:
        """Register a provider class provided by a plugin.

        Args:
            name: Unique name for the provider.
            provider_class: The provider class to register.
        """
        self._providers[name] = provider_class

    @property
    def tools(self) -> list[BaseTool]:
        """All registered tools."""
        return list(self._tools)

    @property
    def loops(self) -> dict[str, type]:
        """All registered loops."""
        return dict(self._loops)

    @property
    def providers(self) -> dict[str, type]:
        """All registered providers."""
        return dict(self._providers)


class BasePlugin(ABC):
    """Abstract base class for Chimera plugins.

    Subclass this and implement :meth:`activate` to register tools,
    loops, or providers with the plugin registry.

    Example:
        ```python
        class MyPlugin(BasePlugin):
            name = "my-plugin"
            version = "1.0.0"

            def activate(self, registry: PluginRegistry) -> None:
                registry.register_tool(MyCustomTool())
        ```
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name."""

    version: str = "0.1.0"

    def activate(self, registry: PluginRegistry) -> None:
        """Called when the plugin is loaded.

        Args:
            registry: Use to register tools, loops, and providers.
        """

    def deactivate(self) -> None:
        """Called when the plugin is unloaded."""
