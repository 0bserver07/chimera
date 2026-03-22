"""Tests for chimera.hooks.loop_detector — loop detection hook."""
from __future__ import annotations

from chimera.hooks.loop_detector import LoopDetectorHook, LoopEvent


class TestExactRepeatDetection:
    """Test that identical consecutive commands are detected."""

    def test_three_identical_commands_triggers_nudge(self) -> None:
        hook = LoopDetectorHook(repeat_threshold=3)

        # First two calls should not trigger
        assert hook.record("bash", {"command": "ls"}) is None
        assert hook.record("bash", {"command": "ls"}) is None

        # Third identical call should trigger
        nudge = hook.record("bash", {"command": "ls"})
        assert nudge is not None
        assert "different approach" in nudge.lower() or "repeating" in nudge.lower()
        assert hook.loop_count == 1

    def test_different_commands_no_detection(self) -> None:
        hook = LoopDetectorHook(repeat_threshold=3)

        assert hook.record("bash", {"command": "ls"}) is None
        assert hook.record("read", {"path": "foo.py"}) is None
        assert hook.record("write", {"path": "bar.py"}) is None
        assert hook.loop_count == 0

    def test_reset_clears_state(self) -> None:
        hook = LoopDetectorHook(repeat_threshold=3)

        hook.record("bash", {"command": "ls"})
        hook.record("bash", {"command": "ls"})
        hook.reset()

        # After reset, the third call should not trigger
        # because history was cleared
        assert hook.record("bash", {"command": "ls"}) is None
        assert hook.loop_count == 0


class TestCycleDetection:
    """Test that A-B-A-B circular patterns are detected."""

    def test_ab_ab_cycle_detected(self) -> None:
        hook = LoopDetectorHook(
            repeat_threshold=5,  # high to avoid exact-repeat
            cycle_threshold=2,
        )

        # A-B-A-B pattern
        assert hook.record("bash", {"command": "cat foo"}) is None
        assert hook.record("bash", {"command": "cat bar"}) is None
        assert hook.record("bash", {"command": "cat foo"}) is None
        nudge = hook.record("bash", {"command": "cat bar"})
        assert nudge is not None
        assert "circular" in nudge.lower() or "pattern" in nudge.lower()


class TestCallbackIntegration:
    """Test the on_detect callback and events list."""

    def test_on_detect_callback_fires(self) -> None:
        events: list[LoopEvent] = []
        hook = LoopDetectorHook(
            repeat_threshold=3,
            on_detect=events.append,
        )

        hook.record("bash", {"command": "ls"})
        hook.record("bash", {"command": "ls"})
        hook.record("bash", {"command": "ls"})

        assert len(events) == 1
        assert events[0].strategy == "exact_repeat"
        assert events[0].nudge == hook.DEFAULT_NUDGE_EXACT
        # The hook's events property should match
        assert hook.events == events
