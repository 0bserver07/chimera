"""Verify the in-tree ``chimera-plugin/`` ships a valid manifest.

The chimera-plugin directory bundles the canonical set of skills,
agents, commands, hooks, and MCP servers that ship with Chimera. It
must:

1. Carry a top-level ``plugin.json`` parseable by both the
   :class:`DirectoryPluginLoader` (loose schema) and the marketplace
   :func:`PluginInfo.from_dict` (PluginInfo schema). The two schemas
   coexist in one file because :func:`PluginInfo.from_dict` ignores
   unknown keys.
2. List components (skill / agent / command / hook) whose ``path``
   resolves to a real file on disk -- broken paths are a maintenance
   landmine that should fail loud at test time.
3. Declare ``mcp_servers`` whose ``module`` strings actually import
   under the running interpreter. We deliberately import the modules
   rather than spawning the subprocess form (``python3 -m ...``) so
   the test stays hermetic and fast.

The test also makes sure the manifest is registered as a real entry
in the example plugin index (with the documented ``_note`` exemption)
so the marketplace can surface it as a valid built-in plugin.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from chimera.plugins.dir_loader import DirectoryPluginLoader
from chimera.plugins.marketplace import PluginInfo, fetch_index

# tests/plugins/test_x.py -> tests/plugins -> tests -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "chimera-plugin"
_MANIFEST = _PLUGIN_DIR / "plugin.json"
_EXAMPLE_INDEX = _REPO_ROOT / "examples" / "plugin-index.json"

_VALID_COMPONENT_TYPES = {"skill", "agent", "command", "hook"}


# ---------------------------------------------------------------------------
# Manifest presence + schema
# ---------------------------------------------------------------------------


def test_manifest_exists() -> None:
    """``chimera-plugin/plugin.json`` is the canonical manifest path."""
    assert _MANIFEST.is_file(), f"missing manifest at {_MANIFEST}"


def test_manifest_marketplace_schema() -> None:
    """The manifest round-trips through ``PluginInfo.from_dict``.

    The marketplace ignores unknown keys, so the richer (components +
    mcp_servers) shape is fine. We just need ``name`` and ``version``
    to be present and stringable.
    """
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    info = PluginInfo.from_dict(raw)
    assert info.name == "chimera-plugin"
    assert info.version
    assert info.description
    assert info.author


def test_manifest_required_fields() -> None:
    """Spec-required fields are all present and non-empty."""
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    for key in ("name", "version", "description", "license", "author", "homepage"):
        assert raw.get(key), f"manifest missing required field {key!r}"
    assert raw["license"] == "MIT"
    assert raw["name"] == "chimera-plugin"


# ---------------------------------------------------------------------------
# Directory loader integration
# ---------------------------------------------------------------------------


def test_manifest_loads_via_directory_loader() -> None:
    """The directory loader must accept the manifest verbatim."""
    loader = DirectoryPluginLoader()
    plugin = loader.load(_PLUGIN_DIR)
    assert plugin.name == "chimera-plugin"
    assert plugin.version
    assert plugin.description


# ---------------------------------------------------------------------------
# Component file existence
# ---------------------------------------------------------------------------


def test_components_resolve_on_disk() -> None:
    """Every declared component path resolves to a real file."""
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    components = raw.get("components", [])
    assert components, "manifest declares no components"
    seen_types: set[str] = set()
    for comp in components:
        ctype = comp.get("type")
        cpath = comp.get("path")
        cname = comp.get("name")
        assert ctype in _VALID_COMPONENT_TYPES, (
            f"component {cname!r} has unknown type {ctype!r}; "
            f"expected one of {sorted(_VALID_COMPONENT_TYPES)}"
        )
        assert cpath, f"component {cname!r} missing 'path'"
        resolved = _PLUGIN_DIR / cpath
        assert resolved.is_file(), (
            f"component {cname!r} ({ctype}) points at missing file: "
            f"{resolved}"
        )
        seen_types.add(ctype)
    # Sanity: cover every component category at least once. If the
    # manifest is ever pruned to drop, say, hooks entirely, this
    # forces an explicit decision.
    assert seen_types == _VALID_COMPONENT_TYPES, (
        f"expected components covering {sorted(_VALID_COMPONENT_TYPES)}, "
        f"got {sorted(seen_types)}"
    )


# ---------------------------------------------------------------------------
# MCP server importability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "server_name",
    [
        "chimera-search",
        "chimera-review",
        "chimera-testgen",
        "chimera-migration",
        "chimera-rag",
        "chimera-benchmark",
    ],
)
def test_mcp_server_module_importable(server_name: str) -> None:
    """Each declared MCP server module imports cleanly.

    We import rather than exec the subprocess form so the test is
    hermetic. Any side-effect crash on import shows up here instead of
    at runtime when a user tries to start the server.
    """
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    servers = raw.get("mcp_servers", {})
    assert server_name in servers, (
        f"manifest missing mcp_server entry {server_name!r}"
    )
    entry = servers[server_name]
    module_name = entry.get("module")
    assert module_name, (
        f"mcp_server {server_name!r} missing 'module' field"
    )
    importlib.import_module(module_name)


def test_mcp_server_command_matches_module() -> None:
    """The subprocess command and the import module agree.

    Catches drift where one form is renamed but the other isn't —
    e.g. someone renames the file but forgets to update the
    ``python3 -m ...`` invocation.
    """
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    for name, entry in raw.get("mcp_servers", {}).items():
        cmd = entry.get("command", [])
        module = entry.get("module")
        assert module in cmd, (
            f"mcp_server {name!r} command {cmd!r} doesn't reference "
            f"its declared module {module!r}"
        )


# ---------------------------------------------------------------------------
# Marketplace index integration
# ---------------------------------------------------------------------------


def test_chimera_plugin_listed_in_example_index() -> None:
    """The example index advertises chimera-plugin as a real entry."""
    registry = fetch_index(str(_EXAMPLE_INDEX))
    info = registry.get("chimera-plugin")
    assert info is not None, (
        "chimera-plugin is missing from examples/plugin-index.json; "
        "the in-tree plugin must be discoverable via the sample index"
    )
    raw = json.loads(_EXAMPLE_INDEX.read_text(encoding="utf-8"))
    matching = [
        e for e in raw.get("plugins", []) if e.get("name") == "chimera-plugin"
    ]
    assert matching, "chimera-plugin missing from raw plugins list"
    note = str(matching[0].get("_note", ""))
    assert "BUILT-IN" in note.upper() and "REAL" in note.upper(), (
        "chimera-plugin entry must carry a '_note: Built-in plugin "
        f"(real)' marker so the placeholder test exempts it; got {note!r}"
    )
