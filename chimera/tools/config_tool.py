"""Configuration tool for reading and writing Chimera/Mink settings.

The tool prefers M2-A's :mod:`chimera.mink.settings` adapter when
available; otherwise it falls back to direct JSON reads/writes against the
canonical settings paths for each scope.

Allowed top-level keys are restricted to a documented schema so the model
cannot insert arbitrary keys outside the parity surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

# Documented top-level keys (mirrors the Mink/CC-ecosystem settings.json schema + Chimera additions).
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "model",
        "permissions",
        "hooks",
        "mcp",
        "env",
        "keybindings",
        "agents",
        "skills",
        "compaction",
        "telemetry",
        "theme",
    }
)

VALID_SCOPES: frozenset[str] = frozenset({"user", "project", "local"})


def _settings_root() -> Path:
    """Return the user-scope settings root.

    Honors ``CHIMERA_SETTINGS_HOME`` for tests; otherwise ``~``.
    """
    override = os.environ.get("CHIMERA_SETTINGS_HOME")
    if override:
        return Path(override)
    return Path.home()


def _project_root() -> Path:
    """Return the project-scope root (cwd or override)."""
    override = os.environ.get("CHIMERA_PROJECT_ROOT")
    if override:
        return Path(override)
    return Path.cwd()


def _scope_path(scope: str) -> Path:
    """Resolve the settings.json path for ``scope``."""
    if scope == "user":
        return _settings_root() / ".claude" / "settings.json"
    if scope == "project":
        return _project_root() / ".claude" / "settings.json"
    if scope == "local":
        return _project_root() / ".claude" / "settings.local.json"
    raise ValueError(f"unknown scope {scope!r}")


def _load(scope: str) -> dict[str, Any]:
    """Load the raw settings dict for ``scope`` (empty if no file)."""
    try:
        from chimera.mink import settings as mink_settings  # type: ignore[attr-defined]

        load_scope = getattr(mink_settings, "load_scope", None)
        if load_scope is not None:
            return dict(load_scope(scope))
    except Exception:
        pass
    p = _scope_path(scope)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save(scope: str, data: dict[str, Any]) -> None:
    """Write the raw settings dict for ``scope``, creating parents."""
    try:
        from chimera.mink import settings as mink_settings  # type: ignore[attr-defined]

        save_scope = getattr(mink_settings, "save_scope", None)
        if save_scope is not None:
            save_scope(scope, data)
            return
    except Exception:
        pass
    p = _scope_path(scope)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _validate_key(key: str) -> str | None:
    """Return an error string if ``key`` is outside the documented schema."""
    top = key.split(".", 1)[0]
    if top not in ALLOWED_KEYS:
        return (
            f"key {key!r} is not in the documented schema "
            f"(allowed top-level: {sorted(ALLOWED_KEYS)})"
        )
    return None


def _get_nested(data: dict[str, Any], dotted: str) -> Any:
    """Retrieve a dotted key from ``data``, returning ``None`` if missing."""
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> None:
    """Set a dotted key in ``data``, creating intermediate dicts."""
    parts = dotted.split(".")
    cur: dict[str, Any] = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


class ConfigTool(BaseTool):
    """Read or write Chimera/Mink settings entries.

    Actions:
        * ``get`` — return the value of ``key`` in the given scope.
        * ``set`` — write ``value`` to ``key`` in the given scope.
        * ``list`` — dump all entries in the given scope.
    """

    name = "config"
    description = "Get/set/list Chimera or .claude settings entries."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set", "list"]},
            "key": {"type": "string", "description": "Dotted key path"},
            "value": {"description": "Value to set (any JSON-serialisable)"},
            "scope": {
                "type": "string",
                "enum": ["user", "project", "local"],
                "default": "project",
            },
        },
        "required": ["action"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        action = args["action"]
        scope = args.get("scope", "project")
        if scope not in VALID_SCOPES:
            return ToolResult(output="", error=f"invalid scope {scope!r}")
        if action == "list":
            return ToolResult(output=json.dumps(_load(scope), indent=2))
        key = args.get("key")
        if not isinstance(key, str) or not key:
            return ToolResult(output="", error="action requires non-empty 'key'")
        err = _validate_key(key)
        if err:
            return ToolResult(output="", error=err)
        if action == "get":
            val = _get_nested(_load(scope), key)
            return ToolResult(output=json.dumps(val))
        if action == "set":
            if "value" not in args:
                return ToolResult(output="", error="set requires 'value'")
            data = _load(scope)
            _set_nested(data, key, args["value"])
            _save(scope, data)
            return ToolResult(output=f"set {key} in {scope}")
        return ToolResult(output="", error=f"unknown action {action!r}")


__all__ = ["ConfigTool", "ALLOWED_KEYS", "VALID_SCOPES"]
