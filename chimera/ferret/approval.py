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

This module also exposes :class:`MutablePermissionPolicy` — a thin proxy
whose inner policy can be hot-swapped at runtime without rebuilding the
agent's :class:`~chimera.core.loop_config.LoopConfig`. The ferret REPL's
``/approval`` slash command uses this proxy so a mid-session preset change
is picked up by the very next tool-call evaluation. The proxy is
thread-safe: ``set_inner`` and ``evaluate`` share a :class:`threading.Lock`
so a concurrent agent turn never sees a torn read.
"""
from __future__ import annotations

import threading
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
    "MutablePermissionPolicy",
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


class MutablePermissionPolicy(PermissionPolicy):
    """Hot-swappable proxy around an inner :class:`PermissionPolicy`.

    The ferret REPL builds its :class:`~chimera.core.loop_config.LoopConfig`
    once at agent creation, but the ``/approval`` slash command lets the
    user change the active preset mid-session. Rather than rebuild the
    agent (risky during a concurrent turn), we install one of these
    proxies as ``LoopConfig.permissions``. ``/approval`` then calls
    :meth:`set_inner` to swap the underlying policy; the next tool-call
    evaluation sees the new stance.

    The proxy is thread-safe — the executor in
    :mod:`chimera.core.tool_executor` may be running on a worker thread
    while the REPL handles the slash command on the main thread, so
    reads and writes funnel through a :class:`threading.Lock`.

    Args:
        inner: Initial policy to delegate to.

    Attributes:
        inner: The currently-active inner policy. Read via
            :meth:`get_inner` (locked) when concurrency matters.
    """

    def __init__(self, inner: PermissionPolicy) -> None:
        self._inner = inner
        self._lock = threading.Lock()

    @property
    def inner(self) -> PermissionPolicy:
        """Return the currently-active inner policy.

        Reads are locked so a concurrent ``set_inner`` cannot publish a
        half-constructed reference.
        """
        with self._lock:
            return self._inner

    def get_inner(self) -> PermissionPolicy:
        """Locked accessor for the current inner policy."""
        with self._lock:
            return self._inner

    def set_inner(self, policy: PermissionPolicy) -> PermissionPolicy:
        """Atomically replace the inner policy.

        Args:
            policy: The new :class:`PermissionPolicy` to delegate to.

        Returns:
            The previous inner policy (handy for tests / undo flows).

        Raises:
            TypeError: If *policy* is not a :class:`PermissionPolicy`.
        """
        if not isinstance(policy, PermissionPolicy):
            raise TypeError(
                f"set_inner requires a PermissionPolicy, got {type(policy).__name__}"
            )
        with self._lock:
            previous = self._inner
            self._inner = policy
            return previous

    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        """Delegate to the live inner policy.

        Snapshot the inner reference under the lock then evaluate
        outside the critical section so a slow inner policy never
        blocks a concurrent ``set_inner``.
        """
        with self._lock:
            inner = self._inner
        return inner.evaluate(tool_name, args)


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
