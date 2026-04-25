# tests/test_loop_detection.py
from chimera.core.loop_detection import LoopDetector


class TestLoopDetector:
    def test_no_loop_initially(self):
        d = LoopDetector()
        assert d.is_looping() is False

    def test_detects_repeated_tool_calls(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is True

    def test_different_args_not_loop(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "a.py"})
        d.record("read_file", {"path": "b.py"})
        d.record("read_file", {"path": "c.py"})
        assert d.is_looping() is False

    def test_window_sliding(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("write_file", {"path": "out.py", "content": "x"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is False

    def test_pattern_detection(self):
        """Detects A-B-A-B-A-B pattern."""
        d = LoopDetector(window=6, threshold=2)
        for _ in range(3):
            d.record("read_file", {"path": "main.py"})
            d.record("write_file", {"path": "main.py", "content": "x"})
        assert d.is_looping() is True

    def test_reset(self):
        d = LoopDetector(window=3, threshold=3)
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        d.record("read_file", {"path": "main.py"})
        assert d.is_looping() is True
        d.reset()
        assert d.is_looping() is False
