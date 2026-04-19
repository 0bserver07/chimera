"""Interactive permission prompt handler with pluggable callback."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.denial_tracking import DenialTrackingState

PromptCallback = Callable[[str, dict[str, Any], PermissionDecision], Awaitable[str]]
# Callback receives (tool_name, input_args, decision) -> returns
# "allow_once"|"allow_always"|"deny_once"|"deny_always"

__all__ = ["PromptCallback", "PermissionPromptHandler"]


class PermissionPromptHandler:
    """Handles interactive permission prompts with pluggable callback."""

    def __init__(
        self,
        callback: PromptCallback | None = None,
        denial_tracking: DenialTrackingState | None = None,
    ) -> None:
        self._callback = callback
        self._denial_tracking = denial_tracking or DenialTrackingState()

    async def handle_ask(
        self,
        tool_name: str,
        input_args: dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """Handle an ASK decision by prompting the user (or auto-denying after threshold)."""
        if self._denial_tracking.should_auto_deny(tool_name):
            return PermissionDecision.deny("Auto-denied after repeated rejections")

        if self._callback is None:
            # No interactive callback -- auto-deny
            return PermissionDecision.deny("No interactive handler configured")

        try:
            choice = await self._callback(tool_name, input_args, decision)
        except Exception:
            return PermissionDecision.deny("Permission prompt failed")

        if choice == "allow_once":
            return PermissionDecision.allow()
        elif choice == "allow_always":
            return PermissionDecision.allow(reason=DecisionReason.rule(tool_name))
        elif choice == "deny_once":
            self._denial_tracking.record_denial(tool_name)
            return PermissionDecision.deny("User denied")
        elif choice == "deny_always":
            self._denial_tracking.record_denial(tool_name)
            return PermissionDecision.deny("User denied permanently")
        else:
            return PermissionDecision.deny("User cancelled")
