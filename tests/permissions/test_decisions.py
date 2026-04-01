"""Tests for chimera.permissions.decisions — DecisionReason, PermissionDecision."""
from __future__ import annotations

import pytest

from chimera.permissions.decisions import DecisionReason, PermissionDecision
from chimera.permissions.rules import PermissionBehavior


class TestDecisionReason:
    def test_rule_classmethod(self) -> None:
        r = DecisionReason.rule("matched allow rule for Bash")
        assert r.type == "rule"
        assert r.detail == "matched allow rule for Bash"

    def test_mode_classmethod(self) -> None:
        r = DecisionReason.mode("bypass")
        assert r.type == "mode"
        assert r.detail == "bypass"


class TestPermissionDecision:
    def test_allow(self) -> None:
        d = PermissionDecision.allow(message="Tool is on allowlist")
        assert d.behavior == PermissionBehavior.ALLOW
        assert d.message == "Tool is on allowlist"
        assert d.reason is None
        assert d.suggestions is None
        assert d.updated_input is None

    def test_deny(self) -> None:
        d = PermissionDecision.deny(message="Blocked by policy")
        assert d.behavior == PermissionBehavior.DENY
        assert d.message == "Blocked by policy"

    def test_ask(self) -> None:
        d = PermissionDecision.ask(
            message="Requires user approval",
            suggestions=["allow Bash", "deny Bash"],
        )
        assert d.behavior == PermissionBehavior.ASK
        assert d.suggestions == ["allow Bash", "deny Bash"]

    def test_allow_with_reason(self) -> None:
        reason = DecisionReason.rule("allow rule from project")
        d = PermissionDecision.allow(
            message="Allowed",
            reason=reason,
        )
        assert d.reason is not None
        assert d.reason.type == "rule"

    def test_allow_with_updated_input(self) -> None:
        d = PermissionDecision.allow(
            message="ok",
            updated_input={"path": "/safe/dir/file.txt"},
        )
        assert d.updated_input == {"path": "/safe/dir/file.txt"}
