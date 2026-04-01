"""Tests for chimera.core.plan_mode — PlanModeGuard (#121)."""
from __future__ import annotations

from chimera.core.plan_mode import PlanModeGuard


class TestPlanModeGuard:
    def test_guard_blocks_bash(self):
        guard = PlanModeGuard()
        guard.activate()

        allowed, msg = guard.check("bash")

        assert not allowed
        assert "bash" in msg
        assert "Blocked" in msg

    def test_guard_blocks_edit(self):
        guard = PlanModeGuard()
        guard.activate()

        allowed, msg = guard.check("edit_file")

        assert not allowed
        assert "edit_file" in msg

    def test_guard_allows_read_file(self):
        guard = PlanModeGuard()
        guard.activate()

        allowed, msg = guard.check("read_file")

        assert allowed
        assert msg == ""

    def test_guard_allows_search(self):
        guard = PlanModeGuard()
        guard.activate()

        allowed, msg = guard.check("search")

        assert allowed
        assert msg == ""

    def test_activate_deactivate_cycle(self):
        guard = PlanModeGuard()
        assert not guard.active

        guard.activate()
        assert guard.active

        guard.deactivate()
        assert not guard.active

    def test_blocked_tools_set_is_complete(self):
        """Ensure the guard blocks all known write/execute tools."""
        guard = PlanModeGuard()
        guard.activate()

        expected_blocked = {"bash", "write_file", "edit_file", "replace_in_file", "apply_patch", "git"}
        for tool_name in expected_blocked:
            allowed, _ = guard.check(tool_name)
            assert not allowed, f"{tool_name} should be blocked"
