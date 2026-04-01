"""Tests for chimera.permissions.modes — PermissionMode enum."""
from __future__ import annotations

import pytest

from chimera.permissions.modes import PermissionMode


class TestPermissionMode:
    """PermissionMode enum must expose exactly six modes."""

    def test_has_default(self) -> None:
        assert PermissionMode.DEFAULT.value == "default"

    def test_has_plan(self) -> None:
        assert PermissionMode.PLAN.value == "plan"

    def test_has_accept_edits(self) -> None:
        assert PermissionMode.ACCEPT_EDITS.value == "accept_edits"

    def test_has_bypass(self) -> None:
        assert PermissionMode.BYPASS.value == "bypass"

    def test_has_dont_ask(self) -> None:
        assert PermissionMode.DONT_ASK.value == "dont_ask"

    def test_has_auto(self) -> None:
        assert PermissionMode.AUTO.value == "auto"

    def test_member_count(self) -> None:
        assert len(PermissionMode) == 6

    def test_is_enum(self) -> None:
        assert isinstance(PermissionMode.DEFAULT, PermissionMode)
