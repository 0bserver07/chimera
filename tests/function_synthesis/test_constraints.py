"""Tests for chimera.training.constraint — Synthesis Layer constraints."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from chimera.training.constraint import (
    Constraint,
    ConstraintResult,
    evaluate_all,
    all_satisfied,
)
from chimera.types import TestResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env():
    """Create a mock environment."""
    env = MagicMock()
    return env


# ---------------------------------------------------------------------------
# ConstraintResult basics
# ---------------------------------------------------------------------------


def test_constraint_result_satisfied():
    r = ConstraintResult(name="test", satisfied=True, message="ok")
    assert r.satisfied is True


def test_constraint_result_not_satisfied():
    r = ConstraintResult(name="test", satisfied=False, message="fail")
    assert r.satisfied is False


def test_constraint_result_default_value_is_none():
    r = ConstraintResult(name="test", satisfied=True, message="ok")
    assert r.value is None


def test_constraint_result_with_value():
    r = ConstraintResult(name="test", satisfied=True, message="ok", value=42)
    assert r.value == 42


# ---------------------------------------------------------------------------
# Constraint.tests_pass
# ---------------------------------------------------------------------------


def test_tests_pass_satisfied(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=5, failed=0, errors=0, output="ok"
    )
    c = Constraint.tests_pass()
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 1.0
    assert result.name == "tests_pass"


def test_tests_pass_not_satisfied(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=3, failed=2, errors=0, output="fail"
    )
    c = Constraint.tests_pass()
    result = c.evaluate(mock_env)
    assert result.satisfied is False


def test_tests_pass_with_errors(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=5, failed=0, errors=1, output="error"
    )
    c = Constraint.tests_pass()
    result = c.evaluate(mock_env)
    assert result.satisfied is False


# ---------------------------------------------------------------------------
# Constraint.min_pass_rate
# ---------------------------------------------------------------------------


def test_min_pass_rate_satisfied(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=8, failed=2, errors=0, output="ok"
    )
    c = Constraint.min_pass_rate(0.7)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 0.8


def test_min_pass_rate_not_satisfied(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=3, failed=7, errors=0, output="fail"
    )
    c = Constraint.min_pass_rate(0.5)
    result = c.evaluate(mock_env)
    assert result.satisfied is False


def test_min_pass_rate_exact_boundary(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=5, failed=5, errors=0, output="ok"
    )
    c = Constraint.min_pass_rate(0.5)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 0.5


def test_min_pass_rate_name_includes_rate(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=10, failed=0, errors=0, output="ok"
    )
    c = Constraint.min_pass_rate(0.9)
    result = c.evaluate(mock_env)
    assert "0.9" in result.name


# ---------------------------------------------------------------------------
# Constraint.max_files
# ---------------------------------------------------------------------------


def test_max_files_satisfied(mock_env):
    mock_env.list_files.return_value = ["a.py", "b.py", "c.py"]
    c = Constraint.max_files(5)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 3


def test_max_files_not_satisfied(mock_env):
    mock_env.list_files.return_value = [
        "a.py", "b.py", "c.py", "d.py", "e.py", "f.py"
    ]
    c = Constraint.max_files(5)
    result = c.evaluate(mock_env)
    assert result.satisfied is False
    assert result.value == 6


def test_max_files_exact_boundary(mock_env):
    mock_env.list_files.return_value = ["a.py", "b.py", "c.py"]
    c = Constraint.max_files(3)
    result = c.evaluate(mock_env)
    assert result.satisfied is True


def test_max_files_empty(mock_env):
    mock_env.list_files.return_value = []
    c = Constraint.max_files(5)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 0


# ---------------------------------------------------------------------------
# Constraint.max_total_lines
# ---------------------------------------------------------------------------


def test_max_total_lines_satisfied(mock_env):
    mock_env.list_files.return_value = ["a.py", "b.py"]
    mock_env.read_file.side_effect = (
        lambda f: "line1\nline2\nline3" if f == "a.py" else "line1\nline2"
    )
    c = Constraint.max_total_lines(10)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 5


def test_max_total_lines_not_satisfied(mock_env):
    mock_env.list_files.return_value = ["a.py"]
    mock_env.read_file.return_value = "\n".join(f"line{i}" for i in range(100))
    c = Constraint.max_total_lines(50)
    result = c.evaluate(mock_env)
    assert result.satisfied is False
    assert result.value == 100


def test_max_total_lines_handles_read_error(mock_env):
    """If a file can't be read, it should be skipped (not crash)."""
    mock_env.list_files.return_value = ["a.py", "broken.py"]
    def read_side_effect(f):
        if f == "broken.py":
            raise OSError("cannot read")
        return "line1\nline2"
    mock_env.read_file.side_effect = read_side_effect
    c = Constraint.max_total_lines(10)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.value == 2  # Only a.py counted


def test_max_total_lines_empty_files(mock_env):
    mock_env.list_files.return_value = ["empty.py"]
    mock_env.read_file.return_value = ""
    c = Constraint.max_total_lines(10)
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    # An empty string has 0 lines when split (but "".splitlines() == [])
    assert result.value == 0


# ---------------------------------------------------------------------------
# Constraint.custom
# ---------------------------------------------------------------------------


def test_custom_constraint_satisfied(mock_env):
    c = Constraint.custom("no_print", lambda env: True, "No print statements")
    result = c.evaluate(mock_env)
    assert result.satisfied is True
    assert result.name == "no_print"
    assert result.message == "No print statements"


def test_custom_constraint_not_satisfied(mock_env):
    c = Constraint.custom("no_print", lambda env: False, "Found print statements")
    result = c.evaluate(mock_env)
    assert result.satisfied is False


def test_custom_constraint_default_message(mock_env):
    c = Constraint.custom("check", lambda env: True)
    result = c.evaluate(mock_env)
    assert result.message == "Satisfied"


def test_custom_constraint_default_message_not_satisfied(mock_env):
    c = Constraint.custom("check", lambda env: False)
    result = c.evaluate(mock_env)
    assert result.message == "Not satisfied"


# ---------------------------------------------------------------------------
# Constraint.evaluate delegates to check callable
# ---------------------------------------------------------------------------


def test_evaluate_calls_check_with_env(mock_env):
    called_with = []

    def check(env):
        called_with.append(env)
        return ConstraintResult(name="spy", satisfied=True, message="ok")

    c = Constraint("spy", check)
    c.evaluate(mock_env)
    assert called_with == [mock_env]


# ---------------------------------------------------------------------------
# evaluate_all
# ---------------------------------------------------------------------------


def test_evaluate_all(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=5, failed=0, errors=0, output="ok"
    )
    mock_env.list_files.return_value = ["a.py"]
    constraints = [Constraint.tests_pass(), Constraint.max_files(10)]
    results = evaluate_all(constraints, mock_env)
    assert len(results) == 2
    assert all(r.satisfied for r in results)


def test_evaluate_all_empty():
    results = evaluate_all([], MagicMock())
    assert results == []


def test_evaluate_all_preserves_order(mock_env):
    mock_env.run_tests.return_value = TestResult(
        passed=5, failed=0, errors=0, output="ok"
    )
    mock_env.list_files.return_value = ["a.py"] * 20
    constraints = [Constraint.tests_pass(), Constraint.max_files(5)]
    results = evaluate_all(constraints, mock_env)
    assert results[0].name == "tests_pass"
    assert results[0].satisfied is True
    assert results[1].name == "max_files(5)"
    assert results[1].satisfied is False


# ---------------------------------------------------------------------------
# all_satisfied
# ---------------------------------------------------------------------------


def test_all_satisfied_true():
    results = [
        ConstraintResult(name="a", satisfied=True, message="ok"),
        ConstraintResult(name="b", satisfied=True, message="ok"),
    ]
    assert all_satisfied(results) is True


def test_all_satisfied_false():
    results = [
        ConstraintResult(name="a", satisfied=True, message="ok"),
        ConstraintResult(name="b", satisfied=False, message="fail"),
    ]
    assert all_satisfied(results) is False


def test_all_satisfied_empty():
    """Empty list should be vacuously true."""
    assert all_satisfied([]) is True
