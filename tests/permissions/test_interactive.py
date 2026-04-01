"""Tests for chimera.permissions.interactive — InteractivePermissionHandler."""
from __future__ import annotations

import pytest

from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.denial_tracking import DenialTrackingState
from chimera.permissions.interactive import InteractivePermissionHandler
from chimera.permissions.rules import PermissionBehavior


def _make_decision() -> PermissionDecision:
    """Create a baseline ASK decision for use in tests."""
    return PermissionDecision.ask(message="Need approval")


class TestInteractivePermissionHandlerAutoDeny:
    @pytest.mark.asyncio
    async def test_auto_deny_after_repeated_rejections(self) -> None:
        handler = InteractivePermissionHandler()
        tracking = DenialTrackingState(max_denials=2)
        tracking.record_denial("Bash")
        tracking.record_denial("Bash")

        decision = await handler.prompt(
            "Bash", {"command": "ls"}, _make_decision(),
            denial_tracking=tracking,
        )
        assert decision.behavior == PermissionBehavior.DENY
        assert "Auto-denied" in decision.message

    @pytest.mark.asyncio
    async def test_no_auto_deny_below_threshold(self) -> None:
        handler = InteractivePermissionHandler()
        tracking = DenialTrackingState(max_denials=3)
        tracking.record_denial("Bash")

        async def _callback(tool_name, input_args, decision):
            return "allow_once"

        decision = await handler.prompt(
            "Bash", {"command": "ls"}, _make_decision(),
            denial_tracking=tracking,
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.ALLOW


class TestInteractivePermissionHandlerNoCallback:
    @pytest.mark.asyncio
    async def test_deny_when_no_callback(self) -> None:
        handler = InteractivePermissionHandler()
        decision = await handler.prompt(
            "Bash", {"command": "ls"}, _make_decision(),
        )
        assert decision.behavior == PermissionBehavior.DENY
        assert "No interactive handler" in decision.message


class TestInteractivePermissionHandlerAllowOnce:
    @pytest.mark.asyncio
    async def test_allow_once(self) -> None:
        handler = InteractivePermissionHandler()

        async def _callback(tool_name, input_args, decision):
            return "allow_once"

        decision = await handler.prompt(
            "Bash", {"command": "ls"}, _make_decision(),
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.ALLOW


class TestInteractivePermissionHandlerAllowAlways:
    @pytest.mark.asyncio
    async def test_allow_always_has_rule_reason(self) -> None:
        handler = InteractivePermissionHandler()

        async def _callback(tool_name, input_args, decision):
            return "allow_always"

        decision = await handler.prompt(
            "Bash", {}, _make_decision(),
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.ALLOW
        assert decision.reason is not None
        assert decision.reason.type == "rule"


class TestInteractivePermissionHandlerDenyOnce:
    @pytest.mark.asyncio
    async def test_deny_once_records_tracking(self) -> None:
        handler = InteractivePermissionHandler()
        tracking = DenialTrackingState(max_denials=5)

        async def _callback(tool_name, input_args, decision):
            return "deny_once"

        decision = await handler.prompt(
            "Bash", {"command": "rm -rf /"}, _make_decision(),
            denial_tracking=tracking,
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.DENY
        assert decision.message == "User denied"
        # Verify tracking was updated
        assert tracking._counts[("Bash", None)] == 1

    @pytest.mark.asyncio
    async def test_deny_once_without_tracking(self) -> None:
        handler = InteractivePermissionHandler()

        async def _callback(tool_name, input_args, decision):
            return "deny_once"

        # Should not raise even without tracking
        decision = await handler.prompt(
            "Bash", {}, _make_decision(),
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.DENY


class TestInteractivePermissionHandlerDenyAlways:
    @pytest.mark.asyncio
    async def test_deny_always_records_tracking(self) -> None:
        handler = InteractivePermissionHandler()
        tracking = DenialTrackingState(max_denials=5)

        async def _callback(tool_name, input_args, decision):
            return "deny_always"

        decision = await handler.prompt(
            "Bash", {}, _make_decision(),
            denial_tracking=tracking,
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.DENY
        assert "permanently" in decision.message
        assert tracking._counts[("Bash", None)] == 1


class TestInteractivePermissionHandlerUnknownChoice:
    @pytest.mark.asyncio
    async def test_unknown_choice_denies(self) -> None:
        handler = InteractivePermissionHandler()

        async def _callback(tool_name, input_args, decision):
            return "some_unknown_value"

        decision = await handler.prompt(
            "Bash", {}, _make_decision(),
            prompt_callback=_callback,
        )
        assert decision.behavior == PermissionBehavior.DENY
        assert "cancelled" in decision.message
