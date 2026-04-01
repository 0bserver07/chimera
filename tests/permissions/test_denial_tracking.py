"""Tests for chimera.permissions.denial_tracking — DenialTrackingState."""
from __future__ import annotations

import pytest

from chimera.permissions.denial_tracking import DenialTrackingState


class TestDenialTrackingState:
    def test_no_denials_initially(self) -> None:
        state = DenialTrackingState()
        assert state.should_auto_deny("Bash") is False

    def test_record_below_threshold(self) -> None:
        state = DenialTrackingState(max_denials=3)
        state.record_denial("Bash")
        state.record_denial("Bash")
        assert state.should_auto_deny("Bash") is False

    def test_auto_deny_after_threshold(self) -> None:
        state = DenialTrackingState(max_denials=3)
        for _ in range(3):
            state.record_denial("Bash")
        assert state.should_auto_deny("Bash") is True

    def test_different_tools_tracked_separately(self) -> None:
        state = DenialTrackingState(max_denials=2)
        state.record_denial("Bash")
        state.record_denial("Bash")
        state.record_denial("Write")
        assert state.should_auto_deny("Bash") is True
        assert state.should_auto_deny("Write") is False

    def test_content_tracking(self) -> None:
        state = DenialTrackingState(max_denials=2)
        state.record_denial("Bash", content="rm -rf /")
        state.record_denial("Bash", content="rm -rf /")
        state.record_denial("Bash", content="ls")
        assert state.should_auto_deny("Bash", content="rm -rf /") is True
        assert state.should_auto_deny("Bash", content="ls") is False

    def test_custom_threshold(self) -> None:
        state = DenialTrackingState(max_denials=1)
        state.record_denial("Bash")
        assert state.should_auto_deny("Bash") is True

    def test_content_none_vs_string(self) -> None:
        """Denials with content=None and content='something' are separate."""
        state = DenialTrackingState(max_denials=2)
        state.record_denial("Bash")
        state.record_denial("Bash")
        state.record_denial("Bash", content="ls")
        assert state.should_auto_deny("Bash") is True
        assert state.should_auto_deny("Bash", content="ls") is False
