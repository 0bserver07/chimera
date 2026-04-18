"""Tests for chimera.permissions.checker — PermissionChecker."""
from __future__ import annotations

from typing import Any

import pytest

from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.decisions import PermissionDecision
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import PermissionBehavior, RuleSource


# ---------------------------------------------------------------------------
# Helpers — lightweight tool stubs
# ---------------------------------------------------------------------------

class _StubTool:
    """Minimal tool object for testing."""

    def __init__(
        self,
        name: str = "Bash",
        *,
        requires_approval: bool = False,
        check_result: PermissionDecision | None = None,
        content: str | None = None,
    ) -> None:
        self.name = name
        self.requires_approval = requires_approval
        self._check_result = check_result
        self._content = content

    # Optional hooks the checker probes via getattr
    def check_permissions(self, args: dict[str, Any], context: PermissionContext) -> PermissionDecision | None:
        return self._check_result

    def get_permission_content(self, args: dict[str, Any]) -> str | None:
        return self._content


class _MinimalTool:
    """Tool without check_permissions / get_permission_content."""

    def __init__(self, name: str = "Read") -> None:
        self.name = name
        self.requires_approval = False


def _ctx(
    mode: PermissionMode = PermissionMode.DEFAULT,
    **kwargs: Any,
) -> PermissionContext:
    return PermissionContext(mode=mode, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPermissionChecker:
    """Test the step-by-step permission algorithm."""

    async def test_deny_rule_blocks(self) -> None:
        """Step 1a: deny rules should deny the tool."""
        checker = PermissionChecker()
        ctx = _ctx(deny_rules={RuleSource.PROJECT: ["Bash"]})
        tool = _StubTool(name="Bash")

        decision = await checker.check(tool, {"command": "ls"}, ctx)
        assert decision.behavior == PermissionBehavior.DENY

    async def test_ask_rule_prompts(self) -> None:
        """Step 1b: ask rules should ask the user."""
        checker = PermissionChecker()
        ctx = _ctx(ask_rules={RuleSource.PROJECT: ["Bash"]})
        tool = _StubTool(name="Bash")

        decision = await checker.check(tool, {"command": "ls"}, ctx)
        assert decision.behavior == PermissionBehavior.ASK

    async def test_tool_check_permissions_deny(self) -> None:
        """Step 1c/1d: tool.check_permissions() returns deny."""
        checker = PermissionChecker()
        ctx = _ctx()
        deny_decision = PermissionDecision.deny(message="tool says no")
        tool = _StubTool(name="Bash", check_result=deny_decision)

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.DENY
        assert decision.message == "tool says no"

    async def test_bypass_mode_allows(self) -> None:
        """Step 2a: BYPASS mode allows everything."""
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.BYPASS)
        tool = _StubTool(name="Bash")

        decision = await checker.check(tool, {"command": "rm -rf /"}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_plan_mode_allows(self) -> None:
        """Step 2a: PLAN mode allows only when is_bypass_available is True."""
        checker = PermissionChecker()
        ctx = _ctx(mode=PermissionMode.PLAN, is_bypass_available=True)
        tool = _StubTool(name="Bash")

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_allow_rule_allows(self) -> None:
        """Step 2b: explicit allow rule."""
        checker = PermissionChecker()
        ctx = _ctx(allow_rules={RuleSource.PROJECT: ["Bash"]})
        tool = _StubTool(name="Bash")

        decision = await checker.check(tool, {"command": "ls"}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_default_is_ask(self) -> None:
        """Step 3: no matching rules -> ask."""
        checker = PermissionChecker()
        ctx = _ctx()
        tool = _StubTool(name="Bash")

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ASK

    async def test_default_ask_includes_suggestions(self) -> None:
        """Step 3: default ask should include rule suggestions."""
        checker = PermissionChecker()
        ctx = _ctx()
        tool = _StubTool(name="Bash", content="ls -la")

        decision = await checker.check(tool, {"command": "ls -la"}, ctx)
        assert decision.behavior == PermissionBehavior.ASK
        assert decision.suggestions is not None
        assert len(decision.suggestions) > 0

    async def test_minimal_tool_works(self) -> None:
        """Tools without check_permissions should not crash."""
        checker = PermissionChecker()
        ctx = _ctx()
        tool = _MinimalTool(name="Read")

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ASK

    async def test_deny_rule_with_content_pattern(self) -> None:
        """Deny rule with content pattern: 'Bash(rm*)' denies rm commands."""
        checker = PermissionChecker()
        ctx = _ctx(deny_rules={RuleSource.PROJECT: ["Bash(rm*)"]})
        tool = _StubTool(name="Bash", content="rm -rf /")

        decision = await checker.check(tool, {"command": "rm -rf /"}, ctx)
        assert decision.behavior == PermissionBehavior.DENY

    async def test_deny_rule_content_no_match(self) -> None:
        """Deny rule 'Bash(rm*)' should NOT deny 'ls'."""
        checker = PermissionChecker()
        ctx = _ctx(deny_rules={RuleSource.PROJECT: ["Bash(rm*)"]})
        tool = _StubTool(name="Bash", content="ls -la")

        decision = await checker.check(tool, {"command": "ls -la"}, ctx)
        # Should fall through to default (ask), not deny
        assert decision.behavior != PermissionBehavior.DENY

    async def test_tool_check_permissions_none_continues(self) -> None:
        """If tool.check_permissions() returns None, continue the algorithm."""
        checker = PermissionChecker()
        ctx = _ctx(allow_rules={RuleSource.PROJECT: ["Bash"]})
        tool = _StubTool(name="Bash", check_result=None)

        decision = await checker.check(tool, {}, ctx)
        assert decision.behavior == PermissionBehavior.ALLOW

    async def test_find_rule_helper(self) -> None:
        """_find_rule should find a matching rule string."""
        checker = PermissionChecker()
        rules = {RuleSource.PROJECT: ["Bash", "Write"]}
        assert checker._find_rule(rules, "Bash") is not None
        assert checker._find_rule(rules, "Read") is None

    async def test_suggest_rules(self) -> None:
        """_suggest_rules should produce usable suggestions."""
        checker = PermissionChecker()
        suggestions = checker._suggest_rules("Bash", "ls -la")
        assert any("Bash" in s for s in suggestions)
