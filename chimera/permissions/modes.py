"""Permission modes controlling the overall approval behaviour.

Two enums live here:

* :class:`PermissionMode` — the **legacy** six-mode enum that the
  interactive REPL and the in-process permission checker pivot on
  (``default`` / ``plan`` / ``accept_edits`` / ``bypass_permissions`` /
  ``dont_ask`` / ``auto``). Untouched for backwards compatibility.
* :class:`ApprovalMode` — the **standard 5-mode** approval surface
  exposed via the ``--permission-mode`` CLI flag on the ferret, badger,
  and mink CLIs (``read-only`` / ``suggest`` / ``auto`` / ``yolo`` /
  ``strict``). Maps onto the existing
  :class:`~chimera.permissions.base.PermissionPolicy` presets via
  :func:`policy_for_mode`.

The 5-mode surface mirrors the cross-ecosystem ``--permission-mode``
flag carried by other coding-agent CLIs without naming any of them.
Three CLIs (ferret, badger, mink) ship the flag; the legacy ferret
``--approval`` flag (3 presets) and mink's pre-existing
``--permission-mode`` (4 ecosystem-parity choices) keep working —
:func:`approval_preset_to_mode` and :func:`legacy_mink_choice_to_mode`
translate them on the way in.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from chimera.permissions.base import PermissionAction, PermissionPolicy

__all__ = [
    "ApprovalMode",
    "AlwaysAskPolicy",
    "AutoEditPolicy",
    "PermissionMode",
    "approval_preset_to_mode",
    "legacy_mink_choice_to_mode",
    "parse_mode",
    "policy_for_mode",
]


class PermissionMode(Enum):
    """High-level mode that governs how permission checks behave.

    DEFAULT  – normal interactive behaviour (ask for dangerous ops).
    PLAN     – read-only planning; all writes denied.
    ACCEPT_EDITS – auto-approve file edits, ask for everything else.
    BYPASS   – skip all permission prompts (dangerous).
    DONT_ASK – deny anything that would normally prompt.
    AUTO     – fully autonomous; equivalent to BYPASS but logged.
    """

    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass_permissions"
    DONT_ASK = "dont_ask"
    AUTO = "auto"


# ---------------------------------------------------------------------------
# 5-mode approval surface (G3) — exposed on ferret/badger/mink CLIs
# ---------------------------------------------------------------------------


class ApprovalMode(str, Enum):
    """The five standard approval modes the ``--permission-mode`` flag exposes.

    From least to most permissive:

    * ``READ_ONLY`` — only the read whitelist (``read_file``, ``search``,
      ``list_files``, ``repo_map``) is allowed; every side-effecting tool
      is denied outright. Useful for plan/review runs against a sandbox.
    * ``SUGGEST`` — reads auto-approve; every write/edit/bash/git call is
      surfaced to the user for explicit approval (``ASK``). Closest to
      ferret's pre-existing ``--approval read-only`` *spirit* in the
      sense of "show your work before acting".
    * ``AUTO`` — reads + simple edits auto-approve; bash/git/destructive
      ops still ASK. Pairs well with a workspace-write sandbox.
    * ``YOLO`` — every tool call auto-approves. Equivalent to
      ``--approval full``. Use only inside a sandbox.
    * ``STRICT`` — every tool call (including reads) is surfaced for
      explicit approval. The most cautious end of the spectrum and the
      right pick for high-risk environments where even reading a file is
      something you want to confirm.

    Inheriting from ``str`` lets argparse ``choices=`` use the canonical
    spellings directly while keeping equality with plain strings cheap.
    """

    READ_ONLY = "read-only"
    SUGGEST = "suggest"
    AUTO = "auto"
    YOLO = "yolo"
    STRICT = "strict"


_MODE_ALIASES: dict[str, ApprovalMode] = {
    # Canonical spellings.
    "read-only": ApprovalMode.READ_ONLY,
    "suggest": ApprovalMode.SUGGEST,
    "auto": ApprovalMode.AUTO,
    "yolo": ApprovalMode.YOLO,
    "strict": ApprovalMode.STRICT,
    # Common alternate spellings.
    "read_only": ApprovalMode.READ_ONLY,
    "readonly": ApprovalMode.READ_ONLY,
    # Legacy ferret ``--approval`` values.
    "full": ApprovalMode.YOLO,
    # Legacy mink ``--permission-mode`` values (pre-G3).
    "default": ApprovalMode.SUGGEST,
    "acceptedits": ApprovalMode.AUTO,
    "accept-edits": ApprovalMode.AUTO,
    "accept_edits": ApprovalMode.AUTO,
    "bypasspermissions": ApprovalMode.YOLO,
    "bypass-permissions": ApprovalMode.YOLO,
    "bypass_permissions": ApprovalMode.YOLO,
    "plan": ApprovalMode.READ_ONLY,
}


def parse_mode(value: str | ApprovalMode) -> ApprovalMode:
    """Normalise a CLI string (or :class:`ApprovalMode`) to an :class:`ApprovalMode`.

    Accepts the five canonical 5-mode spellings, the underscore variants,
    the legacy ferret ``--approval`` strings (``read-only``/``auto``/
    ``full``), and the legacy mink ``--permission-mode`` strings
    (``default``/``acceptEdits``/``bypassPermissions``/``plan``).

    Args:
        value: Raw flag value from argparse, an env var, or a settings
            file. Whitespace and case are ignored.

    Returns:
        The matching :class:`ApprovalMode`.

    Raises:
        ValueError: If ``value`` is not a recognised mode/alias. The
            message lists the canonical 5-mode spellings so argparse
            error output stays actionable.
    """
    if isinstance(value, ApprovalMode):
        return value
    normalised = value.strip().lower()
    # Try direct enum match first, then alias map.
    for mode in ApprovalMode:
        if mode.value == normalised:
            return mode
    if normalised in _MODE_ALIASES:
        return _MODE_ALIASES[normalised]
    accepted = ", ".join(m.value for m in ApprovalMode)
    raise ValueError(
        f"Unknown permission mode {value!r}; expected one of: {accepted}"
    )


def approval_preset_to_mode(value: str) -> ApprovalMode:
    """Map a legacy ferret ``--approval`` value onto an :class:`ApprovalMode`.

    The legacy spellings (``read-only`` / ``auto`` / ``full``) round-trip
    through :func:`parse_mode`; this helper exists as a documented seam
    so callsites that wire back-compat can grep for it.
    """
    return parse_mode(value)


def legacy_mink_choice_to_mode(value: str) -> ApprovalMode:
    """Map a legacy mink ``--permission-mode`` choice onto an :class:`ApprovalMode`.

    mink shipped the flag with four ecosystem-parity choices
    (``default`` / ``acceptEdits`` / ``bypassPermissions`` / ``plan``).
    The mapping is:

    * ``default`` → :attr:`ApprovalMode.SUGGEST`
    * ``acceptEdits`` → :attr:`ApprovalMode.AUTO`
    * ``bypassPermissions`` → :attr:`ApprovalMode.YOLO`
    * ``plan`` → :attr:`ApprovalMode.READ_ONLY`
    """
    return parse_mode(value)


# ---------------------------------------------------------------------------
# Policies for the two new modes that aren't exact :mod:`presets` clones.
# ---------------------------------------------------------------------------


_READ_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "search",
    "list_files",
    "repo_map",
})

_EDIT_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "replace_in_file",
})


class AutoEditPolicy(PermissionPolicy):
    """Policy for :attr:`ApprovalMode.AUTO`.

    Reads and simple file edits resolve to :class:`PermissionAction.ALLOW`;
    bash/git/everything else resolves to :class:`PermissionAction.ASK`. The
    ``ASK`` outcome lets the harness defer to its prompt handler (or, in
    headless contexts, to a deny-by-default fallback).

    The read-tool set mirrors :attr:`ReadOnly.ALLOW_TOOLS` so AUTO never
    disagrees with READ_ONLY about what counts as a read.
    """

    READ_TOOLS: frozenset[str] = _READ_TOOLS
    EDIT_TOOLS: frozenset[str] = _EDIT_TOOLS

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        del args  # WHY: AUTO doesn't peek at tool args — name-only routing.
        if tool_name in self.READ_TOOLS:
            return PermissionAction.ALLOW
        if tool_name in self.EDIT_TOOLS:
            return PermissionAction.ALLOW
        return PermissionAction.ASK


class AlwaysAskPolicy(PermissionPolicy):
    """Policy for :attr:`ApprovalMode.STRICT`.

    Every tool call — including reads — resolves to
    :class:`PermissionAction.ASK`. This is the strictest mode the
    ``--permission-mode`` surface exposes; pair it with a non-interactive
    deny fallback for fully unattended runs.
    """

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        del tool_name, args  # WHY: STRICT asks unconditionally.
        return PermissionAction.ASK


def policy_for_mode(mode: ApprovalMode | str) -> PermissionPolicy:
    """Return the :class:`PermissionPolicy` corresponding to ``mode``.

    Accepts an :class:`ApprovalMode` or any string :func:`parse_mode`
    accepts (canonical / alias / legacy). New instances are returned per
    call so any per-policy mutable state stays per-CLI-invocation.

    Args:
        mode: An :class:`ApprovalMode` value, or a string spelling that
            :func:`parse_mode` recognises.

    Returns:
        A fresh :class:`PermissionPolicy` instance matching the mode.

    Raises:
        ValueError: If ``mode`` is not a recognised
            :class:`ApprovalMode` or alias.
    """
    # WHY: keep the import local so :mod:`chimera.permissions.modes`
    # remains importable even if a future presets refactor changes the
    # module's import-time deps.
    from chimera.permissions.presets import (
        AutoApprove,
        Interactive,
        ReadOnly,
    )

    resolved = mode if isinstance(mode, ApprovalMode) else parse_mode(mode)
    if resolved is ApprovalMode.READ_ONLY:
        return ReadOnly()
    if resolved is ApprovalMode.SUGGEST:
        return Interactive()
    if resolved is ApprovalMode.AUTO:
        return AutoEditPolicy()
    if resolved is ApprovalMode.YOLO:
        return AutoApprove()
    if resolved is ApprovalMode.STRICT:
        return AlwaysAskPolicy()
    # Defensive — every ApprovalMode member is handled above. A future
    # enum addition that forgets to extend this mapping should fail loudly.
    raise ValueError(f"Unknown approval mode: {resolved!r}")
