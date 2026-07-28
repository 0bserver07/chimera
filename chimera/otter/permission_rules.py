"""Declarative permission rules for ``chimera otter`` (W13 G6).

The upstream open-source coding agent ships a per-user permissions
table — ``Rule { permission, pattern, action: allow|deny|ask }`` —
persisted in SQLite and ingested from ``~/.opencode/config.json``.
Otter's parallel surface is this module: a JSON-on-disk ruleset
keyed off tool name + optional argument pattern, wired through the
existing :class:`chimera.permissions.rule.PermissionRuleset` (which
already does fnmatch matching with last-match-wins semantics).

On-disk format (``~/.chimera/permissions.json`` by default; honor
``$CHIMERA_PERMISSIONS_FILE`` for tests / sandboxed environments)::

    {
      "version": 1,
      "default": "ask",
      "rules": [
        {"tool": "read_file", "action": "allow"},
        {"tool": "bash", "arg_key": "command",
         "arg_pattern": "rm -rf*", "action": "deny"},
        {"tool": "*", "action": "ask"}
      ]
    }

* ``tool`` and ``arg_pattern`` are fnmatch-style globs; ``*`` matches
  every tool / argument value.
* ``action`` is one of ``"allow"``, ``"deny"``, ``"ask"`` (the three
  members of :class:`chimera.permissions.PermissionAction`).
* ``default`` is the action returned when no rule matches — matches the
  upstream's "deny by default" / "allow by default" knob.
* The rules list evaluates **last-match wins**, mirroring
  :class:`PermissionRuleset` and ``.gitignore`` semantics.

Public API:

* :class:`OtterPermissionRule` — dataclass mirroring the JSON shape.
* :func:`default_permissions_path` — resolve the on-disk path.
* :func:`load_permission_rules` — read + validate the file.
* :func:`save_permission_rules` — write a fresh copy (used by the
  ``permissions add/remove`` slash command).
* :func:`build_policy` — turn a list of rules into a
  :class:`PermissionRuleset` ready to install on a ``LoopConfig``.
* :func:`add_rule` / :func:`remove_rule` / :func:`list_rules` —
  mutation primitives the slash command and CLI subcommand both use.

Trademark hygiene: this module never names the upstream agent in any
user-visible string, per ``research/otter/SPEC.md``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chimera.permissions.base import PermissionAction
from chimera.permissions.rule import PermissionRuleset, Rule
from chimera.config.paths import chimera_home

__all__ = [
    "DEFAULT_VERSION",
    "OtterPermissionRule",
    "PermissionRulesError",
    "add_rule",
    "build_policy",
    "default_permissions_path",
    "list_rules",
    "load_permission_rules",
    "parse_action",
    "remove_rule",
    "save_permission_rules",
]


_logger = logging.getLogger(__name__)


# Schema version for the on-disk file. Bumped only when the JSON shape
# changes incompatibly; readers ignore unknown versions with a warning
# rather than refusing to load (forward compat).
DEFAULT_VERSION: int = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PermissionRulesError(ValueError):
    """Raised when the permissions file is malformed beyond best-effort recovery.

    Most parse problems degrade to an empty ruleset with a logged
    warning — the REPL prefers "no rules" over "crashed". This
    exception is reserved for callers that opt into strict validation
    via ``load_permission_rules(strict=True)``.
    """


# ---------------------------------------------------------------------------
# Rule dataclass
# ---------------------------------------------------------------------------


@dataclass
class OtterPermissionRule:
    """A single declarative permission rule in JSON-friendly form.

    Attributes:
        tool: fnmatch glob for the tool name. ``"*"`` matches every
            tool. Leading whitespace is stripped on parse.
        action: One of ``"allow"`` / ``"deny"`` / ``"ask"``.
        arg_key: Optional argument key to inspect alongside the tool
            name. When set together with ``arg_pattern``, the rule only
            matches if ``args[arg_key]`` also globs ``arg_pattern``
            (e.g. ``arg_key="command"``, ``arg_pattern="rm -rf*"`` to
            scope a deny to a specific bash invocation shape).
        arg_pattern: fnmatch glob applied to ``str(args[arg_key])``.
        description: Optional human-readable note. Surfaced by the
            slash command's ``list`` subcommand.
    """

    tool: str
    action: str
    arg_key: str | None = None
    arg_pattern: str | None = None
    description: str = ""

    def to_json(self) -> dict[str, Any]:
        """Render as a JSON-serialisable dict, omitting empty fields."""
        out: dict[str, Any] = {"tool": self.tool, "action": self.action}
        if self.arg_key is not None:
            out["arg_key"] = self.arg_key
        if self.arg_pattern is not None:
            out["arg_pattern"] = self.arg_pattern
        if self.description:
            out["description"] = self.description
        return out

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "OtterPermissionRule":
        """Build a rule from a JSON dict, raising if mandatory fields are missing."""
        if not isinstance(payload, dict):
            raise PermissionRulesError(f"rule must be a dict, got {type(payload).__name__}")
        tool = payload.get("tool")
        action = payload.get("action")
        if not isinstance(tool, str) or not tool:
            raise PermissionRulesError(f"rule missing 'tool' string: {payload!r}")
        if not isinstance(action, str) or not action:
            raise PermissionRulesError(f"rule missing 'action' string: {payload!r}")
        # Validate the action eagerly so we surface bad values at load time.
        parse_action(action)
        return cls(
            tool=tool.strip(),
            action=action.strip().lower(),
            arg_key=payload.get("arg_key") if isinstance(payload.get("arg_key"), str) else None,
            arg_pattern=(
                payload.get("arg_pattern")
                if isinstance(payload.get("arg_pattern"), str)
                else None
            ),
            description=str(payload.get("description") or ""),
        )

    def to_rule(self) -> Rule:
        """Translate into the existing :class:`Rule` for evaluation."""
        return Rule(
            tool_pattern=self.tool,
            action=parse_action(self.action),
            arg_key=self.arg_key,
            arg_pattern=self.arg_pattern,
            description=self.description,
        )


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------


_ACTION_ALIASES: dict[str, PermissionAction] = {
    "allow": PermissionAction.ALLOW,
    "deny": PermissionAction.DENY,
    "ask": PermissionAction.ASK,
    # Friendly aliases that match the upstream agent + cursor.
    "permit": PermissionAction.ALLOW,
    "block": PermissionAction.DENY,
    "prompt": PermissionAction.ASK,
    "confirm": PermissionAction.ASK,
}


def parse_action(value: str) -> PermissionAction:
    """Map a string to :class:`PermissionAction`.

    Accepts the canonical values (``"allow"`` / ``"deny"`` / ``"ask"``)
    plus a small set of intuitive aliases. Raises
    :class:`PermissionRulesError` on bad input so the loader can attach
    a useful "rule N has invalid action" message.
    """
    if not isinstance(value, str):
        raise PermissionRulesError(f"action must be a string, got {type(value).__name__}")
    norm = value.strip().lower()
    if norm in _ACTION_ALIASES:
        return _ACTION_ALIASES[norm]
    raise PermissionRulesError(
        f"unknown action {value!r}; expected one of {sorted(_ACTION_ALIASES)}"
    )


# ---------------------------------------------------------------------------
# File location
# ---------------------------------------------------------------------------


def default_permissions_path() -> Path:
    """Return the canonical permissions-file location.

    Honors ``$CHIMERA_PERMISSIONS_FILE`` (used by tests + sandboxed CI);
    falls back to ``~/.chimera/permissions.json``.
    """
    override = os.environ.get("CHIMERA_PERMISSIONS_FILE")
    if override:
        return Path(override)
    return chimera_home() / "permissions.json"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


@dataclass
class _RulesetFile:
    """In-memory mirror of the on-disk permissions JSON.

    Kept as a plain dataclass (rather than a class with methods) so
    callers can mutate ``rules`` directly without touching getter/setter
    boilerplate. ``save_permission_rules`` round-trips the dataclass.
    """

    version: int = DEFAULT_VERSION
    default: str = "ask"
    rules: list[OtterPermissionRule] = field(default_factory=list)


def load_permission_rules(
    path: Path | str | None = None,
    *,
    strict: bool = False,
) -> _RulesetFile:
    """Load permissions JSON from disk, returning a populated ruleset.

    Args:
        path: Override the file location. ``None`` resolves via
            :func:`default_permissions_path`.
        strict: When ``True``, a malformed file raises
            :class:`PermissionRulesError`. Otherwise (default) the
            failure is logged and an empty ruleset is returned so the
            REPL keeps booting.

    Returns:
        A :class:`_RulesetFile` — an empty ruleset when the file does
        not exist or cannot be parsed in non-strict mode.
    """
    target = Path(path) if path is not None else default_permissions_path()
    if not target.is_file():
        return _RulesetFile()

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        if strict:
            raise PermissionRulesError(f"cannot read {target}: {exc}") from exc
        _logger.warning("otter.permissions: cannot read %s: %s", target, exc)
        return _RulesetFile()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise PermissionRulesError(f"invalid JSON in {target}: {exc}") from exc
        _logger.warning("otter.permissions: invalid JSON in %s: %s", target, exc)
        return _RulesetFile()

    if not isinstance(payload, dict):
        if strict:
            raise PermissionRulesError(
                f"top-level value in {target} must be an object, got {type(payload).__name__}",
            )
        _logger.warning("otter.permissions: top-level value not an object in %s", target)
        return _RulesetFile()

    version = payload.get("version", DEFAULT_VERSION)
    if not isinstance(version, int):
        if strict:
            raise PermissionRulesError(f"version must be int in {target}, got {type(version).__name__}")
        _logger.warning("otter.permissions: non-int version in %s; using default", target)
        version = DEFAULT_VERSION

    raw_default = payload.get("default", "ask")
    try:
        # Eagerly validate but keep the string form so we round-trip cleanly.
        parse_action(raw_default if isinstance(raw_default, str) else "ask")
    except PermissionRulesError as exc:
        if strict:
            raise
        _logger.warning("otter.permissions: %s; falling back to ask", exc)
        raw_default = "ask"
    default = raw_default if isinstance(raw_default, str) else "ask"

    raw_rules = payload.get("rules", [])
    if not isinstance(raw_rules, list):
        if strict:
            raise PermissionRulesError(f"rules must be a list in {target}")
        _logger.warning("otter.permissions: rules is not a list in %s", target)
        raw_rules = []

    parsed: list[OtterPermissionRule] = []
    for idx, item in enumerate(raw_rules):
        try:
            parsed.append(OtterPermissionRule.from_json(item))
        except PermissionRulesError as exc:
            if strict:
                raise PermissionRulesError(f"rule {idx}: {exc}") from exc
            _logger.warning("otter.permissions: skipping rule %d: %s", idx, exc)

    return _RulesetFile(version=version, default=default, rules=parsed)


def save_permission_rules(
    ruleset: _RulesetFile,
    path: Path | str | None = None,
) -> Path:
    """Write *ruleset* back to disk in the canonical JSON shape.

    Creates the parent directory (``~/.chimera/`` by default) if
    missing. Returns the resolved path so callers can echo it.

    Args:
        ruleset: The ruleset to persist.
        path: Override the file location.

    Returns:
        The path the file was written to.
    """
    target = Path(path) if path is not None else default_permissions_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ruleset.version or DEFAULT_VERSION,
        "default": ruleset.default or "ask",
        "rules": [r.to_json() for r in ruleset.rules],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Policy construction
# ---------------------------------------------------------------------------


def build_policy(
    rules: list[OtterPermissionRule] | _RulesetFile,
    *,
    default: PermissionAction | str | None = None,
) -> PermissionRuleset:
    """Compose a :class:`PermissionRuleset` from declarative rules.

    Args:
        rules: Either a list of :class:`OtterPermissionRule` or a
            :class:`_RulesetFile` (in which case the file's ``default``
            is honored unless overridden).
        default: Override the default action. Accepts a
            :class:`PermissionAction` or a string alias.

    Returns:
        A :class:`PermissionRuleset` ready to install on a
        :class:`~chimera.core.loop_config.LoopConfig`.
    """
    if isinstance(rules, _RulesetFile):
        ruleset_default = rules.default
        rule_list = rules.rules
    else:
        ruleset_default = None
        rule_list = list(rules)

    if default is not None:
        resolved_default = (
            default if isinstance(default, PermissionAction) else parse_action(default)
        )
    elif ruleset_default is not None:
        resolved_default = parse_action(ruleset_default)
    else:
        resolved_default = PermissionAction.ASK

    return PermissionRuleset(
        rules=[r.to_rule() for r in rule_list],
        default=resolved_default,
    )


# ---------------------------------------------------------------------------
# Mutation primitives (used by /permissions slash + CLI subcommand)
# ---------------------------------------------------------------------------


def list_rules(path: Path | str | None = None) -> list[OtterPermissionRule]:
    """Return the rules currently on disk (empty list if file missing)."""
    return list(load_permission_rules(path).rules)


def add_rule(
    rule: OtterPermissionRule | dict[str, Any],
    *,
    path: Path | str | None = None,
) -> OtterPermissionRule:
    """Append *rule* to the on-disk file and return the persisted rule.

    Args:
        rule: Either a fully-built :class:`OtterPermissionRule` or a
            JSON-style dict (which is validated via
            :meth:`OtterPermissionRule.from_json`).
        path: Override the file location.

    Returns:
        The :class:`OtterPermissionRule` actually persisted.
    """
    materialised = (
        rule if isinstance(rule, OtterPermissionRule) else OtterPermissionRule.from_json(rule)
    )
    current = load_permission_rules(path)
    current.rules.append(materialised)
    save_permission_rules(current, path)
    return materialised


def remove_rule(
    index: int,
    *,
    path: Path | str | None = None,
) -> OtterPermissionRule | None:
    """Remove the rule at *index* (0-based) from the on-disk file.

    Args:
        index: Position in the rules list. Negative indexing is
            supported (Python's standard semantics).
        path: Override the file location.

    Returns:
        The removed rule, or ``None`` if the index was out of range
        (caller can surface a friendly error rather than crashing).
    """
    current = load_permission_rules(path)
    try:
        removed = current.rules.pop(index)
    except IndexError:
        return None
    save_permission_rules(current, path)
    return removed


# Re-export the internal dataclass under a public-ish alias so callers
# that want to introspect the file layout don't need to import the
# private name. Keeps the class itself private (single source of truth)
# while exposing a stable surface to slash + CLI consumers.
RulesetFile = _RulesetFile
__all__.append("RulesetFile")


# Avoid the "asdict imported but unused" warning when ruff scans the
# file — kept around because tests may want a quick dataclass dump.
_ = asdict
