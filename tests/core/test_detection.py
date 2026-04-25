# tests/test_detection.py
"""Tests for the chimera.detection module."""
from __future__ import annotations

from chimera.detection import (
    CompositeDetector,
    DetectionResult,
    ExactRepeatDetector,
    LoopDetector,
    OnDetect,
    PatternCycleDetector,
)


# ---------------------------------------------------------------------------
# DetectionResult
# ---------------------------------------------------------------------------

class TestDetectionResult:
    def test_fields(self):
        r = DetectionResult(
            detected=True,
            strategy="exact_repeat",
            pattern="Same call repeated 3 times",
        )
        assert r.detected is True
        assert r.strategy == "exact_repeat"
        assert r.pattern == "Same call repeated 3 times"
        assert r.confidence == 1.0  # default

    def test_custom_confidence(self):
        r = DetectionResult(
            detected=True,
            strategy="test",
            pattern="p",
            confidence=0.75,
        )
        assert r.confidence == 0.75

    def test_frozen(self):
        r = DetectionResult(detected=False, strategy="s", pattern="p")
        try:
            r.detected = True  # type: ignore[misc]
            assert False, "Expected frozen dataclass to reject mutation"
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# ExactRepeatDetector
# ---------------------------------------------------------------------------

class TestExactRepeatDetector:
    def test_no_loop_initially(self):
        d = ExactRepeatDetector(window=5, threshold=3)
        assert d.check() is None

    def test_no_loop_with_varied_calls(self):
        d = ExactRepeatDetector(window=5, threshold=3)
        d.record("read_file", {"path": "a.py"})
        d.record("read_file", {"path": "b.py"})
        d.record("write_file", {"path": "c.py", "content": "x"})
        assert d.check() is None

    def test_detects_after_threshold_identical_calls(self):
        d = ExactRepeatDetector(window=5, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        assert d.check() is None  # only 2 so far
        d.record("read_file", {"path": "main.py"})
        result = d.check()
        assert result is not None
        assert result.detected is True
        assert result.strategy == "exact_repeat"

    def test_window_evicts_old_entries(self):
        d = ExactRepeatDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("write_file", {"path": "out.py", "content": "x"})
        d.record("read_file", {"path": "main.py"})
        # History is now [read main.py, write out.py, read main.py] -- not identical
        assert d.check() is None

    def test_reset_clears_state(self):
        d = ExactRepeatDetector(window=5, threshold=3)
        for _ in range(3):
            d.record("read_file", {"path": "main.py"})
        assert d.check() is not None
        d.reset()
        assert d.check() is None


# ---------------------------------------------------------------------------
# PatternCycleDetector
# ---------------------------------------------------------------------------

class TestPatternCycleDetector:
    def test_no_loop_initially(self):
        d = PatternCycleDetector(window=10, threshold=2)
        assert d.check() is None

    def test_ab_ab_pattern_detected(self):
        d = PatternCycleDetector(window=10, threshold=2)
        d.record("read_file", {"path": "main.py"})
        d.record("write_file", {"path": "main.py", "content": "x"})
        d.record("read_file", {"path": "main.py"})
        d.record("write_file", {"path": "main.py", "content": "x"})
        result = d.check()
        assert result is not None
        assert result.detected is True
        assert result.strategy == "pattern_cycle"
        assert "period 2" in result.pattern

    def test_abc_abc_pattern_detected(self):
        d = PatternCycleDetector(window=10, threshold=2)
        for _ in range(2):
            d.record("a", {"x": 1})
            d.record("b", {"x": 2})
            d.record("c", {"x": 3})
        result = d.check()
        assert result is not None
        assert "period 3" in result.pattern

    def test_no_pattern_with_varied_calls(self):
        d = PatternCycleDetector(window=10, threshold=2)
        d.record("a", {"x": 1})
        d.record("b", {"x": 2})
        d.record("c", {"x": 3})
        d.record("d", {"x": 4})
        assert d.check() is None

    def test_reset_clears_state(self):
        d = PatternCycleDetector(window=10, threshold=2)
        for _ in range(2):
            d.record("a", {"x": 1})
            d.record("b", {"x": 2})
        assert d.check() is not None
        d.reset()
        assert d.check() is None

    def test_threshold_less_than_two_is_rejected(self):
        """Regression: threshold=1 previously produced vacuous matches.

        With threshold=1 the inner `all(...)` over an empty range returned
        True, so any two-call tail was flagged as a cycle.  A cycle by
        definition requires at least 2 repetitions, so the constructor
        must refuse threshold < 2 instead of silently producing nonsense
        detections.
        """
        import pytest
        with pytest.raises(ValueError, match="threshold >= 2"):
            PatternCycleDetector(window=10, threshold=1)
        with pytest.raises(ValueError, match="threshold >= 2"):
            PatternCycleDetector(window=10, threshold=0)

    def test_long_range_abc_abc_abc_cycle(self):
        """Detector handles A-B-C cycles repeated threshold=3 times."""
        d = PatternCycleDetector(window=20, threshold=3)
        for _ in range(3):
            d.record("a", {"x": 1})
            d.record("b", {"x": 2})
            d.record("c", {"x": 3})
        result = d.check()
        assert result is not None
        assert "period 3" in result.pattern
        assert "3 times" in result.pattern

    def test_no_false_positive_on_legitimate_next_file_loop(self):
        """Regression: repeated same-tool calls with different args (e.g.
        reading a sequence of files) must not be flagged as a cycle.
        """
        d = PatternCycleDetector(window=10, threshold=2)
        for i in range(6):
            d.record("read_file", {"path": f"file_{i}.py"})
        assert d.check() is None


# ---------------------------------------------------------------------------
# CompositeDetector
# ---------------------------------------------------------------------------

class TestCompositeDetector:
    def test_combines_strategies(self):
        exact = ExactRepeatDetector(window=5, threshold=3)
        pattern = PatternCycleDetector(window=10, threshold=2)
        comp = CompositeDetector([exact, pattern])

        # Feed A-B-A-B -- exact won't fire, pattern will
        comp.record("a", {"x": 1})
        comp.record("b", {"x": 2})
        comp.record("a", {"x": 1})
        comp.record("b", {"x": 2})

        result = comp.check()
        assert result is not None
        assert result.strategy == "pattern_cycle"

    def test_exact_fires_before_pattern(self):
        exact = ExactRepeatDetector(window=5, threshold=3)
        pattern = PatternCycleDetector(window=10, threshold=2)
        comp = CompositeDetector([exact, pattern])

        for _ in range(3):
            comp.record("a", {"x": 1})

        result = comp.check()
        assert result is not None
        # exact is first in the list, so it wins
        assert result.strategy == "exact_repeat"

    def test_no_detection_when_clean(self):
        comp = CompositeDetector([
            ExactRepeatDetector(window=5, threshold=3),
            PatternCycleDetector(window=10, threshold=2),
        ])
        comp.record("a", {"x": 1})
        comp.record("b", {"x": 2})
        comp.record("c", {"x": 3})
        assert comp.check() is None

    def test_reset_clears_all(self):
        comp = CompositeDetector([
            ExactRepeatDetector(window=5, threshold=3),
            PatternCycleDetector(window=10, threshold=2),
        ])
        for _ in range(3):
            comp.record("a", {"x": 1})
        assert comp.check() is not None
        comp.reset()
        assert comp.check() is None

    def test_requires_at_least_one_strategy(self):
        try:
            CompositeDetector([])
            assert False, "Expected ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# LoopDetector facade
# ---------------------------------------------------------------------------

class TestLoopDetector:
    def test_default_strategies(self):
        ld = LoopDetector()
        # No calls yet -- should be clean
        assert ld.record_and_check("a", {"x": 1}) is None

    def test_detects_exact_repeat(self):
        ld = LoopDetector(window=5, threshold=3)
        ld.record_and_check("read_file", {"path": "main.py"})
        ld.record_and_check("read_file", {"path": "main.py"})
        result = ld.record_and_check("read_file", {"path": "main.py"})
        assert result is not None
        assert result.detected is True

    def test_detects_pattern_cycle(self):
        ld = LoopDetector(window=10, threshold=2)
        ld.record_and_check("a", {"x": 1})
        ld.record_and_check("b", {"x": 2})
        ld.record_and_check("a", {"x": 1})
        result = ld.record_and_check("b", {"x": 2})
        assert result is not None
        assert result.strategy == "pattern_cycle"

    def test_reset_clears_state(self):
        ld = LoopDetector(window=5, threshold=3)
        for _ in range(3):
            ld.record_and_check("a", {"x": 1})
        ld.reset()
        assert ld.record_and_check("a", {"x": 1}) is None

    def test_custom_strategies(self):
        exact = ExactRepeatDetector(window=5, threshold=2)
        ld = LoopDetector(strategies=[exact])
        ld.record_and_check("a", {"x": 1})
        result = ld.record_and_check("a", {"x": 1})
        assert result is not None
        assert result.strategy == "exact_repeat"

    def test_on_detect_enum_values(self):
        assert OnDetect.ASK.value == "ask"
        assert OnDetect.BREAK.value == "break"
        assert OnDetect.WARN.value == "warn"

    def test_on_detect_break_stored(self):
        ld = LoopDetector(on_detect=OnDetect.BREAK)
        assert ld.on_detect is OnDetect.BREAK


# ---------------------------------------------------------------------------
# Old LoopDetector (backwards compat)
# ---------------------------------------------------------------------------

class TestOldLoopDetectorStillWorks:
    """Ensure chimera.core.loop_detection.LoopDetector is unchanged."""

    def test_old_api(self):
        from chimera.core.loop_detection import LoopDetector as OldLD

        d = OldLD(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is True

    def test_old_reset(self):
        from chimera.core.loop_detection import LoopDetector as OldLD

        d = OldLD(window=3, threshold=3)
        for _ in range(3):
            d.record("read_file", {"path": "main.py"})
        d.reset()
        assert d.is_looping() is False
