"""Otter plugin loader (directory-based).

Mirrors the upstream open-source coding agent's ``~/.opencode/plugin/*``
directory convention while delegating all heavy lifting to the existing
:mod:`chimera.plugins` primitives. Two roots are scanned:

1. **User-level**: ``~/.opencode/plugin/<name>/`` — applies to every
   project on the host.
2. **Project-level**: ``<project_root>/.opencode/plugin/<name>/`` —
   overrides user-level on plugin-name conflict.

Each plugin directory must contain a ``manifest.json`` (also accepted:
``plugin.json``, ``chimera-plugin.json``, ``package.json``). The manifest
shape is intentionally permissive — we read ``name``, ``version``,
``description``, ``author`` and ignore unknown keys so opencode-style
manifests load without modification.

A loaded plugin can additionally contribute, by convention:

* ``agents/*.md`` — markdown agent definitions parsed via
  :meth:`chimera.agents.config.AgentConfig.from_markdown`
* ``command/*.md`` *or* ``commands/*.md`` — slash-command markdown files
  exposed as :class:`OtterCommand` records
* ``mcp.json`` *or* ``.mcp.json`` — MCP server configs
* ``hooks/hooks.json`` *or* ``hooks.json`` — event hooks

The loader is **stdlib-only** and never raises on a malformed plugin: the
offending plugin is skipped so a single bad manifest cannot break the
whole otter invocation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.agents.config import AgentConfig
from chimera.plugins.base import BasePlugin, ComponentRegistry, Hook, MCPServerConfig

# Manifest filenames searched in priority order.
_MANIFEST_NAMES: tuple[str, ...] = (
    "manifest.json",
    "plugin.json",
    "chimera-plugin.json",
    "package.json",
)

# Slash-command directory names searched in priority order. Upstream uses
# ``command/`` (singular); chimera convention is ``commands/``. Honour both.
_COMMAND_DIRS: tuple[str, ...] = ("command", "commands")

# MCP config filenames searched in priority order.
_MCP_FILES: tuple[str, ...] = ("mcp.json", ".mcp.json")


@dataclass
class OtterCommand:
    """A slash command contributed by an otter plugin.

    Args:
        name: The command name (without leading slash).
        description: Human-readable summary lifted from frontmatter.
        body: The markdown body served as the command prompt.
        source: Absolute path to the source ``.md`` file.
        plugin: The contributing plugin's name.
    """

    name: str
    description: str
    body: str
    source: Path
    plugin: str


@dataclass
class OtterPlugin(BasePlugin):
    """Plugin instance materialized from an otter plugin directory.

    Holds the manifest metadata plus pre-parsed extension records so
    callers can introspect contributions without re-walking the dir.
    """

    _name: str = ""
    _version: str = "0.0.0"
    _description: str = ""
    _author: str = ""
    path: Path = field(default_factory=Path)
    scope: str = "user"
    manifest: dict[str, Any] = field(default_factory=dict)
    agents: list[AgentConfig] = field(default_factory=list)
    commands: list[OtterCommand] = field(default_factory=list)
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    hooks: list[Hook] = field(default_factory=list)

    @property
    def name(self) -> str:  # noqa: D401  (see BasePlugin.name)
        return self._name

    def __post_init__(self) -> None:
        """Mirror BasePlugin's class-attribute slots from manifest values."""
        # BasePlugin defines version/description/author as class-level
        # defaults; the dataclass's underscore-prefixed fields are the
        # source of truth, so reflect them onto the public attributes.
        self.version = self._version
        self.description = self._description
        self.author = self._author

    # ---- BasePlugin extension hooks ---------------------------------------

    def register_agents(self, registry: ComponentRegistry) -> None:
        """Stage parsed agent configs onto the registry."""
        for agent in self.agents:
            # ComponentRegistry has no first-class agent slot; keep the
            # parsed configs on the plugin instance and expose via the
            # generic ``register_command`` slot for downstream aggregation.
            registry.register_command({"kind": "agent", "config": agent})

    def register_mcp_servers(self, registry: ComponentRegistry) -> None:
        """Stage MCP server configs (no-op if none)."""
        for server_name, cfg in self.mcp_servers.items():
            registry.register_command(
                {"kind": "mcp", "name": server_name, "config": cfg}
            )

    def register_hooks(self, registry: ComponentRegistry) -> None:
        """Stage hooks, keyed by event type."""
        for hook in self.hooks:
            registry.register_hook(hook.event_type, hook)


# ---------------------------------------------------------------------------
# Manifest + asset parsing helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    """Best-effort JSON read; returns ``None`` on any failure.

    Returns the parsed structure as-is — callers are responsible for
    validating its type. ``None`` signals "missing or unparseable".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _load_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    """Locate and parse the first manifest file present.

    Returns ``None`` if no recognised manifest exists or all parses fail.
    """
    for name in _MANIFEST_NAMES:
        candidate = plugin_dir / name
        if candidate.is_file():
            data = _read_json(candidate)
            if isinstance(data, dict):
                return data
    return None


def _load_agents(plugin_dir: Path) -> list[AgentConfig]:
    """Parse ``agents/*.md`` files; bad files are skipped, not raised."""
    agents_dir = plugin_dir / "agents"
    if not agents_dir.is_dir():
        return []
    out: list[AgentConfig] = []
    for md in sorted(agents_dir.glob("*.md")):
        try:
            out.append(AgentConfig.from_markdown(str(md)))
        except (OSError, ValueError):
            continue
    return out


def _parse_command_md(path: Path, plugin_name: str) -> OtterCommand | None:
    """Parse one slash-command markdown file.

    The format mirrors :meth:`AgentConfig.from_markdown`: a YAML
    frontmatter block (``---`` delimited) carrying at minimum ``name``
    and ``description``, followed by the prompt body. Files without
    frontmatter fall back to using the filename stem as the command name.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    name = path.stem
    description = ""
    body = text.strip()
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, _, raw = line.partition(":")
                key = key.strip()
                value = raw.strip().strip("\"'")
                if key == "name" and value:
                    name = value
                elif key == "description":
                    description = value
    return OtterCommand(
        name=name,
        description=description,
        body=body,
        source=path,
        plugin=plugin_name,
    )


def _load_commands(plugin_dir: Path, plugin_name: str) -> list[OtterCommand]:
    """Walk recognised command dirs and return parsed commands."""
    out: list[OtterCommand] = []
    for sub in _COMMAND_DIRS:
        d = plugin_dir / sub
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            cmd = _parse_command_md(md, plugin_name)
            if cmd is not None:
                out.append(cmd)
    return out


def _coerce_mcp_config(raw: Any) -> MCPServerConfig | None:
    """Normalize one MCP entry into :class:`MCPServerConfig`.

    Accepts both opencode-style (``{"command": "node", "args": [...]}``)
    and chimera-style (``{"command": ["node", "..."]}``) shapes.
    """
    if not isinstance(raw, dict):
        return None
    cmd = raw.get("command")
    args_raw = raw.get("args")
    env_raw = raw.get("env", {})

    if isinstance(cmd, str):
        command_list: list[str] = [cmd]
    elif isinstance(cmd, list) and all(isinstance(p, str) for p in cmd):
        command_list = list(cmd)
    else:
        return None

    if args_raw is None:
        args_list: list[str] = []
    elif isinstance(args_raw, list) and all(isinstance(p, str) for p in args_raw):
        args_list = list(args_raw)
    else:
        return None

    if not isinstance(env_raw, dict):
        env_dict: dict[str, str] = {}
    else:
        env_dict = {
            str(k): str(v) for k, v in env_raw.items() if isinstance(v, (str, int, float))
        }

    return MCPServerConfig(command=command_list, args=args_list, env=env_dict)


def _load_mcp_servers(plugin_dir: Path) -> dict[str, MCPServerConfig]:
    """Load MCP server configs from ``mcp.json`` / ``.mcp.json``.

    Recognises two top-level shapes:

    * ``{"servers": {"name": {...}, ...}}`` (chimera ``DirectoryPluginLoader``)
    * ``{"mcpServers": {"name": {...}, ...}}`` (Claude / opencode style)
    """
    for fname in _MCP_FILES:
        path = plugin_dir / fname
        if not path.is_file():
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        servers_raw = data.get("servers")
        if not isinstance(servers_raw, dict):
            servers_raw = data.get("mcpServers")
        if not isinstance(servers_raw, dict):
            continue
        out: dict[str, MCPServerConfig] = {}
        for name, raw in servers_raw.items():
            cfg = _coerce_mcp_config(raw)
            if cfg is not None:
                out[str(name)] = cfg
        return out
    return {}


def _load_hooks(plugin_dir: Path) -> list[Hook]:
    """Load hooks from ``hooks/hooks.json`` or ``hooks.json``.

    Accepts a list of hook dicts or a dict keyed by event type whose values
    are lists of hook dicts. Each entry must at minimum supply ``command``
    and (when in list form) ``event_type``.
    """
    candidates = [
        plugin_dir / "hooks" / "hooks.json",
        plugin_dir / "hooks.json",
    ]
    raw: Any = None
    for path in candidates:
        if path.is_file():
            raw = _read_json(path)
            if raw is not None:
                break
    if raw is None:
        return []

    out: list[Hook] = []

    def _push(entry: Any, event_type: str | None) -> None:
        if not isinstance(entry, dict):
            return
        cmd = entry.get("command")
        if not isinstance(cmd, str) or not cmd:
            return
        ev = event_type or entry.get("event_type")
        if not isinstance(ev, str) or not ev:
            return
        timeout_raw = entry.get("timeout", 30)
        try:
            timeout = int(timeout_raw)
        except (TypeError, ValueError):
            timeout = 30
        env_raw = entry.get("env", {})
        env = (
            {str(k): str(v) for k, v in env_raw.items()}
            if isinstance(env_raw, dict)
            else {}
        )
        wd = entry.get("working_dir")
        out.append(
            Hook(
                command=cmd,
                event_type=ev,
                working_dir=str(wd) if isinstance(wd, str) else None,
                timeout=timeout,
                env=env,
            )
        )

    if isinstance(raw, list):
        for entry in raw:
            _push(entry, None)
    elif isinstance(raw, dict):
        for ev, entries in raw.items():
            if isinstance(entries, list):
                for entry in entries:
                    _push(entry, str(ev))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _user_root() -> Path:
    """Return the user-level otter plugin root."""
    return Path.home() / ".opencode" / "plugin"


def _project_root(project_root: Path) -> Path:
    """Return the project-level otter plugin root for ``project_root``."""
    return project_root / ".opencode" / "plugin"


def _plugin_dirs(root: Path) -> list[Path]:
    """Return immediate subdirectories of ``root`` that look like plugins.

    A plugin directory is any subdir of ``root`` that contains at least
    one of the recognised manifest files. Hidden directories (``.git``,
    ``__pycache__``, dotfiles) are skipped.
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if any((child / m).is_file() for m in _MANIFEST_NAMES):
            out.append(child)
    return out


def _build_plugin(plugin_dir: Path, scope: str) -> OtterPlugin | None:
    """Materialize one :class:`OtterPlugin` from a directory.

    Returns ``None`` when the manifest is missing or unparseable.
    """
    manifest = _load_manifest(plugin_dir)
    if manifest is None:
        return None
    raw_name = manifest.get("name")
    name = (
        str(raw_name).strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else plugin_dir.name
    )
    plugin = OtterPlugin(
        _name=name,
        _version=str(manifest.get("version", "0.0.0")),
        _description=str(manifest.get("description", "")),
        _author=str(manifest.get("author", "")),
        path=plugin_dir,
        scope=scope,
        manifest=manifest,
        agents=_load_agents(plugin_dir),
        commands=_load_commands(plugin_dir, name),
        mcp_servers=_load_mcp_servers(plugin_dir),
        hooks=_load_hooks(plugin_dir),
    )
    return plugin


def load_otter_plugins(
    project_root: Path,
    *,
    user_root: Path | None = None,
) -> list[BasePlugin]:
    """Load all otter plugins from user and project roots.

    Args:
        project_root: Project directory; ``<project_root>/.opencode/plugin/``
            is scanned for project-scoped plugins.
        user_root: Override for the user-level plugin root. Defaults to
            ``~/.opencode/plugin/``. Primarily used by tests.

    Returns:
        A list of :class:`BasePlugin` instances. Project-level plugins
        replace user-level plugins on plugin-name conflict; the returned
        order is *user-first then project*, with conflicts resolved by
        emitting only the project-level instance in the project's slot.
    """
    user_dir = user_root if user_root is not None else _user_root()
    project_dir = _project_root(project_root)

    user_plugins: dict[str, OtterPlugin] = {}
    for d in _plugin_dirs(user_dir):
        plugin = _build_plugin(d, scope="user")
        if plugin is not None:
            user_plugins[plugin.name] = plugin

    project_plugins: dict[str, OtterPlugin] = {}
    for d in _plugin_dirs(project_dir):
        plugin = _build_plugin(d, scope="project")
        if plugin is not None:
            project_plugins[plugin.name] = plugin

    # Project overrides user on name conflict.
    merged: dict[str, OtterPlugin] = {}
    for name, plugin in user_plugins.items():
        if name not in project_plugins:
            merged[name] = plugin
    for name, plugin in project_plugins.items():
        merged[name] = plugin

    return list(merged.values())


__all__ = [
    "OtterCommand",
    "OtterPlugin",
    "load_otter_plugins",
]
