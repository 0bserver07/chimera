"""Tests for chimera.permissions.modes — PermissionMode + ApprovalMode."""
from __future__ import annotations

import argparse

import pytest

from chimera.permissions.base import PermissionAction
from chimera.permissions.modes import (
    AlwaysAskPolicy,
    ApprovalMode,
    AutoEditPolicy,
    PermissionMode,
    parse_mode,
    policy_for_mode,
)
from chimera.permissions.presets import (
    AutoApprove,
    Interactive,
    ReadOnly,
)


class TestPermissionMode:
    """PermissionMode enum must expose exactly six modes."""

    def test_has_default(self) -> None:
        assert PermissionMode.DEFAULT.value == "default"

    def test_has_plan(self) -> None:
        assert PermissionMode.PLAN.value == "plan"

    def test_has_accept_edits(self) -> None:
        assert PermissionMode.ACCEPT_EDITS.value == "accept_edits"

    def test_has_bypass(self) -> None:
        assert PermissionMode.BYPASS.value == "bypass_permissions"

    def test_has_dont_ask(self) -> None:
        assert PermissionMode.DONT_ASK.value == "dont_ask"

    def test_has_auto(self) -> None:
        assert PermissionMode.AUTO.value == "auto"

    def test_member_count(self) -> None:
        assert len(PermissionMode) == 6

    def test_is_enum(self) -> None:
        assert isinstance(PermissionMode.DEFAULT, PermissionMode)


# ---------------------------------------------------------------------------
# G3 (W13): the 5-mode ApprovalMode surface
# ---------------------------------------------------------------------------


class TestApprovalMode:
    """ApprovalMode must expose exactly the five standard modes."""

    def test_member_count(self) -> None:
        assert len(ApprovalMode) == 5

    def test_canonical_spellings(self) -> None:
        # Canonical hyphenated spellings the CLI surfaces in --help.
        assert {m.value for m in ApprovalMode} == {
            "read-only",
            "suggest",
            "auto",
            "yolo",
            "strict",
        }

    def test_is_str_subclass(self) -> None:
        # WHY: argparse ``choices=`` plays nicest with str-backed enums.
        assert isinstance(ApprovalMode.READ_ONLY, str)


class TestParseMode:
    """parse_mode accepts canonical, alias, and legacy spellings."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("read-only", ApprovalMode.READ_ONLY),
            ("read_only", ApprovalMode.READ_ONLY),
            ("readonly", ApprovalMode.READ_ONLY),
            ("suggest", ApprovalMode.SUGGEST),
            ("auto", ApprovalMode.AUTO),
            ("yolo", ApprovalMode.YOLO),
            ("strict", ApprovalMode.STRICT),
            # Legacy ferret --approval values.
            ("full", ApprovalMode.YOLO),
            # Legacy mink --permission-mode values.
            ("default", ApprovalMode.SUGGEST),
            ("acceptEdits", ApprovalMode.AUTO),
            ("accept_edits", ApprovalMode.AUTO),
            ("bypassPermissions", ApprovalMode.YOLO),
            ("plan", ApprovalMode.READ_ONLY),
            # Whitespace + casing tolerated.
            (" YOLO ", ApprovalMode.YOLO),
        ],
    )
    def test_parse_mode_accepts(self, raw: str, expected: ApprovalMode) -> None:
        assert parse_mode(raw) is expected

    def test_parse_mode_passthrough(self) -> None:
        assert parse_mode(ApprovalMode.STRICT) is ApprovalMode.STRICT

    def test_parse_mode_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown permission mode"):
            parse_mode("not-a-mode")


class TestPolicyForMode:
    """policy_for_mode dispatches each mode to a fresh PermissionPolicy."""

    def test_read_only_returns_read_only_policy(self) -> None:
        policy = policy_for_mode(ApprovalMode.READ_ONLY)
        assert isinstance(policy, ReadOnly)

    def test_suggest_returns_interactive(self) -> None:
        policy = policy_for_mode(ApprovalMode.SUGGEST)
        assert isinstance(policy, Interactive)

    def test_auto_returns_auto_edit(self) -> None:
        policy = policy_for_mode(ApprovalMode.AUTO)
        assert isinstance(policy, AutoEditPolicy)

    def test_yolo_returns_auto_approve(self) -> None:
        policy = policy_for_mode(ApprovalMode.YOLO)
        assert isinstance(policy, AutoApprove)

    def test_strict_returns_always_ask(self) -> None:
        policy = policy_for_mode(ApprovalMode.STRICT)
        assert isinstance(policy, AlwaysAskPolicy)

    def test_returns_fresh_instance_each_call(self) -> None:
        a = policy_for_mode(ApprovalMode.SUGGEST)
        b = policy_for_mode(ApprovalMode.SUGGEST)
        assert a is not b

    def test_accepts_string_input(self) -> None:
        # Convenience overload: callers can pass the raw flag value.
        assert isinstance(policy_for_mode("read-only"), ReadOnly)

    def test_rejects_unknown_string(self) -> None:
        with pytest.raises(ValueError):
            policy_for_mode("nonsense")


# ---------------------------------------------------------------------------
# Per-mode behaviour against a sample bash + write attempt
# ---------------------------------------------------------------------------


# WHY: each row is one of the five modes paired with the expected
# PermissionAction for the two canonical attempts the spec calls out:
# a sample bash invocation and a sample file-write invocation.
_MODE_BEHAVIOUR = {
    ApprovalMode.READ_ONLY: (
        # Bash + writes are denied outright.
        PermissionAction.DENY,
        PermissionAction.DENY,
    ),
    ApprovalMode.SUGGEST: (
        # Reads OK (covered separately); bash + writes ask.
        PermissionAction.ASK,
        PermissionAction.ASK,
    ),
    ApprovalMode.AUTO: (
        # Bash still asks; simple writes auto-approve.
        PermissionAction.ASK,
        PermissionAction.ALLOW,
    ),
    ApprovalMode.YOLO: (
        # Everything goes.
        PermissionAction.ALLOW,
        PermissionAction.ALLOW,
    ),
    ApprovalMode.STRICT: (
        # Even reads ask — bash + writes ASK too.
        PermissionAction.ASK,
        PermissionAction.ASK,
    ),
}


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_policy_behaviour_on_bash_and_write(mode: ApprovalMode) -> None:
    """Each mode's policy must give the documented action for bash + write."""
    policy = policy_for_mode(mode)
    bash_expected, write_expected = _MODE_BEHAVIOUR[mode]
    assert policy.evaluate("bash", {"command": "echo hi"}) is bash_expected
    assert (
        policy.evaluate("write_file", {"path": "x.py", "content": "print()"})
        is write_expected
    )


# ---------------------------------------------------------------------------
# 5 modes × 3 CLIs = 15 cases — the per-CLI integration matrix
# ---------------------------------------------------------------------------


def _ferret_namespace(mode: str) -> argparse.Namespace:
    """Build a parsed ferret-shaped namespace for ``--permission-mode``."""
    return argparse.Namespace(
        permission_mode=mode,
        # ferret's legacy ``--approval`` flag still exists; passing
        # ``None`` simulates "user did not set it" so --permission-mode
        # is the load-bearing input.
        approval=None,
    )


def _badger_namespace(mode: str) -> argparse.Namespace:
    """Build a parsed badger-shaped namespace for ``--permission-mode``."""
    return argparse.Namespace(permission_mode=mode)


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_ferret_permission_mode_routing(mode: ApprovalMode) -> None:
    """ferret CLI: ``--permission-mode <X>`` selects the matching policy."""
    from chimera.ferret.cli import _resolve_ferret_permissions

    policy = _resolve_ferret_permissions(_ferret_namespace(mode.value))
    assert policy is not None
    bash_expected, write_expected = _MODE_BEHAVIOUR[mode]
    assert policy.evaluate("bash", {"command": "ls"}) is bash_expected
    assert policy.evaluate("write_file", {"path": "f"}) is write_expected


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_badger_permission_mode_routing(mode: ApprovalMode) -> None:
    """badger CLI: ``--permission-mode <X>`` selects the matching policy."""
    from chimera.badger.cli import _resolve_badger_permissions

    policy = _resolve_badger_permissions(_badger_namespace(mode.value))
    assert policy is not None
    bash_expected, write_expected = _MODE_BEHAVIOUR[mode]
    assert policy.evaluate("bash", {"command": "ls"}) is bash_expected
    assert policy.evaluate("write_file", {"path": "f"}) is write_expected


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_mink_permission_mode_routing(mode: ApprovalMode) -> None:
    """mink CLI: ``--permission-mode <X>`` selects the matching policy."""
    from chimera.mink.cli import _policy_for_mode

    policy = _policy_for_mode(mode.value)
    bash_expected, write_expected = _MODE_BEHAVIOUR[mode]
    assert policy.evaluate("bash", {"command": "ls"}) is bash_expected
    assert policy.evaluate("write_file", {"path": "f"}) is write_expected


# ---------------------------------------------------------------------------
# Backwards-compat: legacy ferret ``--approval`` and legacy mink choices
# ---------------------------------------------------------------------------


class TestFerretApprovalBackCompat:
    """ferret ``--approval`` keeps working when ``--permission-mode`` is unset."""

    def test_approval_read_only_maps_to_read_only_mode(self) -> None:
        from chimera.ferret.cli import _resolve_ferret_permissions

        ns = argparse.Namespace(permission_mode=None, approval="read-only")
        policy = _resolve_ferret_permissions(ns)
        assert isinstance(policy, ReadOnly)

    def test_approval_full_maps_to_yolo(self) -> None:
        from chimera.ferret.cli import _resolve_ferret_permissions

        ns = argparse.Namespace(permission_mode=None, approval="full")
        policy = _resolve_ferret_permissions(ns)
        assert isinstance(policy, AutoApprove)

    def test_permission_mode_wins_over_approval(self) -> None:
        from chimera.ferret.cli import _resolve_ferret_permissions

        # If both are set, --permission-mode is the source of truth.
        ns = argparse.Namespace(permission_mode="yolo", approval="read-only")
        policy = _resolve_ferret_permissions(ns)
        assert isinstance(policy, AutoApprove)


class TestMinkLegacyChoices:
    """mink's pre-G3 spellings still resolve to a working policy."""

    @pytest.mark.parametrize(
        ("legacy", "expected_cls"),
        [
            ("default", Interactive),
            ("plan", ReadOnly),
            ("acceptEdits", AutoEditPolicy),
            ("bypassPermissions", AutoApprove),
        ],
    )
    def test_legacy_choices(self, legacy: str, expected_cls: type) -> None:
        from chimera.mink.cli import _policy_for_mode

        assert isinstance(_policy_for_mode(legacy), expected_cls)
