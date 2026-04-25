"""Mink ``settings.json`` loader (compatible with the broader CC ecosystem).

Reads ``.claude/settings.json`` (system/user/project/local) plus
``.chimera/settings.json`` in the standard precedence order, deep-merges
with additive arrays for permissions/hooks/mcp, applies ``ANTHROPIC_*``
env overrides, and exposes a unified :class:`MinkSettings` dataclass
plus a :class:`~chimera.core.loop_config.LoopConfig` adapter.

See research/mink/12-cc-config.md, /23-cc-official-docs.md,
/25-implementation-plan.md §3 M2.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig

__all__ = [
    "MinkSettings",
    "MinkSettingsError",
    "Permissions",
    "load_mink_settings",
]


class MinkSettingsError(ValueError):
    """Raised when a settings file is malformed; message carries path + line/col."""


@dataclass
class Permissions:
    """The ``permissions`` block from a CC ``settings.json``.

    Attributes:
        default_mode: ``default`` | ``plan`` | ``acceptEdits`` | ``bypassPermissions`` | ``dontAsk``.
        allow: Tool patterns auto-allowed (e.g. ``"Bash(git status)"``).
        ask: Tool patterns that always prompt the user.
        deny: Tool patterns that are always blocked.
        additional_directories: Extra paths the agent may read/write.
        disable_bypass_permissions_mode: If ``True``, ``bypassPermissions`` is rejected.
    """

    default_mode: str = "default"
    allow: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    additional_directories: list[str] = field(default_factory=list)
    disable_bypass_permissions_mode: bool = False


@dataclass
class MinkSettings:
    """Unified, merged Mink settings; construct via :func:`load_mink_settings`.

    Attributes:
        permissions: Allow/ask/deny tool rules and default mode.
        hooks: Map of CC event names to lists of hook entries.
        mcp: ``{"servers": {<name>: {...}}}`` mirroring ``.mcp.json``.
        env: Process-environment overrides applied by the harness.
        model: Model alias (``opus``/``sonnet``/``haiku``) or ``provider/id``.
        output_format: ``text`` | ``json`` | ``stream-json``.
        agent: Default subagent name.
        enable_all_project_mcp_servers: Auto-trust project MCP servers.
        api_key_helper: Path to an executable that prints a fresh API key.
        auto_memory_enabled: If True, append observations to CLAUDE.md memory.
    """

    permissions: Permissions = field(default_factory=Permissions)
    hooks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    mcp: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    output_format: str | None = None
    agent: str | None = None
    enable_all_project_mcp_servers: bool = False
    api_key_helper: str | None = None
    auto_memory_enabled: bool = False

    def to_chimera_loop_config(self) -> LoopConfig:
        """Build a :class:`LoopConfig` with permissions wired from this MinkSettings.

        Allow/ask/deny lists become a :class:`~chimera.permissions.rule.PermissionRuleset`
        evaluated last-match-wins (deny appended last). Hook executor wiring is
        the responsibility of M2's hook task and is not done here.

        Returns:
            LoopConfig with ``permissions`` set; other slots remain None.
        """
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction
        from chimera.permissions.rule import PermissionRuleset, Rule

        rules: list[Rule] = []
        # Order matters: deny last so it always wins over allow/ask in
        # last-match-wins evaluation.
        for pat in self.permissions.allow:
            rules.append(_rule_from_pattern(pat, PermissionAction.ALLOW))
        for pat in self.permissions.ask:
            rules.append(_rule_from_pattern(pat, PermissionAction.ASK))
        for pat in self.permissions.deny:
            rules.append(_rule_from_pattern(pat, PermissionAction.DENY))

        default = (
            PermissionAction.ALLOW
            if self.permissions.default_mode in ("acceptEdits", "bypassPermissions")
            else PermissionAction.ASK
        )
        ruleset = PermissionRuleset(rules=rules, default=default)
        return LoopConfig(permissions=ruleset)


def _rule_from_pattern(pattern: str, action: Any) -> Any:
    """Parse a CC permission pattern (``Tool`` / ``Tool(x)`` / ``Tool(key:pat)``) into a Rule."""
    from chimera.permissions.rule import Rule

    if "(" not in pattern or not pattern.endswith(")"):
        return Rule(tool_pattern=pattern, action=action)
    tool, _, rest = pattern.partition("(")
    inner = rest[:-1]
    if ":" in inner:
        key, _, val = inner.partition(":")
        return Rule(tool_pattern=tool, action=action, arg_key=key.strip(), arg_pattern=val.strip())
    # Legacy content-match form: best-guess against args["command"].
    return Rule(tool_pattern=tool, action=action, arg_key="command", arg_pattern=inner.strip())


# ---------------------------------------------------------------------------
# Merge + load
# ---------------------------------------------------------------------------


# WHY: CC's reference implementation silently *replaces* arrays under
# permissions/hooks/mcp.servers when a higher-precedence layer redefines
# them, which breaks team-wide policy stacks (see research/mink/12-cc-config.md
# "Bug to mirror or avoid"). We diverge intentionally and deep-merge:
# arrays are concatenated (de-duplicated, order-preserving), dict values
# are merged recursively, and scalars are last-write-wins.
_ADDITIVE_ARRAY_PATHS: frozenset[str] = frozenset(
    {
        "permissions.allow",
        "permissions.ask",
        "permissions.deny",
        "permissions.additional_directories",
        "permissions.additionalDirectories",
    }
)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any], _path: str = "") -> dict[str, Any]:
    """Recursively merge overlay into base; arrays at additive paths concat-dedupe."""
    result: dict[str, Any] = dict(base)
    for key, ov in overlay.items():
        sub_path = f"{_path}.{key}" if _path else key
        if key in result and isinstance(result[key], dict) and isinstance(ov, dict):
            result[key] = _deep_merge(result[key], ov, sub_path)
        elif (
            key in result
            and isinstance(result[key], list)
            and isinstance(ov, list)
            and _is_additive(sub_path)
        ):
            merged: list[Any] = list(result[key])
            for item in ov:
                if item not in merged:
                    merged.append(item)
            result[key] = merged
        else:
            result[key] = ov
    return result


def _is_additive(path: str) -> bool:
    """True iff a list at this dotted path should be concat-deduped (vs replaced)."""
    if path in _ADDITIVE_ARRAY_PATHS:
        return True
    if path.startswith("hooks.") and path.count(".") == 1:
        return True
    if path == "mcp.servers":
        return True
    return False


def _load_json_file(path: Path) -> dict[str, Any]:
    """Parse JSON file; missing returns {}, malformed raises MinkSettingsError."""
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError as exc:
        raise MinkSettingsError(f"{path}: cannot read ({exc})") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MinkSettingsError(
            f"{path}: invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise MinkSettingsError(f"{path}: top-level JSON value must be an object")
    return data


# Map env-var name to the dotted settings path it overrides.
_ENV_OVERRIDES: dict[str, str] = {
    "ANTHROPIC_MODEL": "model",
    "ANTHROPIC_BASE_URL": "env.ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY": "env.ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN": "env.ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OUTPUT_FORMAT": "output_format",
}


def _apply_env_overrides(merged: dict[str, Any], environ: dict[str, str]) -> dict[str, Any]:
    """Apply ``ANTHROPIC_*`` (and friends) overrides on top of merged settings."""
    for env_name, dotted in _ENV_OVERRIDES.items():
        val = environ.get(env_name)
        if not val:
            continue
        parts = dotted.split(".")
        cursor: dict[str, Any] = merged
        for p in parts[:-1]:
            cursor = cursor.setdefault(p, {})
            if not isinstance(cursor, dict):
                cursor = {}
                break
        cursor[parts[-1]] = val
    return merged


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present key's value (camelCase/snake_case fallback chain)."""
    for k in keys:
        if k in d:
            return d[k]
    return default


def _build_settings(merged: dict[str, Any]) -> MinkSettings:
    """Convert a merged dict into a MinkSettings dataclass (camelCase or snake_case)."""
    p = merged.get("permissions") or {}
    perms = Permissions(
        default_mode=_pick(p, "defaultMode", "default_mode", default="default"),
        allow=list(p.get("allow") or []),
        ask=list(p.get("ask") or []),
        deny=list(p.get("deny") or []),
        additional_directories=list(_pick(p, "additionalDirectories", "additional_directories", default=[]) or []),
        disable_bypass_permissions_mode=bool(_pick(p, "disableBypassPermissionsMode", "disable_bypass_permissions_mode", default=False)),
    )
    return MinkSettings(
        permissions=perms,
        hooks=dict(merged.get("hooks") or {}),
        mcp=dict(merged.get("mcp") or {}),
        env=dict(merged.get("env") or {}),
        model=merged.get("model"),
        output_format=_pick(merged, "output_format", "outputFormat"),
        agent=merged.get("agent"),
        enable_all_project_mcp_servers=bool(_pick(merged, "enable_all_project_mcp_servers", "enableAllProjectMcpServers", default=False)),
        api_key_helper=_pick(merged, "api_key_helper", "apiKeyHelper"),
        auto_memory_enabled=bool(_pick(merged, "auto_memory_enabled", "autoMemoryEnabled", default=False)),
    )


def _system_defaults() -> dict[str, Any]:
    """Lowest-precedence layer; empty today, reserved for future hardcoded defaults."""
    return {}


def load_mink_settings(cwd: Path | None = None) -> MinkSettings:
    """Load + merge CC settings: system -> user -> project -> .local -> .chimera -> env.

    Args:
        cwd: Project root; defaults to ``Path.cwd()``.

    Returns:
        A fully merged :class:`MinkSettings` instance.

    Raises:
        MinkSettingsError: If any layer's JSON is malformed.
    """
    cwd = (cwd or Path.cwd()).resolve()
    home = Path(os.path.expanduser("~"))

    layers: list[dict[str, Any]] = [
        _system_defaults(),
        _load_json_file(home / ".claude" / "settings.json"),
        _load_json_file(cwd / ".claude" / "settings.json"),
        _load_json_file(cwd / ".claude" / "settings.local.json"),
        _load_json_file(cwd / ".chimera" / "settings.json"),
    ]
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = _deep_merge(merged, layer)
    merged = _apply_env_overrides(merged, dict(os.environ))
    return _build_settings(merged)
