"""``chimera ferret mcp {add,list,remove}`` — manage MCP server launchers.

Persists a JSON registry of MCP server commands at
``~/.chimera/ferret/mcp_servers.json`` so the user can register the
launchers they want ferret to spawn for each project. The on-disk
schema mirrors the standard ``.mcp.json`` envelope::

    {
      "mcpServers": {
        "<name>": {
          "command": "<binary>",
          "args": ["arg1", "arg2", ...]
        }
      }
    }

Surfaces
--------

* ``ferret mcp add <name> <command...>`` — register a new launcher.
  The ``<command>`` is split on whitespace; the first token becomes
  ``command`` and the rest become ``args``. Re-adding an existing name
  overwrites with a stderr notice.
* ``ferret mcp list`` — print the configured launchers.
* ``ferret mcp remove <name>`` — drop a launcher.

Exit codes
----------

* ``0`` — operation succeeded (including ``list`` with no entries).
* ``2`` — usage error or missing ``<name>`` / ``<command>``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from chimera.config.paths import STATE_DIRNAME, store_path

__all__ = [
    "DEFAULT_MCP_CONFIG_PATH",
    "load_mcp_config",
    "save_mcp_config",
    "add_mcp_server",
    "remove_mcp_server",
    "list_mcp_servers",
    "run_mcp",
]


def _default_config_path() -> Path:
    """Return ``~/.chimera/ferret/mcp_servers.json`` honoring ``Path.home()``."""
    return store_path("ferret") / "mcp_servers.json"


# Module-level constant kept for ``__all__`` exposure; resolved lazily so
# tests that monkey-patch ``Path.home()`` see the patched value.
DEFAULT_MCP_CONFIG_PATH = f"~/{STATE_DIRNAME}/ferret/mcp_servers.json"


def load_mcp_config(path: Path | None = None) -> dict[str, Any]:
    """Load the persisted MCP server registry.

    Returns the canonical envelope (``{"mcpServers": {...}}``) even if
    the file is missing or malformed — callers can mutate ``["mcpServers"]``
    without a None-check.

    Args:
        path: Override the registry path; defaults to
            :func:`_default_config_path`.

    Returns:
        Parsed dict, or a fresh ``{"mcpServers": {}}`` envelope.
    """
    target = path or _default_config_path()
    if not target.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mcpServers": {}}
    if not isinstance(data, dict):
        return {"mcpServers": {}}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        data["mcpServers"] = {}
    return data


def save_mcp_config(
    config: dict[str, Any],
    path: Path | None = None,
) -> Path:
    """Atomically write *config* to disk; return the resolved path.

    The parent dir is created on demand and the file is written with
    ``0o600`` permissions because operators often store API keys inside
    the ``args`` array.
    """
    target = path or _default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(config, indent=2, sort_keys=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(blob + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        # Best-effort — Windows / restricted filesystems silently ignore.
        pass
    os.replace(tmp, target)
    return target


def add_mcp_server(
    name: str,
    command: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Register or overwrite a server entry.

    ``command`` is split on whitespace: the first token becomes the
    binary, the rest become ``args``. Overwriting an existing entry
    is allowed (callers can detect via
    :func:`load_mcp_config` first if they want to confirm).

    Args:
        name: Human-readable launcher name (used as the dict key).
        command: Whitespace-separated invocation string.
        path: Override the registry path.

    Returns:
        The added entry (``{"command": ..., "args": [...]}``).

    Raises:
        ValueError: When *name* or *command* is empty.
    """
    if not name:
        raise ValueError("ferret mcp add: name must be non-empty")
    if not command or not command.strip():
        raise ValueError("ferret mcp add: command must be non-empty")
    parts = command.strip().split()
    entry = {"command": parts[0], "args": parts[1:]}
    config = load_mcp_config(path)
    config.setdefault("mcpServers", {})[name] = entry
    save_mcp_config(config, path)
    return entry


def remove_mcp_server(
    name: str,
    *,
    path: Path | None = None,
) -> bool:
    """Drop *name* from the registry. Returns ``True`` when removed."""
    if not name:
        raise ValueError("ferret mcp remove: name must be non-empty")
    config = load_mcp_config(path)
    servers = config.setdefault("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    save_mcp_config(config, path)
    return True


def list_mcp_servers(path: Path | None = None) -> dict[str, Any]:
    """Return the ``{"mcpServers": ...}`` envelope as a dict."""
    config = load_mcp_config(path)
    servers = config.get("mcpServers") or {}
    return dict(servers)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _format_server_table(servers: dict[str, Any]) -> str:
    """Render the registry for stdout."""
    if not servers:
        return "(no MCP servers registered)\n"
    lines: list[str] = []
    name_width = max(len(name) for name in servers) if servers else 4
    for name, entry in sorted(servers.items()):
        if not isinstance(entry, dict):
            lines.append(f"{name.ljust(name_width)}  (malformed entry)")
            continue
        cmd = str(entry.get("command", ""))
        args = entry.get("args") or []
        joined = " ".join([cmd, *map(str, args)]).strip()
        lines.append(f"{name.ljust(name_width)}  {joined}")
    return "\n".join(lines) + "\n"


def run_mcp(args: argparse.Namespace) -> int:
    """Dispatch ``chimera ferret mcp {add,list,remove}``.

    Reads the action off ``args.sub_action`` and the per-action
    arguments off ``args.sub_target`` (name) and ``args.print_mode``
    (the cli currently has no 4th positional, so we accept the
    command via ``-p`` as a documented work-around when add is
    invoked from a non-interactive shell). When the cli has been
    extended with a 4th positional ``sub_extra``, that wins.
    """
    action = getattr(args, "sub_action", None)
    name = getattr(args, "sub_target", None)
    command = getattr(args, "sub_extra", None) or getattr(args, "print_mode", None)

    if action == "list":
        servers = list_mcp_servers()
        sys.stdout.write(_format_server_table(servers))
        return 0

    if action == "add":
        if not name or not command:
            sys.stderr.write(
                "ferret mcp add: requires <name> <command...>. "
                "Example: chimera ferret mcp add my-search "
                "'python -m chimera.mcp_servers.search_server'\n"
            )
            return 2
        try:
            entry = add_mcp_server(str(name), str(command))
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        sys.stdout.write(
            f"[ferret mcp add] {name} -> {entry['command']} "
            f"{' '.join(entry['args'])}\n"
        )
        return 0

    if action == "remove":
        if not name:
            sys.stderr.write(
                "ferret mcp remove: requires <name>. "
                "Run 'chimera ferret mcp list' to see registered servers.\n"
            )
            return 2
        try:
            removed = remove_mcp_server(str(name))
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        if not removed:
            sys.stderr.write(f"ferret mcp remove: no such server: {name!r}\n")
            return 2
        sys.stdout.write(f"[ferret mcp remove] {name}\n")
        return 0

    sys.stderr.write(
        f"ferret mcp: unknown action {action!r}. "
        "Use 'add', 'list', or 'remove'.\n"
    )
    return 2
