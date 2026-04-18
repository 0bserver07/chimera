from __future__ import annotations

from chimera.eval.anti_overfit import (
    check_hardcoded_answers,
    check_output_similarity,
)


class TestCheckOutputSimilarity:
    def test_no_overfit_for_varied_outputs(self):
        outputs = [
            "def solve_a(): return 1",
            "def solve_b(): return 2",
            "def solve_c(): return 3",
            "def solve_d(): return 4",
            "def solve_e(): return 5",
        ]
        signal = check_output_similarity(outputs)
        assert signal.detected is False
        assert signal.confidence < 0.9

    def test_overfit_detected_for_identical_outputs(self):
        outputs = ["return 42"] * 10
        signal = check_output_similarity(outputs)
        assert signal.detected is True
        assert signal.confidence >= 0.9
        assert "similarity" in signal.reason.lower()

    def test_single_output_no_detection(self):
        signal = check_output_similarity(["only one"])
        assert signal.detected is False
        assert signal.confidence == 0.0

    def test_empty_outputs(self):
        signal = check_output_similarity([])
        assert signal.detected is False

    def test_custom_threshold(self):
        # 5 outputs, 3 identical -> similarity = 1 - 3/5 = 0.4
        outputs = ["same", "same", "same", "diff1", "diff2"]
        signal = check_output_similarity(outputs, threshold=0.3)
        assert signal.detected is True

        signal = check_output_similarity(outputs, threshold=0.5)
        assert signal.detected is False


class TestCheckHardcodedAnswers:
    def test_hardcoding_detected(self):
        # Agent output contains all known test values
        output = """
def solve(x):
    if x == 1: return 42
    if x == 2: return 99
    if x == 3: return 7
    if x == 4: return 13
    if x == 5: return 21
"""
        test_values = ["42", "99", "7", "13", "21"]
        signal = check_hardcoded_answers(output, test_values)
        assert signal.detected is True
        assert signal.confidence == 1.0
        assert "5/5" in signal.reason

    def test_no_hardcoding_for_general_code(self):
        output = """
def solve(x):
    return x * 2 + 1
"""
        test_values = ["42", "99", "7", "13", "21"]
        signal = check_hardcoded_answers(output, test_values)
        assert signal.detected is False
        assert signal.confidence == 0.0

    def test_empty_test_values(self):
        signal = check_hardcoded_answers("some output", [])
        assert signal.detected is False
        assert signal.confidence == 0.0

    def test_partial_match_below_threshold(self):
        # Only 2 out of 5 values present -> 0.4 ratio, below 0.8 threshold
        output = "return 42 if x == 1 else 99"
        test_values = ["42", "99", "7", "13", "21"]
        signal = check_hardcoded_answers(output, test_values)
        assert signal.detected is False
        assert signal.confidence == 0.4
