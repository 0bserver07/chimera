"""Immutable snapshot of the permission state passed to the checker."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import RuleSource

__all__ = ["PermissionContext"]


@dataclass(frozen=True)
class PermissionContext:
    """All permission-related state needed for a single check.

    This is a frozen (immutable) dataclass so it can be shared safely
    across concurrent checks without locking.

    Attributes:
        mode:                   The active permission mode.
        allow_rules:            Rules that explicitly allow tools, keyed by source.
        deny_rules:             Rules that explicitly deny tools, keyed by source.
        ask_rules:              Rules that require user confirmation, keyed by source.
        additional_working_dirs: Extra directories the agent is allowed to modify.
        is_bypass_available:    Whether the user has unlocked bypass capability.
        should_avoid_prompts:   Hint to avoid prompts (e.g. CI environments).
        pre_plan_mode:          The mode that was active before switching to PLAN.
    """

    mode: PermissionMode
    allow_rules: dict[RuleSource, list[str]] = field(default_factory=dict)
    deny_rules: dict[RuleSource, list[str]] = field(default_factory=dict)
    ask_rules: dict[RuleSource, list[str]] = field(default_factory=dict)
    additional_working_dirs: frozenset[str] = field(default_factory=frozenset)
    is_bypass_available: bool = False
    should_avoid_prompts: bool = False
    pre_plan_mode: PermissionMode | None = None
