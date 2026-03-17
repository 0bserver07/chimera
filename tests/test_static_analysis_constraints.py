"""Tests for type_check, lint_check, and security_scan constraints."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from chimera.env.local import LocalEnvironment
from chimera.training.constraint import Constraint


@pytest.fixture
def tmp_env(tmp_path: Path) -> LocalEnvironment:
    """Create a LocalEnvironment in a temporary directory."""
    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    return env


# ------------------------------------------------------------------
# type_check
# ------------------------------------------------------------------


def test_type_check_clean_code(tmp_env: LocalEnvironment) -> None:
    """No errors on valid typed code."""
    tmp_env.write_file(
        "hello.py",
        "def greet(name: str) -> str:\n    return 'Hello ' + name\n",
    )
    constraint = Constraint.type_check()
    result = constraint.evaluate(tmp_env)
    # If mypy is available and the code is clean, should be satisfied.
    # If mypy is not available, graceful fallback also satisfies.
    assert result.satisfied is True
    assert result.score == 1.0


def test_type_check_tool_not_found(tmp_env: LocalEnvironment) -> None:
    """Graceful fallback when the type checker is not installed."""
    tmp_env.write_file("hello.py", "x: int = 1\n")
    constraint = Constraint.type_check(tool="nonexistent_type_checker_xyz")
    result = constraint.evaluate(tmp_env)
    assert result.satisfied is True
    assert result.score == 1.0
    assert "not available" in result.message


# ------------------------------------------------------------------
# lint_check
# ------------------------------------------------------------------


def test_lint_check_clean(tmp_env: LocalEnvironment) -> None:
    """No warnings on clean code."""
    tmp_env.write_file("clean.py", "x = 1\n")
    constraint = Constraint.lint_check()
    result = constraint.evaluate(tmp_env)
    # If ruff is available and code is clean, satisfied.
    # If ruff not available, graceful fallback.
    assert result.satisfied is True
    assert result.score == 1.0


def test_lint_check_warnings(tmp_env: LocalEnvironment) -> None:
    """Warnings reduce score."""
    # Write code with unused imports which most linters flag.
    tmp_env.write_file("messy.py", "import os\nimport sys\nx = 1\n")
    constraint = Constraint.lint_check()
    result = constraint.evaluate(tmp_env)
    # If ruff is available, it should find unused import warnings.
    # If ruff not available, graceful fallback (satisfied=True, score=1.0).
    if "not available" in result.message:
        assert result.satisfied is True
    else:
        # ruff found warnings -> score should be less than 1.0
        assert result.score < 1.0


def test_lint_check_tool_not_found(tmp_env: LocalEnvironment) -> None:
    """Graceful fallback when the linter is not installed."""
    tmp_env.write_file("hello.py", "x = 1\n")
    constraint = Constraint.lint_check(tool="nonexistent_linter_xyz")
    result = constraint.evaluate(tmp_env)
    assert result.satisfied is True
    assert result.score == 1.0
    assert "not available" in result.message


# ------------------------------------------------------------------
# security_scan
# ------------------------------------------------------------------


def test_security_scan_clean(tmp_env: LocalEnvironment) -> None:
    """No issues on safe code."""
    tmp_env.write_file("safe.py", "def add(a: int, b: int) -> int:\n    return a + b\n")
    constraint = Constraint.security_scan()
    result = constraint.evaluate(tmp_env)
    assert result.satisfied is True
    assert result.score == 1.0
    assert result.message == "Clean"
    assert result.value == {"issues": []}


def test_security_scan_eval(tmp_env: LocalEnvironment) -> None:
    """Detects eval() usage."""
    tmp_env.write_file("danger.py", "x = eval('1 + 2')\n")
    constraint = Constraint.security_scan()
    result = constraint.evaluate(tmp_env)
    assert result.satisfied is False
    assert result.score < 1.0
    assert len(result.value["issues"]) == 1
    assert "eval() call" in result.value["issues"][0]


def test_security_scan_os_system(tmp_env: LocalEnvironment) -> None:
    """Detects os.system() usage."""
    tmp_env.write_file("danger.py", "import os\nos.system('ls')\n")
    constraint = Constraint.security_scan()
    result = constraint.evaluate(tmp_env)
    assert result.satisfied is False
    assert result.score < 1.0
    assert len(result.value["issues"]) == 1
    assert "os.system() call" in result.value["issues"][0]
