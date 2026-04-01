"""Tests for chimera.permissions.context — PermissionContext."""
from __future__ import annotations

import pytest

from chimera.permissions.context import PermissionContext
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import RuleSource


class TestPermissionContext:
    def test_default_construction(self) -> None:
        ctx = PermissionContext(mode=PermissionMode.DEFAULT)
        assert ctx.mode == PermissionMode.DEFAULT
        assert ctx.allow_rules == {}
        assert ctx.deny_rules == {}
        assert ctx.ask_rules == {}
        assert ctx.additional_working_dirs == frozenset()
        assert ctx.is_bypass_available is False
        assert ctx.should_avoid_prompts is False
        assert ctx.pre_plan_mode is None

    def test_frozen(self) -> None:
        ctx = PermissionContext(mode=PermissionMode.DEFAULT)
        with pytest.raises(AttributeError):
            ctx.mode = PermissionMode.BYPASS  # type: ignore[misc]

    def test_with_rules(self) -> None:
        ctx = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            allow_rules={RuleSource.PROJECT: ["Bash", "Write"]},
            deny_rules={RuleSource.USER: ["rm -rf"]},
            ask_rules={},
            is_bypass_available=True,
        )
        assert ctx.allow_rules[RuleSource.PROJECT] == ["Bash", "Write"]
        assert RuleSource.USER in ctx.deny_rules
        assert ctx.is_bypass_available is True

    def test_additional_working_dirs(self) -> None:
        ctx = PermissionContext(
            mode=PermissionMode.DEFAULT,
            additional_working_dirs=frozenset({"/tmp/extra"}),
        )
        assert "/tmp/extra" in ctx.additional_working_dirs

    def test_pre_plan_mode(self) -> None:
        ctx = PermissionContext(
            mode=PermissionMode.PLAN,
            pre_plan_mode=PermissionMode.DEFAULT,
        )
        assert ctx.pre_plan_mode == PermissionMode.DEFAULT
