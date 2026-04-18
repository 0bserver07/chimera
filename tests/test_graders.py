"""Tests for the grader framework."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from chimera.eval.graders.builtin import (
    CompositeGrader,
    FileExistsGrader,
    PatternMatchGrader,
    SchemaGrader,
    TestPassGrader,
)
from chimera.eval.graders.llm import LLMRubricGrader


# --- FileExistsGrader ---


def test_file_exists_pass() -> None:
    """Files exist -> pass."""
    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = Path(tmpdir) / "a.txt"
        f2 = Path(tmpdir) / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")

        grader = FileExistsGrader(paths=[str(f1), str(f2)])
        result = grader.grade({}, {})

        assert result.passed is True
        assert result.score == 1.0
        assert result.grader_name == "file_exists"


def test_file_exists_fail() -> None:
    """Files missing -> fail with score."""
    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = Path(tmpdir) / "exists.txt"
        f1.write_text("hello")
        missing = str(Path(tmpdir) / "missing.txt")

        grader = FileExistsGrader(paths=[str(f1), missing])
        result = grader.grade({}, {})

        assert result.passed is False
        assert result.score == 0.5
        assert "missing.txt" in result.reason


# --- PatternMatchGrader ---


def test_pattern_match_pass() -> None:
    """Regex matches -> pass."""
    grader = PatternMatchGrader(pattern=r"SUCCESS:\s+\d+")
    result = grader.grade({}, {"output": "Result: SUCCESS: 42 items"})

    assert result.passed is True
    assert result.score == 1.0


def test_pattern_match_fail() -> None:
    """No match -> fail."""
    grader = PatternMatchGrader(pattern=r"PASSED")
    result = grader.grade({}, {"output": "FAILED with errors"})

    assert result.passed is False
    assert result.score == 0.0


# --- TestPassGrader ---


def test_test_pass_success() -> None:
    """Command exits 0 -> pass."""
    with patch("chimera.eval.graders.builtin.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        grader = TestPassGrader(command="pytest tests/")
        result = grader.grade({}, {})

        assert result.passed is True
        assert result.score == 1.0
        mock_run.assert_called_once_with(
            "pytest tests/",
            shell=True,
            timeout=60,
            capture_output=True,
            text=True,
        )


def test_test_pass_failure() -> None:
    """Command exits non-0 -> fail."""
    with patch("chimera.eval.graders.builtin.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        grader = TestPassGrader(command="pytest tests/")
        result = grader.grade({}, {})

        assert result.passed is False
        assert result.score == 0.0
        assert "exit 1" in result.reason


def test_test_pass_timeout() -> None:
    """Command times out -> fail."""
    import subprocess

    with patch("chimera.eval.graders.builtin.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 100", timeout=5)
        grader = TestPassGrader(command="sleep 100", timeout=5)
        result = grader.grade({}, {})

        assert result.passed is False
        assert result.score == 0.0
        assert "timed out" in result.reason


# --- SchemaGrader ---


def test_schema_valid() -> None:
    """Valid JSON matches schema -> pass."""
    schema = {"name": "str", "age": "int", "active": "bool"}
    output = json.dumps({"name": "Alice", "age": 30, "active": True})

    grader = SchemaGrader(schema=schema)
    result = grader.grade({}, {"output": output})

    assert result.passed is True
    assert result.score == 1.0


def test_schema_invalid() -> None:
    """Invalid -> fail."""
    schema = {"name": "str", "age": "int", "tags": "list"}
    output = json.dumps({"name": "Alice", "age": "thirty", "tags": "not-a-list"})

    grader = SchemaGrader(schema=schema)
    result = grader.grade({}, {"output": output})

    assert result.passed is False
    assert result.score < 1.0
    assert "age" in result.reason or "tags" in result.reason


# --- CompositeGrader ---


def test_composite_all_pass() -> None:
    """All graders pass -> pass."""
    g1 = PatternMatchGrader(pattern=r"OK")
    g2 = PatternMatchGrader(pattern=r"DONE")
    composite = CompositeGrader(graders=[g1, g2], mode="all")

    result = composite.grade({}, {"output": "OK and DONE"})

    assert result.passed is True
    assert result.score == 1.0


def test_composite_all_one_fails() -> None:
    """One fails -> fail in 'all' mode."""
    g1 = PatternMatchGrader(pattern=r"OK")
    g2 = PatternMatchGrader(pattern=r"MISSING")
    composite = CompositeGrader(graders=[g1, g2], mode="all")

    result = composite.grade({}, {"output": "OK but no match"})

    assert result.passed is False
    assert result.score == 0.5  # mean of 1.0 and 0.0


def test_composite_any_one_passes() -> None:
    """One passes -> pass in 'any' mode."""
    g1 = PatternMatchGrader(pattern=r"OK")
    g2 = PatternMatchGrader(pattern=r"MISSING")
    composite = CompositeGrader(graders=[g1, g2], mode="any")

    result = composite.grade({}, {"output": "OK but no match"})

    assert result.passed is True
    assert result.score == 1.0  # max of 1.0 and 0.0


def test_composite_any_none_pass() -> None:
    """All fail -> fail in 'any' mode."""
    g1 = PatternMatchGrader(pattern=r"MISSING1")
    g2 = PatternMatchGrader(pattern=r"MISSING2")
    composite = CompositeGrader(graders=[g1, g2], mode="any")

    result = composite.grade({}, {"output": "nothing matches"})

    assert result.passed is False
    assert result.score == 0.0


# --- LLMRubricGrader ---


def test_llm_rubric_above_threshold() -> None:
    """LLM returns 0.8 -> pass."""
    mock_provider = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "score": 0.8,
        "reasoning": "Output is well-structured and correct.",
    })
    mock_provider.complete.return_value = mock_response

    grader = LLMRubricGrader(
        provider=mock_provider,
        rubric="Output should be correct and well-structured.",
    )
    result = grader.grade(
        {"prompt": "Write a function"},
        {"output": "def foo(): return 42"},
    )

    assert result.passed is True
    assert result.score == 0.8
    assert "well-structured" in result.reason
    mock_provider.complete.assert_called_once()


def test_llm_rubric_below_threshold() -> None:
    """LLM returns 0.4 -> fail."""
    mock_provider = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "score": 0.4,
        "reasoning": "Output is incomplete and has errors.",
    })
    mock_provider.complete.return_value = mock_response

    grader = LLMRubricGrader(
        provider=mock_provider,
        rubric="Output should be correct and complete.",
    )
    result = grader.grade(
        {"prompt": "Write a function"},
        {"output": "def foo():"},
    )

    assert result.passed is False
    assert result.score == 0.4
    assert "incomplete" in result.reason
