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
        keybindings: Map of action name to key sequence
            (e.g. ``{"submit": "ctrl-d", "cancel": "ctrl-c"}``).
            Consumed by REPL key handling
            (``chimera/cli/code.py`` + ``chimera/mink/cli.py``).
        output_styles: Per-style configs keyed by style name; each value
            mirrors CC's ``outputStyles[name]`` block (e.g.
            ``{"theme": "monokai", "max_width": 120}``). Consumed by
            ``chimera/cli/render.py``.
        statusline: Custom REPL status line. ``False`` disables, ``True``
            enables the default. A dict carries
            ``{"command": "...", "enabled": true, "format": "..."}``
            for the REPL bottom bar.
        theme: Visual theme — ``"dark"`` / ``"light"`` / ``"auto"`` /
            ``"<custom-name>"``. Consumed by ``chimera/cli/render.py``.
        cleanup_period_days: Number of days after which transcripts /
            session files are auto-purged.
        include_co_authored_by: If ``True``, append ``Co-Authored-By:
            Claude`` trailers when committing through chimera.
        force_login_method: One of ``"oauth"`` / ``"api_key"`` /
            ``"console"`` to lock the auth mechanism.
        auto_updates: Whether the CLI may self-update on startup.
        verbose: If ``True``, the CLI defaults to verbose logging.
        install_method: How the CLI was installed
            (``"brew"`` / ``"pipx"`` / ``"uv"`` / ``"manual"``).
        preferred_notif_channel: Where lifecycle notifications go
            (``"terminal"`` / ``"system"`` / ``"slack"`` / ``"webhook"``).
        aws_auth_refresh: Optional command run to refresh AWS Bedrock
            credentials before each session.
        enabled_mcp_json_servers: Allow-list of ``.mcp.json`` server
            names that auto-load.
        disabled_mcp_json_servers: Deny-list of ``.mcp.json`` server
            names that never load (overrides the allow-list).
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
    # W13-G14 — settings.json key expansion.
    keybindings: dict[str, str] = field(default_factory=dict)
    output_styles: dict[str, dict[str, Any]] = field(default_factory=dict)
    statusline: bool | dict[str, Any] | None = None
    theme: str | None = None
    cleanup_period_days: int | None = None
    include_co_authored_by: bool = True
    force_login_method: str | None = None
    auto_updates: bool = True
    verbose: bool = False
    install_method: str | None = None
    preferred_notif_channel: str | None = None
    aws_auth_refresh: str | None = None
    enabled_mcp_json_servers: list[str] = field(default_factory=list)
    disabled_mcp_json_servers: list[str] = field(default_factory=list)

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
    # W13-G14 — additive MCP server allow/deny lists.
    if path in {
        "enabledMcpjsonServers",
        "disabledMcpjsonServers",
        "enabled_mcp_json_servers",
        "disabled_mcp_json_servers",
    }:
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

    # W13-G14 — extra CC keys.
    raw_keybindings = _pick(merged, "keybindings", default={}) or {}
    keybindings = {
        str(k): str(v) for k, v in raw_keybindings.items()
        if isinstance(v, str)
    } if isinstance(raw_keybindings, dict) else {}

    raw_output_styles = _pick(merged, "outputStyles", "output_styles", default={}) or {}
    output_styles: dict[str, dict[str, Any]] = {}
    if isinstance(raw_output_styles, dict):
        for name, cfg in raw_output_styles.items():
            if isinstance(cfg, dict):
                output_styles[str(name)] = dict(cfg)

    raw_statusline = _pick(merged, "statusline", "statuslineCommand", "statusLine", default=None)
    statusline: bool | dict[str, Any] | None
    if isinstance(raw_statusline, bool):
        statusline = raw_statusline
    elif isinstance(raw_statusline, dict):
        statusline = dict(raw_statusline)
    elif isinstance(raw_statusline, str):
        # CC's statuslineCommand was a bare string; promote into the dict shape.
        statusline = {"command": raw_statusline, "enabled": True}
    else:
        statusline = None

    raw_enabled = _pick(merged, "enabledMcpjsonServers", "enabled_mcp_json_servers", default=[]) or []
    raw_disabled = _pick(merged, "disabledMcpjsonServers", "disabled_mcp_json_servers", default=[]) or []
    enabled_mcp = [str(x) for x in raw_enabled] if isinstance(raw_enabled, list) else []
    disabled_mcp = [str(x) for x in raw_disabled] if isinstance(raw_disabled, list) else []

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
        keybindings=keybindings,
        output_styles=output_styles,
        statusline=statusline,
        theme=_pick(merged, "theme"),
        cleanup_period_days=_pick(merged, "cleanupPeriodDays", "cleanup_period_days"),
        include_co_authored_by=bool(_pick(merged, "includeCoAuthoredBy", "include_co_authored_by", default=True)),
        force_login_method=_pick(merged, "forceLoginMethod", "force_login_method"),
        auto_updates=bool(_pick(merged, "autoUpdates", "auto_updates", default=True)),
        verbose=bool(_pick(merged, "verbose", default=False)),
        install_method=_pick(merged, "installMethod", "install_method"),
        preferred_notif_channel=_pick(merged, "preferredNotifChannel", "preferred_notif_channel"),
        aws_auth_refresh=_pick(merged, "awsAuthRefresh", "aws_auth_refresh"),
        enabled_mcp_json_servers=enabled_mcp,
        disabled_mcp_json_servers=disabled_mcp,
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
    settings = _build_settings(merged)

    # Fire CONFIG_CHANGE once settings have been (re)materialised. Hot reloads
    # call this same function, so observers see one event per refresh. Best-
    # effort: a missing global emitter or a hook error is swallowed.
    _emit_config_change(cwd)

    return settings


def _emit_config_change(cwd: Path) -> None:
    """Fire :data:`HookEvent.CONFIG_CHANGE` via the global emitter.

    No-op when no global emitter has been registered, so this function is
    safe to call from import-time / startup code paths.
    """
    try:
        from chimera.hooks.emitter import get_global_emitter
        from chimera.hooks.events import HookEvent
        emitter = get_global_emitter()
        if emitter.active:
            emitter.emit_sync(
                HookEvent.CONFIG_CHANGE,
                tool_name="mink.settings",
                tool_input={"cwd": str(cwd)},
            )
    except Exception:
        pass
