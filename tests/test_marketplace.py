"""Tests for the plugin marketplace."""
from __future__ import annotations

from chimera.plugins.marketplace import Marketplace, PluginInfo, MarketplaceRegistry


# ---------------------------------------------------------------------------
# PluginInfo tests
# ---------------------------------------------------------------------------


def test_plugin_info_defaults():
    """PluginInfo uses sensible defaults for optional fields."""
    info = PluginInfo(name="my-plugin", version="1.0.0")
    assert info.name == "my-plugin"
    assert info.version == "1.0.0"
    assert info.description == ""
    assert info.author == ""
    assert info.url == ""
    assert info.tags == []
    assert info.downloads == 0
    assert info.rating == 0.0


def test_plugin_info_with_fields():
    """PluginInfo stores all provided field values."""
    info = PluginInfo(
        name="advanced-tool",
        version="2.3.1",
        description="An advanced tool plugin",
        author="Alice",
        url="https://example.com/advanced-tool",
        tags=["tool", "advanced"],
        downloads=500,
        rating=4.5,
    )
    assert info.name == "advanced-tool"
    assert info.version == "2.3.1"
    assert info.description == "An advanced tool plugin"
    assert info.author == "Alice"
    assert info.url == "https://example.com/advanced-tool"
    assert info.tags == ["tool", "advanced"]
    assert info.downloads == 500
    assert info.rating == 4.5


# ---------------------------------------------------------------------------
# MarketplaceRegistry tests
# ---------------------------------------------------------------------------


def test_registry_register_and_get():
    """register() adds a plugin and get() retrieves it by name."""
    registry = MarketplaceRegistry()
    info = PluginInfo(name="foo", version="1.0.0")
    registry.register(info)
    assert registry.get("foo") is info
    assert registry.get("nonexistent") is None


def test_registry_unregister():
    """unregister() removes a plugin from the registry."""
    registry = MarketplaceRegistry()
    info = PluginInfo(name="bar", version="1.0.0")
    registry.register(info)
    registry.unregister("bar")
    assert registry.get("bar") is None
    # Unregistering a non-existent plugin does not raise
    registry.unregister("nonexistent")


def test_registry_search_by_name():
    """search() matches against plugin names (case-insensitive)."""
    registry = MarketplaceRegistry()
    registry.register(PluginInfo(name="code-formatter", version="1.0.0"))
    registry.register(PluginInfo(name="linter", version="1.0.0"))
    results = registry.search("CODE")
    assert len(results) == 1
    assert results[0].name == "code-formatter"


def test_registry_search_by_description():
    """search() matches against plugin descriptions."""
    registry = MarketplaceRegistry()
    registry.register(
        PluginInfo(
            name="alpha",
            version="1.0.0",
            description="Formats Python code beautifully",
        )
    )
    registry.register(
        PluginInfo(name="beta", version="1.0.0", description="Runs tests")
    )
    results = registry.search("python")
    assert len(results) == 1
    assert results[0].name == "alpha"


def test_registry_search_by_tag():
    """search() matches against tag values."""
    registry = MarketplaceRegistry()
    registry.register(
        PluginInfo(name="widget", version="1.0.0", tags=["ui", "dashboard"])
    )
    registry.register(
        PluginInfo(name="gadget", version="1.0.0", tags=["backend"])
    )
    results = registry.search("dashboard")
    assert len(results) == 1
    assert results[0].name == "widget"


def test_registry_list_all():
    """list_all() returns every registered plugin."""
    registry = MarketplaceRegistry()
    registry.register(PluginInfo(name="a", version="1.0.0"))
    registry.register(PluginInfo(name="b", version="2.0.0"))
    all_plugins = registry.list_all()
    assert len(all_plugins) == 2
    names = {p.name for p in all_plugins}
    assert names == {"a", "b"}


def test_registry_by_tag():
    """by_tag() filters plugins that have a specific tag."""
    registry = MarketplaceRegistry()
    registry.register(
        PluginInfo(name="p1", version="1.0.0", tags=["tool", "ai"])
    )
    registry.register(
        PluginInfo(name="p2", version="1.0.0", tags=["tool"])
    )
    registry.register(
        PluginInfo(name="p3", version="1.0.0", tags=["provider"])
    )
    tool_plugins = registry.by_tag("tool")
    assert len(tool_plugins) == 2
    names = {p.name for p in tool_plugins}
    assert names == {"p1", "p2"}


def test_registry_top_rated():
    """top_rated() returns plugins sorted by rating descending."""
    registry = MarketplaceRegistry()
    registry.register(PluginInfo(name="low", version="1.0.0", rating=1.0))
    registry.register(PluginInfo(name="mid", version="1.0.0", rating=3.0))
    registry.register(PluginInfo(name="high", version="1.0.0", rating=5.0))
    top = registry.top_rated(limit=2)
    assert len(top) == 2
    assert top[0].name == "high"
    assert top[1].name == "mid"


# ---------------------------------------------------------------------------
# Marketplace tests
# ---------------------------------------------------------------------------


def test_marketplace_publish():
    """publish() adds a plugin to the underlying registry."""
    mp = Marketplace()
    info = PluginInfo(name="mp-plugin", version="1.0.0")
    mp.publish(info)
    assert mp.registry.get("mp-plugin") is info


def test_marketplace_install_uninstall():
    """install() and uninstall() track plugin installation state."""
    mp = Marketplace()
    mp.publish(PluginInfo(name="installable", version="1.0.0"))

    # Install succeeds for a known plugin
    assert mp.install("installable") is True
    assert mp.is_installed("installable") is True

    # Install fails for unknown plugin
    assert mp.install("unknown") is False

    # Uninstall succeeds for installed plugin
    assert mp.uninstall("installable") is True
    assert mp.is_installed("installable") is False

    # Uninstall fails for non-installed plugin
    assert mp.uninstall("installable") is False


def test_marketplace_installed_property():
    """installed property returns sorted list of installed plugin names."""
    mp = Marketplace()
    mp.publish(PluginInfo(name="zebra", version="1.0.0"))
    mp.publish(PluginInfo(name="alpha", version="1.0.0"))
    mp.install("zebra")
    mp.install("alpha")
    assert mp.installed == ["alpha", "zebra"]


def test_marketplace_search():
    """Marketplace.search() delegates to the underlying registry."""
    mp = Marketplace()
    mp.publish(PluginInfo(name="search-me", version="1.0.0", description="A great plugin"))
    mp.publish(PluginInfo(name="other", version="1.0.0", description="Not relevant"))
    results = mp.search("great")
    assert len(results) == 1
    assert results[0].name == "search-me"
