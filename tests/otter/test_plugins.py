"""Tests for the otter plugin loader.

Materialize fake plugin directories under ``tmp_path`` for both the
user-level (``~/.opencode/plugin/``) and project-level
(``<project>/.opencode/plugin/``) roots, then exercise
:func:`chimera.otter.plugins.load_otter_plugins`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.otter import plugins as plugins_mod
from chimera.otter.plugins import (
    OtterCommand,
    OtterPlugin,
    load_otter_plugins,
)
from chimera.plugins.base import ComponentRegistry, Hook, MCPServerConfig


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_manifest(plugin_dir: Path, payload: dict[str, Any], *, name: str = "manifest.json") -> None:
    """Write a manifest file under ``plugin_dir``."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def _write_agent_md(plugin_dir: Path, filename: str, *, name: str, body: str = "Agent body") -> None:
    """Write an agents/*.md file with valid YAML frontmatter."""
    agents = plugin_dir / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / filename).write_text(
        f"---\nname: {name}\ndescription: test agent\n---\n{body}\n",
        encoding="utf-8",
    )


def _write_command_md(
    plugin_dir: Path,
    filename: str,
    *,
    name: str | None = None,
    description: str = "test command",
    body: str = "Command body",
    sub: str = "command",
) -> None:
    """Write a command markdown file under the requested subdir."""
    cmd_dir = plugin_dir / sub
    cmd_dir.mkdir(parents=True, exist_ok=True)
    if name is None:
        text = body
    else:
        text = f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    (cmd_dir / filename).write_text(text, encoding="utf-8")


def _write_mcp_json(plugin_dir: Path, payload: dict[str, Any], *, filename: str = "mcp.json") -> None:
    """Write an MCP server config file."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def _write_hooks(
    plugin_dir: Path,
    payload: Any,
    *,
    nested: bool = True,
) -> None:
    """Write hooks.json either at hooks/hooks.json or top-level."""
    if nested:
        target = plugin_dir / "hooks"
        target.mkdir(parents=True, exist_ok=True)
        (target / "hooks.json").write_text(json.dumps(payload), encoding="utf-8")
    else:
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "hooks.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Return a fresh project root under tmp_path."""
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    """Return a fresh user-level plugin root under tmp_path."""
    root = tmp_path / "home" / ".opencode" / "plugin"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# Manifest discovery
# ---------------------------------------------------------------------------


def test_load_returns_empty_when_no_roots(project_root: Path, tmp_path: Path) -> None:
    """Both roots absent: clean empty list, no exception."""
    missing_user = tmp_path / "no-user"
    plugins = load_otter_plugins(project_root, user_root=missing_user)
    assert plugins == []


def test_load_user_plugin_with_manifest(project_root: Path, user_root: Path) -> None:
    """A user-level plugin with a manifest is discovered."""
    plugin_dir = user_root / "demo"
    _write_manifest(plugin_dir, {"name": "demo", "version": "1.2.3", "description": "d"})

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert len(plugins) == 1
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert p.name == "demo"
    assert p.version == "1.2.3"
    assert p.description == "d"
    assert p.scope == "user"
    assert p.path == plugin_dir


def test_load_skips_dirs_without_manifest(project_root: Path, user_root: Path) -> None:
    """A subdir lacking a recognised manifest is skipped, not raised."""
    (user_root / "no-manifest").mkdir()
    _write_manifest(user_root / "good", {"name": "good"})

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert [p.name for p in plugins] == ["good"]


def test_load_skips_hidden_and_pycache(project_root: Path, user_root: Path) -> None:
    """Hidden dirs and __pycache__ are not treated as plugins."""
    _write_manifest(user_root / ".hidden", {"name": "hidden"})
    _write_manifest(user_root / "__pycache__", {"name": "cache"})
    _write_manifest(user_root / "real", {"name": "real"})

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert [p.name for p in plugins] == ["real"]


def test_load_falls_back_to_dirname_when_manifest_lacks_name(
    project_root: Path, user_root: Path
) -> None:
    """Empty/missing manifest ``name`` falls back to the directory name."""
    plugin_dir = user_root / "fallback-name"
    _write_manifest(plugin_dir, {})
    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert [p.name for p in plugins] == ["fallback-name"]


def test_load_accepts_alternate_manifest_filenames(
    project_root: Path, user_root: Path
) -> None:
    """``plugin.json``, ``chimera-plugin.json``, ``package.json`` are accepted."""
    _write_manifest(user_root / "a", {"name": "a"}, name="plugin.json")
    _write_manifest(user_root / "b", {"name": "b"}, name="chimera-plugin.json")
    _write_manifest(user_root / "c", {"name": "c"}, name="package.json")

    plugins = load_otter_plugins(project_root, user_root=user_root)
    names = sorted(p.name for p in plugins)
    assert names == ["a", "b", "c"]


def test_load_ignores_malformed_manifest(project_root: Path, user_root: Path) -> None:
    """An unparseable manifest is skipped silently."""
    bad = user_root / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{ not valid json", encoding="utf-8")
    _write_manifest(user_root / "good", {"name": "good"})

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert [p.name for p in plugins] == ["good"]


# ---------------------------------------------------------------------------
# Project override
# ---------------------------------------------------------------------------


def test_project_overrides_user_on_name_conflict(
    project_root: Path, user_root: Path
) -> None:
    """Project plugin replaces user plugin sharing the same ``name``."""
    _write_manifest(user_root / "shared", {"name": "shared", "version": "1.0.0"})
    project_plugin_dir = project_root / ".opencode" / "plugin" / "shared"
    _write_manifest(project_plugin_dir, {"name": "shared", "version": "9.9.9"})

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert len(plugins) == 1
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert p.name == "shared"
    assert p.version == "9.9.9"
    assert p.scope == "project"


def test_user_and_project_coexist_when_distinct(
    project_root: Path, user_root: Path
) -> None:
    """Distinct names from each scope appear together."""
    _write_manifest(user_root / "alpha", {"name": "alpha"})
    _write_manifest(
        project_root / ".opencode" / "plugin" / "beta",
        {"name": "beta"},
    )
    plugins = load_otter_plugins(project_root, user_root=user_root)
    by_name = {plugin.name: plugin for plugin in plugins}
    assert set(by_name) == {"alpha", "beta"}
    # alpha is from user_root, beta is from project_root
    alpha = by_name["alpha"]
    beta = by_name["beta"]
    assert isinstance(alpha, OtterPlugin)
    assert isinstance(beta, OtterPlugin)
    assert alpha.scope == "user"
    assert beta.scope == "project"


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def test_plugin_loads_agents(project_root: Path, user_root: Path) -> None:
    """``agents/*.md`` files are parsed into AgentConfig records."""
    plugin_dir = user_root / "with-agents"
    _write_manifest(plugin_dir, {"name": "with-agents"})
    _write_agent_md(plugin_dir, "alpha.md", name="alpha")
    _write_agent_md(plugin_dir, "beta.md", name="beta")

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    names = sorted(a.name for a in p.agents)
    assert names == ["alpha", "beta"]


def test_plugin_skips_malformed_agent(project_root: Path, user_root: Path) -> None:
    """Agent markdown without frontmatter is skipped, not raised."""
    plugin_dir = user_root / "bad-agent"
    _write_manifest(plugin_dir, {"name": "bad-agent"})
    agents = plugin_dir / "agents"
    agents.mkdir()
    (agents / "broken.md").write_text("no frontmatter here", encoding="utf-8")
    _write_agent_md(plugin_dir, "good.md", name="good")

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert isinstance(plugins[0], OtterPlugin)
    assert [a.name for a in plugins[0].agents] == ["good"]


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def test_plugin_loads_commands_from_command_dir(
    project_root: Path, user_root: Path
) -> None:
    """Commands under ``command/`` (singular) are picked up."""
    plugin_dir = user_root / "with-commands"
    _write_manifest(plugin_dir, {"name": "with-commands"})
    _write_command_md(
        plugin_dir, "review.md", name="review", description="review code", body="Do a review.",
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert len(p.commands) == 1
    cmd = p.commands[0]
    assert isinstance(cmd, OtterCommand)
    assert cmd.name == "review"
    assert cmd.description == "review code"
    assert "Do a review." in cmd.body
    assert cmd.plugin == "with-commands"


def test_plugin_loads_commands_from_commands_dir(
    project_root: Path, user_root: Path
) -> None:
    """Commands under ``commands/`` (plural) are picked up too."""
    plugin_dir = user_root / "plural"
    _write_manifest(plugin_dir, {"name": "plural"})
    _write_command_md(plugin_dir, "test.md", name="test", sub="commands")

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert isinstance(plugins[0], OtterPlugin)
    assert [c.name for c in plugins[0].commands] == ["test"]


def test_command_without_frontmatter_uses_filename(
    project_root: Path, user_root: Path
) -> None:
    """A bare markdown command falls back to the filename stem."""
    plugin_dir = user_root / "bare"
    _write_manifest(plugin_dir, {"name": "bare"})
    _write_command_md(plugin_dir, "raw.md", body="just text")

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert isinstance(plugins[0], OtterPlugin)
    cmds = plugins[0].commands
    assert [c.name for c in cmds] == ["raw"]
    assert cmds[0].body == "just text"


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


def test_plugin_loads_mcp_servers_chimera_shape(
    project_root: Path, user_root: Path
) -> None:
    """``{"servers": {...}}`` shape with command-as-list is accepted."""
    plugin_dir = user_root / "mcp-c"
    _write_manifest(plugin_dir, {"name": "mcp-c"})
    _write_mcp_json(
        plugin_dir,
        {
            "servers": {
                "search": {"command": ["python", "-m", "foo"], "env": {"K": "V"}},
            },
        },
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert "search" in p.mcp_servers
    cfg = p.mcp_servers["search"]
    assert isinstance(cfg, MCPServerConfig)
    assert cfg.command == ["python", "-m", "foo"]
    assert cfg.env == {"K": "V"}


def test_plugin_loads_mcp_servers_string_command(
    project_root: Path, user_root: Path
) -> None:
    """Opencode-style ``{"mcpServers": {"...": {"command": "node", "args": [...]}}}``."""
    plugin_dir = user_root / "mcp-o"
    _write_manifest(plugin_dir, {"name": "mcp-o"})
    _write_mcp_json(
        plugin_dir,
        {
            "mcpServers": {
                "lsp": {"command": "node", "args": ["server.js"]},
            },
        },
        filename=".mcp.json",
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert p.mcp_servers["lsp"].command == ["node"]
    assert p.mcp_servers["lsp"].args == ["server.js"]


def test_plugin_skips_invalid_mcp_entry(project_root: Path, user_root: Path) -> None:
    """Entries with non-string commands are dropped, not raised."""
    plugin_dir = user_root / "mcp-bad"
    _write_manifest(plugin_dir, {"name": "mcp-bad"})
    _write_mcp_json(
        plugin_dir,
        {"servers": {"good": {"command": "node"}, "bad": {"command": 42}}},
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert "good" in p.mcp_servers
    assert "bad" not in p.mcp_servers


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def test_plugin_loads_hooks_list_form(project_root: Path, user_root: Path) -> None:
    """A list of hook dicts becomes a list of Hook objects."""
    plugin_dir = user_root / "with-hooks"
    _write_manifest(plugin_dir, {"name": "with-hooks"})
    _write_hooks(
        plugin_dir,
        [
            {"command": "echo pre", "event_type": "PreToolUse"},
            {"command": "echo post", "event_type": "PostToolUse", "timeout": 5},
        ],
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    assert [(h.command, h.event_type) for h in p.hooks] == [
        ("echo pre", "PreToolUse"),
        ("echo post", "PostToolUse"),
    ]
    assert p.hooks[1].timeout == 5


def test_plugin_loads_hooks_dict_form(project_root: Path, user_root: Path) -> None:
    """A dict keyed by event name with list values is accepted."""
    plugin_dir = user_root / "hooks-dict"
    _write_manifest(plugin_dir, {"name": "hooks-dict"})
    _write_hooks(
        plugin_dir,
        {
            "PreToolUse": [{"command": "echo pre"}],
            "PostToolUse": [{"command": "echo post"}],
        },
        nested=False,
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    by_event = {h.event_type: h.command for h in p.hooks}
    assert by_event == {"PreToolUse": "echo pre", "PostToolUse": "echo post"}


def test_plugin_skips_invalid_hook_entry(project_root: Path, user_root: Path) -> None:
    """Entries missing ``command`` or ``event_type`` are dropped."""
    plugin_dir = user_root / "hooks-bad"
    _write_manifest(plugin_dir, {"name": "hooks-bad"})
    _write_hooks(
        plugin_dir,
        [
            {"command": "echo good", "event_type": "Pre"},
            {"event_type": "Pre"},                # missing command
            {"command": "echo no-event"},          # missing event_type
            {"command": "echo bad-timeout", "event_type": "Pre", "timeout": "x"},
        ],
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    p = plugins[0]
    assert isinstance(p, OtterPlugin)
    cmds = [h.command for h in p.hooks]
    assert "echo good" in cmds
    assert "echo bad-timeout" in cmds  # invalid timeout falls back to default
    bad_timeout = next(h for h in p.hooks if h.command == "echo bad-timeout")
    assert bad_timeout.timeout == 30
    # Two invalid entries dropped -> we kept exactly the two valid ones.
    assert len(p.hooks) == 2


# ---------------------------------------------------------------------------
# BasePlugin.activate integration
# ---------------------------------------------------------------------------


def test_activate_registers_hooks_on_component_registry(
    project_root: Path, user_root: Path
) -> None:
    """Calling ``plugin.activate(reg)`` propagates hooks to the registry."""
    plugin_dir = user_root / "hooks-activate"
    _write_manifest(plugin_dir, {"name": "hooks-activate"})
    _write_hooks(
        plugin_dir,
        [{"command": "echo a", "event_type": "PreToolUse"}],
    )

    plugins = load_otter_plugins(project_root, user_root=user_root)
    assert plugins, "loader returned no plugins"

    registry = ComponentRegistry()
    plugins[0].activate(registry)
    pre = registry.hooks.get("PreToolUse", [])
    assert len(pre) == 1
    assert isinstance(pre[0], Hook)
    assert pre[0].command == "echo a"


def test_activate_stages_agent_and_mcp_into_commands_slot(
    project_root: Path, user_root: Path
) -> None:
    """Activate exposes agents and MCP servers via the command slot."""
    plugin_dir = user_root / "rich"
    _write_manifest(plugin_dir, {"name": "rich"})
    _write_agent_md(plugin_dir, "a.md", name="a")
    _write_mcp_json(plugin_dir, {"servers": {"s": {"command": "node"}}})

    plugins = load_otter_plugins(project_root, user_root=user_root)
    registry = ComponentRegistry()
    plugins[0].activate(registry)
    kinds = sorted(item.get("kind", "?") for item in registry.commands)
    assert kinds == ["agent", "mcp"]


# ---------------------------------------------------------------------------
# Default user_root via Path.home()
# ---------------------------------------------------------------------------


def test_default_user_root_uses_path_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``user_root=`` the loader resolves ``~/.opencode/plugin``."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    plugin_dir = fake_home / ".opencode" / "plugin" / "by-home"
    _write_manifest(plugin_dir, {"name": "by-home"})

    project_root = tmp_path / "proj"
    project_root.mkdir()

    plugins = load_otter_plugins(project_root)
    assert [p.name for p in plugins] == ["by-home"]


def test_module_exports_public_api() -> None:
    """The public surface stays intentional."""
    assert "load_otter_plugins" in plugins_mod.__all__
    assert "OtterPlugin" in plugins_mod.__all__
    assert "OtterCommand" in plugins_mod.__all__
