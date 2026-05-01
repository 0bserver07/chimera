"""Interactive permission prompt handler with pluggable callback."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
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
        *,
        hook_emitter: HookEmitter | None = None,
    ) -> None:
        """Construct the prompt handler.

        Args:
            callback: Async callable that prompts the user and returns
                one of ``allow_once``/``allow_always``/``deny_once``/
                ``deny_always``.
            denial_tracking: Tracker for repeated denials -> auto-deny.
            hook_emitter: Optional :class:`HookEmitter`.  When set, fires
                :data:`HookEvent.ELICITATION` immediately before the
                callback runs and :data:`HookEvent.ELICITATION_RESULT`
                immediately after — including on the auto-deny short
                circuits (no-callback / auto-deny-threshold).
        """
        self._callback = callback
        self._denial_tracking = denial_tracking or DenialTrackingState()
        self._hook_emitter = hook_emitter

    async def handle_ask(
        self,
        tool_name: str,
        input_args: dict[str, Any],
        decision: PermissionDecision,
    ) -> PermissionDecision:
        """Handle an ASK decision by prompting the user (or auto-denying after threshold)."""
        # Fire ELICITATION before any decision branch so external observers
        # can see "the harness is about to ask the user about X".
        await self._safe_emit(
            HookEvent.ELICITATION,
            tool_name=tool_name,
            tool_input=dict(input_args),
        )

        if self._denial_tracking.should_auto_deny(tool_name):
            result = PermissionDecision.deny(
                "Auto-denied after repeated rejections"
            )
            await self._safe_emit(
                HookEvent.ELICITATION_RESULT,
                tool_name=tool_name,
                tool_input=dict(input_args),
                tool_output="auto_deny",
            )
            return result

        if self._callback is None:
            # No interactive callback -- auto-deny
            result = PermissionDecision.deny("No interactive handler configured")
            await self._safe_emit(
                HookEvent.ELICITATION_RESULT,
                tool_name=tool_name,
                tool_input=dict(input_args),
                tool_output="no_callback",
            )
            return result

        try:
            choice = await self._callback(tool_name, input_args, decision)
        except Exception:
            await self._safe_emit(
                HookEvent.ELICITATION_RESULT,
                tool_name=tool_name,
                tool_input=dict(input_args),
                tool_output="error",
            )
            return PermissionDecision.deny("Permission prompt failed")

        # Surface the user's raw response on ELICITATION_RESULT so observers
        # can audit what the user picked, regardless of the decision shape.
        await self._safe_emit(
            HookEvent.ELICITATION_RESULT,
            tool_name=tool_name,
            tool_input=dict(input_args),
            tool_output=str(choice),
        )

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

    async def _safe_emit(self, event: HookEvent, **kwargs: Any) -> None:
        """Fire an emitter event without ever propagating exceptions."""
        if self._hook_emitter is None or not self._hook_emitter.active:
            return
        try:
            await self._hook_emitter.emit(event, **kwargs)
        except Exception:  # pragma: no cover - hooks must never break flow
            pass
