"""3-scope ``.mcp.json`` loader.

Merges three CC-style configuration files:
    1. user scope    -- ``~/.claude/.mcp.json``
    2. project scope -- ``<cwd>/.claude/.mcp.json``
    3. local scope   -- ``<cwd>/.claude/.mcp.local.json``

Project overrides user; local overrides project. Server names are the
merge keys (each server is replaced wholesale, not deep-merged) so a
project can swap a server's command/args without inheriting stray env
vars from the user-scope entry.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _servers(blob: dict[str, Any]) -> dict[str, Any]:
    """Pull the servers map from either {"mcpServers": ...} or {"servers": ...}.

    CC writes ``mcpServers``; older Chimera configs use ``servers``.  Both
    are accepted to keep upgrades painless.
    """
    if "mcpServers" in blob and isinstance(blob["mcpServers"], dict):
        return dict(blob["mcpServers"])
    if "servers" in blob and isinstance(blob["servers"], dict):
        return dict(blob["servers"])
    return {}


def load_mcp_config(cwd: str | Path | None = None, home: str | Path | None = None) -> dict[str, Any]:
    """Load and merge MCP server configs from the 3 standard scopes.

    Args:
        cwd: Project root (defaults to ``Path.cwd()``).
        home: Override for the user-home directory; primarily for tests.

    Returns:
        ``{"servers": {<name>: <server_config>}}`` -- the same shape
        :meth:`MCPToolSource.from_config` already accepts.
    """
    cwd_path = Path(cwd) if cwd else Path.cwd()
    home_path = Path(home) if home else Path.home()

    user = _read(home_path / ".claude" / ".mcp.json")
    project = _read(cwd_path / ".claude" / ".mcp.json")
    local = _read(cwd_path / ".claude" / ".mcp.local.json")

    merged: dict[str, Any] = {}
    # Order matters: each successive scope overrides the previous one wholesale.
    for scope in (_servers(user), _servers(project), _servers(local)):
        merged.update(scope)
    return {"servers": merged}
