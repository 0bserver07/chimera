# chimera/permissions/rule.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chimera.permissions.base import PermissionAction, PermissionPolicy
from chimera.permissions.patterns import matches_pattern

__all__ = ["Rule", "PermissionRuleset"]


@dataclass
class Rule:
    """A single permission rule that matches tool invocations by glob pattern.

    Rules are evaluated in order; the *last* matching rule wins (like ``.gitignore``).

    Parameters
    ----------
    tool_pattern:
        Glob pattern for the tool name (e.g. ``"bash"``, ``"write_*"``, ``"*"``).
    action:
        The permission action to apply when this rule matches.
    arg_key:
        Optional argument key to inspect.  When set together with *arg_pattern*,
        the rule only matches if ``args[arg_key]`` also matches *arg_pattern*.
    arg_pattern:
        Glob pattern for the value of ``args[arg_key]``.
    description:
        Human-readable description of why this rule exists.
    """

    tool_pattern: str
    action: PermissionAction
    arg_key: str | None = None
    arg_pattern: str | None = None
    description: str = ""


class PermissionRuleset(PermissionPolicy):
    """Ordered list of :class:`Rule` objects evaluated last-match-wins.

    Parameters
    ----------
    rules:
        Rules to evaluate in order.  The **last** matching rule determines the
        returned action.
    default:
        Action returned when no rule matches.  Defaults to
        :attr:`PermissionAction.ASK`.
    """

    def __init__(
        self,
        rules: list[Rule],
        default: PermissionAction = PermissionAction.ASK,
    ) -> None:
        self._rules = list(rules)
        self._default = default

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        """Return the action of the last matching rule, or *default* if none match."""
        result = self._default
        for rule in self._rules:
            if not matches_pattern(tool_name, rule.tool_pattern):
                continue
            # If rule specifies an arg constraint, check it too.
            if rule.arg_key is not None and rule.arg_pattern is not None:
                arg_value = args.get(rule.arg_key)
                if arg_value is None or not matches_pattern(str(arg_value), rule.arg_pattern):
                    continue
            result = rule.action
        return result
