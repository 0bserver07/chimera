"""Tests for the missing permission checker steps (IG-3).

Each test targets a specific gap in the checker algorithm:
- Step 1e: requires_user_interaction bypass-immune ASK
- Step 1g: safety_check reason type bypass-immune ASK
- ACCEPT_EDITS mode auto-allow for file edit tools
- DONT_ASK mode converts final ASK to DENY
- PLAN mode only allows if is_bypass_available
"""
from __future__ import annotations

from typing import Any

import pytest

from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import PermissionBehavior, RuleSource


# ---------------------------------------------------------------------------
# Helpers — tool stubs
# ---------------------------------------------------------------------------

class _StubTool:
    """Configurable tool stub for testing."""

    def __init__(
        self,
        name: str = "Bash",
        *,
        is_read_only: bool = False,
        requires_user_interaction: bool = False,
        check_result: PermissionDecision | None = None,
        content: str | None = None,
    ) -> None:
        self.name = name
        self.is_read_only = is_read_only
        self.requires_user_interaction = requires_user_interaction
        self._check_result = check_result
        self._content = content

    def check_permissions(self, args: dict[str, Any], context: Any = None) -> PermissionDecision | None:
        return self._check_result

    def get_permission_content(self, args: dict[str, Any]) -> str | None:
        return self._content


def _ctx(
    mode: PermissionMode = PermissionMode.DEFAULT,
    **kwargs: Any,
) -> PermissionContext:
    return PermissionContext(mode=mode, **kwargs)


# ---------------------------------------------------------------------------
# Step 1e: requires_user_interaction bypass-immune ASK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStep1eRequiresUserInteraction:
    """If tool has requires_user_interaction=True and check_permissions returns ASK,
    the ASK should be returned immediately (bypass-immune)."""

    async def test_requires_interaction_ask_bypasses_mode(self) -> None:
        """Even in BYPASS mode, a tool requiring interaction must still ASK."""
        ask_decision = PermissionDecision.ask(message="Needs user input")
        tool = _StubTool(
            name="AskUser",
            requires_user_interaction=True,
            check_result=ask_decision,
        )
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.BYPASS)

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ASK
        assert decision.message == "Needs user input"

    async def test_non_interaction_tool_not_bypass_immune(self) -> None:
        """A tool without requires_user_interaction continues normally."""
        ask_decision = PermissionDecision.ask(message="Generic ask")
        tool = _StubTool(
            name="Bash",
            requires_user_interaction=False,
            check_result=ask_decision,
        )
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.BYPASS)

        decision = await checker.check(tool, {}, ctx)
        # In BYPASS mode, should be allowed (not ASK) since no interaction required
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_requires_interaction_deny_still_denies(self) -> None:
        """If check_permissions returns DENY (not ASK), deny regardless of flag."""
        deny_decision = PermissionDecision.deny(message="Denied by tool")
        tool = _StubTool(
            name="AskUser",
            requires_user_interaction=True,
            check_result=deny_decision,
        )
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.BYPASS)

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.DENY


# ---------------------------------------------------------------------------
# Step 1g: safety_check reason type bypass-immune ASK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStep1gSafetyCheckBypassImmune:
    """If check_permissions returns ASK with reason type 'safety_check',
    it is bypass-immune and must always be returned."""

    async def test_safety_check_ask_bypasses_mode(self) -> None:
        ask_decision = PermissionDecision.ask(
            message="Safety check required",
            reason=DecisionReason(type="safety_check", detail="destructive op"),
        )
        tool = _StubTool(name="Bash", check_result=ask_decision)
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.BYPASS)

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ASK
        assert decision.reason is not None
        assert decision.reason.type == "safety_check"

    async def test_non_safety_ask_not_bypass_immune(self) -> None:
        """An ASK without safety_check reason goes through normal flow."""
        ask_decision = PermissionDecision.ask(
            message="Regular ask",
            reason=DecisionReason(type="rule", detail="some rule"),
        )
        tool = _StubTool(name="Bash", check_result=ask_decision)
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.BYPASS)

        decision = await checker.check(tool, {}, ctx)
        # BYPASS mode should convert this to ALLOW
        assert decision.behavior == PermissionBehavior.ALLOW


# ---------------------------------------------------------------------------
# ACCEPT_EDITS mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAcceptEditsMode:
    """In ACCEPT_EDITS mode, file edit tools (non-read-only, name contains
    'edit' or 'write') should be auto-allowed in step 2a."""

    async def test_edit_tool_allowed(self) -> None:
        tool = _StubTool(name="edit_file", is_read_only=False)
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.ACCEPT_EDITS)

        decision = await checker.check(tool, {"path": "foo.py"}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_write_tool_allowed(self) -> None:
        tool = _StubTool(name="write_file", is_read_only=False)
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.ACCEPT_EDITS)

        decision = await checker.check(tool, {"path": "foo.py"}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_read_only_tool_not_auto_allowed(self) -> None:
        """A read-only tool named 'edit' should NOT be auto-allowed."""
        tool = _StubTool(name="edit_preview", is_read_only=True)
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.ACCEPT_EDITS)

        decision = await checker.check(tool, {}, ctx)
        # Should fall through to default ASK (no allow rules match)
        assert decision.behavior == PermissionBehavior.ASK

    async def test_non_edit_tool_not_auto_allowed(self) -> None:
        """A non-edit tool (e.g. Bash) should NOT be auto-allowed in ACCEPT_EDITS."""
        tool = _StubTool(name="Bash", is_read_only=False)
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.ACCEPT_EDITS)

        decision = await checker.check(tool, {"command": "ls"}, ctx)
        # Should fall through to default ASK
        assert decision.behavior == PermissionBehavior.ASK


# ---------------------------------------------------------------------------
# DONT_ASK mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDontAskMode:
    """If the final result is ASK and mode is DONT_ASK, convert to DENY."""

    async def test_ask_converted_to_deny(self) -> None:
        tool = _StubTool(name="Bash")
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.DONT_ASK)

        decision = await checker.check(tool, {"command": "ls"}, ctx)
        assert decision.behavior == PermissionBehavior.DENY

    async def test_allow_rule_still_works(self) -> None:
        """Allow rules should still function in DONT_ASK mode."""
        tool = _StubTool(name="Bash")
        checker = PermissionChecker()
        ctx = _ctx(
            mode=PermissionMode.DONT_ASK,
            allow_rules={RuleSource.PROJECT: ["Bash"]},
        )

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_deny_rule_still_denies(self) -> None:
        """Deny rules should still work in DONT_ASK mode."""
        tool = _StubTool(name="Bash")
        checker = PermissionChecker()
        ctx = _ctx(
            mode=PermissionMode.DONT_ASK,
            deny_rules={RuleSource.PROJECT: ["Bash"]},
        )

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.DENY


# ---------------------------------------------------------------------------
# PLAN mode: only allow if is_bypass_available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPlanModeBypassAvailable:
    """PLAN mode should only auto-allow in step 2a if is_bypass_available is True."""

    async def test_plan_with_bypass_available_allows(self) -> None:
        tool = _StubTool(name="Read")
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.PLAN, is_bypass_available=True)

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_plan_without_bypass_available_asks(self) -> None:
        tool = _StubTool(name="Read")
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.PLAN, is_bypass_available=False)

        decision = await checker.check(tool, {}, ctx)
        # Without bypass, PLAN should fall through to default (ASK)
        assert decision.behavior == PermissionBehavior.ASK

    async def test_plan_with_allow_rule_still_allows(self) -> None:
        """Even without bypass, an explicit allow rule should work in PLAN mode."""
        tool = _StubTool(name="Read")
        checker = PermissionChecker()
        ctx = _ctx(
            mode=PermissionMode.PLAN,
            is_bypass_available=False,
            allow_rules={RuleSource.PROJECT: ["Read"]},
        )

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW


# ---------------------------------------------------------------------------
# Tool.check_permissions new attributes on BaseTool
# ---------------------------------------------------------------------------

class TestToolNewAttributes:
    """Test the new attributes added to BaseTool."""

    def test_requires_user_interaction_default(self) -> None:
        from chimera.core.tool import BaseTool
        from chimera.types import ToolResult

        class DummyTool(BaseTool):
            name = "dummy"
            description = "test"
            parameters: dict = {"type": "object"}
            def execute(self, args, env) -> ToolResult:
                return ToolResult(output="ok")

        t = DummyTool()
        assert t.requires_user_interaction is False

    def test_get_permission_content_default(self) -> None:
        from chimera.core.tool import BaseTool
        from chimera.types import ToolResult

        class DummyTool(BaseTool):
            name = "dummy"
            description = "test"
            parameters: dict = {"type": "object"}
            def execute(self, args, env) -> ToolResult:
                return ToolResult(output="ok")

        t = DummyTool()
        assert t.get_permission_content({"key": "value"}) is None

    def test_check_permissions_default(self) -> None:
        from chimera.core.tool import BaseTool
        from chimera.types import ToolResult

        class DummyTool(BaseTool):
            name = "dummy"
            description = "test"
            parameters: dict = {"type": "object"}
            def execute(self, args, env) -> ToolResult:
                return ToolResult(output="ok")

        t = DummyTool()
        assert t.check_permissions({}) is None
