"""Tests for chimera.permissions.prompt_handler — interactive permission prompts."""
from __future__ import annotations

import pytest

from chimera.permissions.decisions import PermissionDecision
from chimera.permissions.denial_tracking import DenialTrackingState
from chimera.permissions.prompt_handler import PermissionPromptHandler
from chimera.permissions.rules import PermissionBehavior


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ask_decision(msg: str = "Needs approval") -> PermissionDecision:
    return PermissionDecision.ask(msg)


# ---------------------------------------------------------------------------
# Tests: no callback configured
# ---------------------------------------------------------------------------


class TestNoCallback:
    @pytest.mark.asyncio
    async def test_no_callback_denies(self):
        """Without a callback, handle_ask should auto-deny."""
        handler = PermissionPromptHandler()
        result = await handler.handle_ask("Bash", {"command": "ls"}, _ask_decision())
        assert result.behavior is PermissionBehavior.DENY
        assert "No interactive handler" in result.message


# ---------------------------------------------------------------------------
# Tests: auto-deny after repeated rejections
# ---------------------------------------------------------------------------


class TestAutoDeny:
    @pytest.mark.asyncio
    async def test_auto_deny_after_threshold(self):
        """After max_denials rejections, should auto-deny without prompting."""
        tracking = DenialTrackingState(max_denials=2)
        tracking.record_denial("Bash")
        tracking.record_denial("Bash")

        # Callback should NOT be called — auto-deny kicks in first
        called = False

        async def callback(tool_name, input_args, decision):
            nonlocal called
            called = True
            return "allow_once"

        handler = PermissionPromptHandler(callback=callback, denial_tracking=tracking)
        result = await handler.handle_ask("Bash", {"command": "rm -rf /"}, _ask_decision())

        assert result.behavior is PermissionBehavior.DENY
        assert "Auto-denied" in result.message
        assert not called

    @pytest.mark.asyncio
    async def test_below_threshold_still_prompts(self):
        """Below the threshold, the callback should still be invoked."""
        tracking = DenialTrackingState(max_denials=3)
        tracking.record_denial("Bash")  # Only 1 denial, threshold=3

        async def callback(tool_name, input_args, decision):
            return "allow_once"

        handler = PermissionPromptHandler(callback=callback, denial_tracking=tracking)
        result = await handler.handle_ask("Bash", {"command": "ls"}, _ask_decision())

        assert result.behavior is PermissionBehavior.ALLOW


# ---------------------------------------------------------------------------
# Tests: callback returns allow
# ---------------------------------------------------------------------------


class TestCallbackAllow:
    @pytest.mark.asyncio
    async def test_allow_once(self):
        async def callback(tool_name, input_args, decision):
            return "allow_once"

        handler = PermissionPromptHandler(callback=callback)
        result = await handler.handle_ask("Bash", {"command": "ls"}, _ask_decision())

        assert result.behavior is PermissionBehavior.ALLOW

    @pytest.mark.asyncio
    async def test_allow_always(self):
        async def callback(tool_name, input_args, decision):
            return "allow_always"

        handler = PermissionPromptHandler(callback=callback)
        result = await handler.handle_ask("Bash", {"command": "ls"}, _ask_decision())

        assert result.behavior is PermissionBehavior.ALLOW
        assert result.reason is not None
        assert result.reason.type == "rule"


# ---------------------------------------------------------------------------
# Tests: callback returns deny
# ---------------------------------------------------------------------------


class TestCallbackDeny:
    @pytest.mark.asyncio
    async def test_deny_once(self):
        tracking = DenialTrackingState(max_denials=5)

        async def callback(tool_name, input_args, decision):
            return "deny_once"

        handler = PermissionPromptHandler(callback=callback, denial_tracking=tracking)
        result = await handler.handle_ask("Bash", {"command": "rm"}, _ask_decision())

        assert result.behavior is PermissionBehavior.DENY
        assert "User denied" in result.message
        # Should have recorded the denial
        assert tracking._counts[("Bash", None)] == 1

    @pytest.mark.asyncio
    async def test_deny_always(self):
        tracking = DenialTrackingState(max_denials=5)

        async def callback(tool_name, input_args, decision):
            return "deny_always"

        handler = PermissionPromptHandler(callback=callback, denial_tracking=tracking)
        result = await handler.handle_ask("Write", {}, _ask_decision())

        assert result.behavior is PermissionBehavior.DENY
        assert "permanently" in result.message
        assert tracking._counts[("Write", None)] == 1

    @pytest.mark.asyncio
    async def test_unknown_choice_denies(self):
        """An unrecognized callback return should result in deny."""
        async def callback(tool_name, input_args, decision):
            return "something_weird"

        handler = PermissionPromptHandler(callback=callback)
        result = await handler.handle_ask("Bash", {}, _ask_decision())

        assert result.behavior is PermissionBehavior.DENY
        assert "cancelled" in result.message


# ---------------------------------------------------------------------------
# Tests: callback raises exception
# ---------------------------------------------------------------------------


class TestCallbackException:
    @pytest.mark.asyncio
    async def test_callback_exception_denies(self):
        """If the callback raises, should deny gracefully."""
        async def callback(tool_name, input_args, decision):
            raise RuntimeError("UI crashed")

        handler = PermissionPromptHandler(callback=callback)
        result = await handler.handle_ask("Bash", {}, _ask_decision())

        assert result.behavior is PermissionBehavior.DENY
        assert "failed" in result.message


# ---------------------------------------------------------------------------
# Tests: callback receives correct arguments
# ---------------------------------------------------------------------------


class TestCallbackArguments:
    @pytest.mark.asyncio
    async def test_callback_receives_tool_name_and_args(self):
        """The callback should receive the tool name, input args, and decision."""
        received = {}

        async def callback(tool_name, input_args, decision):
            received["tool_name"] = tool_name
            received["input_args"] = input_args
            received["decision"] = decision
            return "allow_once"

        decision = _ask_decision("Please approve")
        handler = PermissionPromptHandler(callback=callback)
        await handler.handle_ask("Bash", {"command": "echo hi"}, decision)

        assert received["tool_name"] == "Bash"
        assert received["input_args"] == {"command": "echo hi"}
        assert received["decision"] is decision
