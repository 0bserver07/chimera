"""Ferret approval presets.

A single ``--approval`` flag selects the entire permission stance in one go,
contrasting with mink's fine-grained ``--allowed-tools`` posture. The IDE-first
OpenAI-flagship coding agent that ferret mirrors collapses approval choice
into three discrete presets — read-only, auto, full — and ferret follows
suit so users do not have to compose a permission ruleset by hand.

Mapping:

* ``READ_ONLY`` → :class:`chimera.permissions.presets.ReadOnly` — only the
  whitelisted read/search/list tools are allowed; writes, shell, and network
  are denied outright.
* ``AUTO`` → a composite stance that auto-approves the read whitelist and
  defers to :class:`~chimera.permissions.presets.Interactive` for the
  write/edit/bash family, so the user is asked exactly when a side-effect
  would land.
* ``FULL`` → :class:`~chimera.permissions.presets.AutoApprove` — every tool
  call is approved unconditionally (the "yolo" stance).

The CLI wires this via late-binding (``cli.py`` imports
:func:`policy_for_preset` lazily) so this module can land before ferret's
argparse front-end does. Stdlib + ``chimera.permissions`` only.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from chimera.permissions.base import PermissionAction, PermissionPolicy
from chimera.permissions.presets import (
    AutoApprove,
    Interactive,
    ReadOnly,
)

__all__ = [
    "ApprovalPreset",
    "AutoApprovalPolicy",
    "policy_for_preset",
    "preset_from_string",
]


class ApprovalPreset(Enum):
    """The three approval stances ferret exposes via ``--approval``.

    READ_ONLY — refuse every side-effecting tool; only allow reads/searches.
    AUTO      — auto-approve reads, ask interactively for writes/shell/edits.
    FULL      — auto-approve everything (use with care — typically paired
                 with a sandbox).
    """

    READ_ONLY = "read-only"
    AUTO = "auto"
    FULL = "full"


class AutoApprovalPolicy(PermissionPolicy):
    """Composite policy for ``--approval auto``.

    Whitelisted read tools resolve to :class:`PermissionAction.ALLOW`; every
    other tool defers to :class:`~chimera.permissions.presets.Interactive`
    so the harness can prompt the user (or be wired into a non-interactive
    deny in headless contexts).

    The whitelist intentionally mirrors
    :attr:`chimera.permissions.presets.ReadOnly.ALLOW_TOOLS` so ferret's AUTO
    preset never disagrees with READ_ONLY about what counts as a read.
    """

    READ_TOOLS: frozenset[str] = ReadOnly.ALLOW_TOOLS

    def __init__(self) -> None:
        # WHY: Compose AutoApprove + Interactive rather than reimplement the
        # ASK semantics here. If Interactive's READ_TOOLS / ASK_TOOLS sets
        # ever drift, AUTO inherits the drift for free.
        self._reads = AutoApprove()
        self._writes = Interactive()

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        if tool_name in self.READ_TOOLS:
            return self._reads.evaluate(tool_name, args)
        return self._writes.evaluate(tool_name, args)


def policy_for_preset(preset: ApprovalPreset) -> PermissionPolicy:
    """Return the :class:`PermissionPolicy` corresponding to ``preset``.

    Args:
        preset: One of :class:`ApprovalPreset`.

    Returns:
        A fresh :class:`PermissionPolicy` instance the caller owns. New
        instances (rather than module-level singletons) are returned so
        per-policy mutable state — should any preset gain it later — does
        not leak across CLI invocations.

    Raises:
        ValueError: If ``preset`` is not a recognised
            :class:`ApprovalPreset` member. This guards against future enum
            additions that forget to update the mapping.
    """
    if preset is ApprovalPreset.READ_ONLY:
        return ReadOnly()
    if preset is ApprovalPreset.AUTO:
        return AutoApprovalPolicy()
    if preset is ApprovalPreset.FULL:
        return AutoApprove()
    raise ValueError(f"Unknown approval preset: {preset!r}")


def preset_from_string(value: str) -> ApprovalPreset:
    """Translate a ``--approval`` CLI string into an :class:`ApprovalPreset`.

    Accepts the canonical hyphenated forms (``read-only``, ``auto``,
    ``full``) as well as the underscored alias ``read_only`` for parity
    with environment variables and config files.

    Args:
        value: Raw flag value from ``argparse``.

    Returns:
        The matching :class:`ApprovalPreset`.

    Raises:
        ValueError: If ``value`` is not one of the supported strings. The
            message lists the accepted forms so argparse error output is
            actionable.
    """
    normalised = value.strip().lower().replace("_", "-")
    for preset in ApprovalPreset:
        if preset.value == normalised:
            return preset
    accepted = ", ".join(p.value for p in ApprovalPreset)
    raise ValueError(
        f"Unknown approval preset {value!r}; expected one of: {accepted}"
    )
