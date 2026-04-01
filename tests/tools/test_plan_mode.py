"""Tests for chimera.tools.plan_mode — plan mode enter/exit tools (#121)."""
from __future__ import annotations

from chimera.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool


class TestEnterPlanMode:
    def test_enter_plan_mode_activates(self):
        tool = EnterPlanModeTool()
        assert not tool.is_plan_mode_active

        result = tool.execute({}, env=None)

        assert result.success
        assert tool.is_plan_mode_active
        assert "Plan mode activated" in result.output

    def test_enter_plan_mode_is_read_only_and_concurrent(self):
        tool = EnterPlanModeTool()
        assert tool.is_read_only is True
        assert tool.is_concurrency_safe is True


class TestExitPlanMode:
    def test_exit_plan_mode_deactivates(self):
        enter_tool = EnterPlanModeTool()
        exit_tool = ExitPlanModeTool(enter_tool=enter_tool)

        enter_tool.execute({}, env=None)
        assert enter_tool.is_plan_mode_active

        result = exit_tool.execute({}, env=None)

        assert result.success
        assert not enter_tool.is_plan_mode_active
        assert "Plan mode deactivated" in result.output

    def test_exit_plan_mode_is_read_only_and_concurrent(self):
        enter_tool = EnterPlanModeTool()
        exit_tool = ExitPlanModeTool(enter_tool=enter_tool)
        assert exit_tool.is_read_only is True
        assert exit_tool.is_concurrency_safe is True


class TestPlanModeGuardViaTool:
    """Integration: tools + guard work together."""

    def test_plan_mode_guard_blocks_write_tools(self):
        from chimera.core.plan_mode import PlanModeGuard

        guard = PlanModeGuard()
        guard.activate()

        for tool_name in ("bash", "write_file", "edit_file", "apply_patch", "git"):
            allowed, msg = guard.check(tool_name)
            assert not allowed, f"{tool_name} should be blocked"
            assert "Blocked" in msg

    def test_plan_mode_guard_allows_read_tools(self):
        from chimera.core.plan_mode import PlanModeGuard

        guard = PlanModeGuard()
        guard.activate()

        for tool_name in ("read_file", "search", "list_files", "think", "enter_plan_mode"):
            allowed, msg = guard.check(tool_name)
            assert allowed, f"{tool_name} should be allowed"
            assert msg == ""

    def test_plan_mode_guard_inactive_allows_all(self):
        from chimera.core.plan_mode import PlanModeGuard

        guard = PlanModeGuard()
        # guard is inactive by default

        for tool_name in ("bash", "write_file", "edit_file", "apply_patch", "git"):
            allowed, msg = guard.check(tool_name)
            assert allowed, f"{tool_name} should be allowed when guard inactive"
            assert msg == ""
