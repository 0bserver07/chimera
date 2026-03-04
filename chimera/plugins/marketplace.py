"""Plugin marketplace for discovery, search, and installation."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PluginInfo:
    """Metadata describing a plugin available in the marketplace.

    Attributes:
        name: Unique plugin name.
        version: Version string (e.g. "1.0.0").
        description: Human-readable description of the plugin.
        author: Plugin author name or organization.
        url: URL for the plugin's homepage or repository.
        tags: Categorization tags for discovery.
        downloads: Number of times the plugin has been downloaded.
        rating: Average user rating on a 0-5 star scale.
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    url: str = ""
    tags: list[str] = field(default_factory=list)
    downloads: int = 0
    rating: float = 0.0


class PluginRegistry:
    """Registry of available plugins with search and filtering.

    Stores :class:`PluginInfo` entries and provides methods to search,
    filter, and retrieve plugin metadata.

    Example:
        ```python
        registry = PluginRegistry()
        registry.register(PluginInfo(name="my-tool", version="1.0.0"))
        results = registry.search("tool")
        ```
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginInfo] = {}

    def register(self, info: PluginInfo) -> None:
        """Register a plugin in the registry.

        Args:
            info: Plugin metadata to register.
        """
        self._plugins[info.name] = info

    def unregister(self, name: str) -> None:
        """Remove a plugin from the registry.

        Args:
            name: Name of the plugin to remove.
        """
        self._plugins.pop(name, None)

    def get(self, name: str) -> PluginInfo | None:
        """Look up a plugin by name.

        Args:
            name: Plugin name to look up.

        Returns:
            The plugin info if found, otherwise None.
        """
        return self._plugins.get(name)

    def search(self, query: str) -> list[PluginInfo]:
        """Search plugins by name, description, or tags.

        Performs a case-insensitive substring match against the plugin
        name, description, and tag values.

        Args:
            query: Search string.

        Returns:
            List of matching plugins.
        """
        query_lower = query.lower()
        results: list[PluginInfo] = []
        for info in self._plugins.values():
            if (
                query_lower in info.name.lower()
                or query_lower in info.description.lower()
                or any(query_lower in tag.lower() for tag in info.tags)
            ):
                results.append(info)
        return results

    def list_all(self) -> list[PluginInfo]:
        """Return all registered plugins.

        Returns:
            List of all plugin info entries.
        """
        return list(self._plugins.values())

    def by_tag(self, tag: str) -> list[PluginInfo]:
        """Filter plugins by tag.

        Performs a case-insensitive exact match on tag values.

        Args:
            tag: Tag to filter by.

        Returns:
            List of plugins that have the given tag.
        """
        tag_lower = tag.lower()
        return [
            info
            for info in self._plugins.values()
            if any(tag_lower == t.lower() for t in info.tags)
        ]

    def top_rated(self, limit: int = 10) -> list[PluginInfo]:
        """Return the highest-rated plugins.

        Args:
            limit: Maximum number of results to return.

        Returns:
            List of plugins sorted by rating in descending order.
        """
        sorted_plugins = sorted(
            self._plugins.values(), key=lambda p: p.rating, reverse=True
        )
        return sorted_plugins[:limit]


class Marketplace:
    """Plugin marketplace for publishing, searching, and installing plugins.

    Wraps a :class:`PluginRegistry` and adds install/uninstall tracking.

    Example:
        ```python
        mp = Marketplace()
        mp.publish(PluginInfo(name="my-tool", version="1.0.0"))
        mp.install("my-tool")
        assert mp.is_installed("my-tool")
        ```
    """

    def __init__(self, registry: PluginRegistry | None = None) -> None:
        self._registry = registry or PluginRegistry()
        self._installed: set[str] = set()

    @property
    def registry(self) -> PluginRegistry:
        """Access the underlying plugin registry."""
        return self._registry

    def publish(self, info: PluginInfo) -> None:
        """Publish a plugin to the marketplace.

        Args:
            info: Plugin metadata to publish.
        """
        self._registry.register(info)

    def search(self, query: str) -> list[PluginInfo]:
        """Search for plugins in the marketplace.

        Args:
            query: Search string.

        Returns:
            List of matching plugins.
        """
        return self._registry.search(query)

    def install(self, name: str) -> bool:
        """Mark a plugin as installed.

        Args:
            name: Name of the plugin to install.

        Returns:
            True if the plugin was found and installed, False otherwise.
        """
        info = self._registry.get(name)
        if info is None:
            return False
        self._installed.add(name)
        return True

    def uninstall(self, name: str) -> bool:
        """Mark a plugin as uninstalled.

        Args:
            name: Name of the plugin to uninstall.

        Returns:
            True if the plugin was installed and is now removed, False otherwise.
        """
        if name not in self._installed:
            return False
        self._installed.discard(name)
        return True

    @property
    def installed(self) -> list[str]:
        """List of installed plugin names."""
        return sorted(self._installed)

    def is_installed(self, name: str) -> bool:
        """Check whether a plugin is installed.

        Args:
            name: Plugin name to check.

        Returns:
            True if the plugin is currently installed.
        """
        return name in self._installed
