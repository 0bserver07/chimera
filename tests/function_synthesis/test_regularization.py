"""Tests for Feature 3: Regularization.

Covers weighted constraint scores, penalty-based constraint factories,
and the RegularizationCallback combined scoring.
"""

from __future__ import annotations

import textwrap

import pytest
from unittest.mock import MagicMock

from chimera.training.constraint import Constraint, ConstraintResult
from chimera.training.regularization import RegularizationCallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(files: dict[str, str]) -> MagicMock:
    """Create a mock environment with the given files."""
    env = MagicMock()

    def list_files(pattern: str = "**/*.py") -> list[str]:
        return list(files.keys())

    def read_file(path: str) -> str:
        return files[path]

    env.list_files = MagicMock(side_effect=list_files)
    env.read_file = MagicMock(side_effect=read_file)
    return env


# ---------------------------------------------------------------------------
# 1. test_constraint_score_field
# ---------------------------------------------------------------------------


def test_constraint_score_field():
    """ConstraintResult has score field defaulting to 1.0."""
    r = ConstraintResult(name="test", satisfied=True, message="ok")
    assert r.score == 1.0

    r2 = ConstraintResult(name="test", satisfied=True, message="ok", score=0.5)
    assert r2.score == 0.5


# ---------------------------------------------------------------------------
# 2. test_complexity_penalty_high
# ---------------------------------------------------------------------------


def test_complexity_penalty_high():
    """High cyclomatic complexity produces score < 1.0."""
    # Code with many branches — each if/for/while/except counts.
    complex_code = textwrap.dedent("""\
        def f(x):
            if x > 0:
                pass
            if x > 1:
                pass
            if x > 2:
                pass
            if x > 3:
                pass
            if x > 4:
                pass
            for i in range(x):
                pass
            for j in range(x):
                pass
            while x > 10:
                x -= 1
            while x > 20:
                x -= 1
            while x > 30:
                x -= 1
            while x > 40:
                x -= 1
            while x > 50:
                x -= 1
    """)
    env = _make_env({"complex.py": complex_code})
    c = Constraint.complexity_penalty(max_complexity=3)
    result = c.evaluate(env)
    assert result.score < 1.0
    assert result.satisfied is False
    assert result.name == "complexity_penalty"


# ---------------------------------------------------------------------------
# 3. test_line_count_penalty_at_target
# ---------------------------------------------------------------------------


def test_line_count_penalty_at_target():
    """At or below target line count, score is 1.0."""
    # Create a file with exactly 50 lines
    code = "\n".join(f"line{i}" for i in range(50))
    env = _make_env({"small.py": code})
    c = Constraint.line_count_penalty(target=50, hard_max=100)
    result = c.evaluate(env)
    assert result.score == 1.0
    assert result.satisfied is True


# ---------------------------------------------------------------------------
# 4. test_line_count_penalty_at_max
# ---------------------------------------------------------------------------


def test_line_count_penalty_at_max():
    """At hard_max line count, score is 0.0."""
    code = "\n".join(f"line{i}" for i in range(100))
    env = _make_env({"big.py": code})
    c = Constraint.line_count_penalty(target=50, hard_max=100)
    result = c.evaluate(env)
    assert result.score == 0.0
    assert result.satisfied is True  # exactly at hard_max, satisfied = total <= hard_max


# ---------------------------------------------------------------------------
# 5. test_reg_callback_combined_score
# ---------------------------------------------------------------------------


def test_reg_callback_combined_score():
    """Combined score is correctly weighted."""
    critic = MagicMock()
    reg = RegularizationCallback(critic=critic, weight=0.3)

    # pass_rate=0.8, critic_score=0.6
    # expected = 0.8 * 0.7 + 0.6 * 0.3 = 0.56 + 0.18 = 0.74
    result = reg.combined_score(pass_rate=0.8, critic_score=0.6)
    assert abs(result - 0.74) < 1e-9

    # weight=0 → pure pass_rate
    reg0 = RegularizationCallback(critic=critic, weight=0.0)
    assert reg0.combined_score(0.9, 0.1) == pytest.approx(0.9)

    # weight=1 → pure critic
    reg1 = RegularizationCallback(critic=critic, weight=1.0)
    assert reg1.combined_score(0.9, 0.4) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# 6. test_duplication_penalty
# ---------------------------------------------------------------------------


def test_duplication_penalty():
    """Duplicate lines produce a lower score."""
    # 10 lines total, 5 are duplicates of "x = 1" (6 copies → 5 extra)
    code = "\n".join(["x = 1"] * 6 + ["y = 2", "z = 3", "a = 4", "b = 5"])
    env = _make_env({"dup.py": code})
    c = Constraint.duplication_penalty(threshold=0.1)
    result = c.evaluate(env)
    # dup_ratio = 5/10 = 0.5, which is >> 0.1 threshold
    assert result.score < 1.0
    assert result.satisfied is False
    assert result.value == pytest.approx(0.5)
