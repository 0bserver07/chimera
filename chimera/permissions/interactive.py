"""Interactive approval UX for tool execution.

When a PermissionPolicy returns ASK, this module provides the actual
user-facing prompt. In REPL mode: shows tool name, arguments, risk level,
and prompts for y/n/always. In non-interactive mode: denies by default.

Inspired by Claude Code's permission prompts.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from chimera.permissions.base import PermissionAction
from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.denial_tracking import DenialTrackingState
from chimera.permissions.risk import classify_risk


@dataclass
class ApprovalDecision:
    """Result of an interactive approval prompt."""

    action: PermissionAction
    always: bool = False  # "Always allow this tool"
    reason: str = ""


@dataclass
class ApprovalMemory:
    """Remembers "always allow" decisions across a session."""

    _allowed_tools: set[str] = field(default_factory=set)

    def is_always_allowed(self, tool_name: str) -> bool:
        """Check if this tool has been permanently allowed."""
        return tool_name in self._allowed_tools

    def remember_allow(self, tool_name: str) -> None:
        """Remember that this tool is always allowed."""
        self._allowed_tools.add(tool_name)

    def clear(self) -> None:
        """Reset all remembered decisions."""
        self._allowed_tools.clear()


class InteractiveApprover:
    """Prompts the user for tool execution approval.

    Args:
        interactive: Whether to actually prompt (False = auto-deny).
        memory: Shared memory for "always allow" decisions.
        output: File-like object for output (default: stderr).
        input_fn: Function to read user input (default: builtin input).
    """

    def __init__(
        self,
        interactive: bool = True,
        memory: ApprovalMemory | None = None,
        output: Any = None,
        input_fn: Any = None,
    ) -> None:
        self._interactive = interactive
        self._memory = memory or ApprovalMemory()
        self._output = output or sys.stderr
        self._input_fn = input_fn or input

    def prompt(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> ApprovalDecision:
        """Prompt the user for approval of a tool call.

        Args:
            tool_name: Name of the tool requesting execution.
            args: Tool call arguments.

        Returns:
            ApprovalDecision with the user's choice.
        """
        # Check memory first
        if self._memory.is_always_allowed(tool_name):
            return ApprovalDecision(action=PermissionAction.ALLOW, always=True)

        # Non-interactive mode: deny
        if not self._interactive:
            return ApprovalDecision(action=PermissionAction.DENY, reason="non-interactive")

        # Classify risk
        risk_level, risk_reason = classify_risk(tool_name, args)

        # Display prompt
        self._output.write(f"\n{'='*60}\n")
        self._output.write(f"  Tool: {tool_name}\n")
        self._output.write(f"  Risk: {risk_level.name} ({risk_reason})\n")
        if args:
            for key, value in args.items():
                display = str(value)
                if len(display) > 100:
                    display = display[:100] + "..."
                self._output.write(f"  {key}: {display}\n")
        self._output.write(f"{'='*60}\n")
        self._output.write("  Allow? [y]es / [n]o / [a]lways: ")
        self._output.flush()

        try:
            choice = self._input_fn().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return ApprovalDecision(action=PermissionAction.DENY, reason="interrupted")

        if choice in ("y", "yes"):
            return ApprovalDecision(action=PermissionAction.ALLOW)
        elif choice in ("a", "always"):
            self._memory.remember_allow(tool_name)
            return ApprovalDecision(action=PermissionAction.ALLOW, always=True)
        else:
            return ApprovalDecision(action=PermissionAction.DENY, reason="user denied")


class InteractivePermissionHandler:
    """Async handler for interactive permission prompts using the new
    PermissionDecision-based flow.

    Unlike :class:`InteractiveApprover`, this handler works with
    :class:`PermissionDecision` (not :class:`PermissionAction`) and supports
    denial tracking and pluggable async callbacks.
    """

    async def prompt(
        self,
        tool_name: str,
        input_args: dict[str, Any],
        decision: PermissionDecision,
        *,
        denial_tracking: DenialTrackingState | None = None,
        prompt_callback: Callable[..., Awaitable[str]] | None = None,
    ) -> PermissionDecision:
        """Prompt for interactive approval.

        Args:
            tool_name:        Name of the tool requesting permission.
            input_args:       The tool's input arguments.
            decision:         The current (ASK) decision from the checker.
            denial_tracking:  Optional denial tracker for auto-deny logic.
            prompt_callback:  Async callback ``(tool_name, input_args, decision) -> str``
                              returning one of ``"allow_once"``, ``"allow_always"``,
                              ``"deny_once"``, ``"deny_always"``.

        Returns:
            A final :class:`PermissionDecision`.
        """
        # Auto-deny after repeated rejections
        if denial_tracking and denial_tracking.should_auto_deny(tool_name):
            return PermissionDecision.deny("Auto-denied after repeated rejections")

        # No interactive handler available -> deny
        if prompt_callback is None:
            return PermissionDecision.deny("No interactive handler available")

        choice = await prompt_callback(tool_name, input_args, decision)

        if choice == "allow_once":
            return PermissionDecision.allow()
        elif choice == "allow_always":
            return PermissionDecision.allow(reason=DecisionReason.rule(tool_name))
        elif choice == "deny_once":
            if denial_tracking:
                denial_tracking.record_denial(tool_name)
            return PermissionDecision.deny("User denied")
        elif choice == "deny_always":
            if denial_tracking:
                denial_tracking.record_denial(tool_name)
            return PermissionDecision.deny("User denied permanently")
        else:
            return PermissionDecision.deny("User cancelled")
