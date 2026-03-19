"""Interactive approval UX for tool execution.

When a PermissionPolicy returns ASK, this module provides the actual
user-facing prompt. In REPL mode: shows tool name, arguments, risk level,
and prompts for y/n/always. In non-interactive mode: denies by default.

Inspired by Claude Code's permission prompts.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from chimera.permissions.base import PermissionAction
from chimera.permissions.risk import RiskLevel, classify_risk


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
