"""Directory-based plugin loader."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.agents.config import AgentConfig
from chimera.plugins.base import BasePlugin, ComponentRegistry, Hook, MCPServerConfig
from chimera.plugins.registry import PluginExtensionRegistry

if TYPE_CHECKING:
    pass


class DirectoryPluginLoader:
    """Load plugin extensions from a directory structure.

    Expected layout::

        my-plugin/
        ├── plugin.json            # manifest (name, version, etc.)
        ├── agents/
        │   └── code-reviewer.md   # agent definition (YAML frontmatter)
        ├── hooks/
        │   └── hooks.json         # event hooks
        └── .mcp.json              # MCP server configs

    Example:
        ```python
        loader = DirectoryPluginLoader()
        plugin = loader.load("/path/to/my-plugin")
        plugin.activate(registry)
        ```
    """

    MANIFEST_FILES = ["plugin.json", "chimera-plugin.json"]

    def load(self, plugin_dir: str | Path) -> BasePlugin:
        """Load a plugin from a directory with convention-based structure.

        Args:
            plugin_dir: Path to the plugin directory.

        Returns:
            A BasePlugin instance ready to be activated.
        """
        path = Path(plugin_dir)
        manifest = self._load_manifest(path)
        return _DirectoryPlugin(manifest, path)

    def _load_manifest(self, path: Path) -> dict:
        """Load the plugin manifest file.

        Args:
            path: Plugin directory path.

        Returns:
            Manifest data as a dictionary.
        """
        for name in self.MANIFEST_FILES:
            manifest_path = path / name
            if manifest_path.exists():
                return json.loads(manifest_path.read_text())
        return {}


class _DirectoryPlugin(BasePlugin):
    """Plugin loaded from a directory."""

    def __init__(self, manifest: dict, path: Path) -> None:
        self._name = manifest.get("name", path.name)
        self.version = manifest.get("version", "0.0.0")
        self.description = manifest.get("description", "")
        self.author = manifest.get("author", "")
        self._path = path

    @property
    def name(self) -> str:
        return self._name

    def register_agents(self, registry: ComponentRegistry) -> None:
        agents_dir = self._path / "agents"
        if not agents_dir.exists():
            return
        for agent_file in sorted(agents_dir.glob("*.md")):
            try:
                config = AgentConfig.from_markdown(str(agent_file))
                PluginExtensionRegistry.register_agent(config.name, config)
            except Exception:
                continue

    def register_mcp_servers(self, registry: ComponentRegistry) -> None:
        mcp_file = self._path / ".mcp.json"
        if not mcp_file.exists():
            return
        try:
            data = json.loads(mcp_file.read_text())
            for name, server_config in data.get("servers", {}).items():
                PluginExtensionRegistry.register_mcp_server(
                    name, MCPServerConfig(**server_config)
                )
        except Exception:
            pass

    def register_hooks(self, registry: ComponentRegistry) -> None:
        hooks_file = self._path / "hooks" / "hooks.json"
        if not hooks_file.exists():
            return
        try:
            hooks_data = json.loads(hooks_file.read_text())
            for hook_def in hooks_data:
                hook = Hook(**hook_def)
                PluginExtensionRegistry.register_hook(hook.event_type, hook)
        except Exception:
            pass
