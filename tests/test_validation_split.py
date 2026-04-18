"""Tests for chimera.training.validation — ValidationSplit and ValidationResult."""

from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import MagicMock

import pytest

from chimera.training.spec import Spec
from chimera.training.validation import ValidationResult, ValidationSplit


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_tests_dir(num_files: int) -> str:
    """Create a temporary directory with *num_files* trivial test files.

    Each file is named ``test_<i>.py`` and contains a single passing test.
    Returns the path to the temporary directory.
    """
    d = tempfile.mkdtemp(prefix="chimera_vtest_")
    for i in range(num_files):
        path = os.path.join(d, f"test_{i}.py")
        with open(path, "w") as f:
            f.write(f"def test_add_{i}():\n    assert 1 + 1 == 2\n")
    return d


def _make_workdir() -> str:
    """Create a minimal workdir for the Environment mock."""
    d = tempfile.mkdtemp(prefix="chimera_vwork_")
    return d


def _make_env(workdir: str) -> MagicMock:
    """Return a mock Environment with a ``workdir`` attribute."""
    env = MagicMock()
    env.workdir = workdir
    return env


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestSplitRatios:
    """test_split_ratios — 10 test files, ratio=0.3 -> 7 train, 3 val."""

    def test_split_ratios(self, tmp_path: pytest.TempPathFactory) -> None:
        tests_dir = _make_tests_dir(10)
        try:
            spec = Spec.from_tests(tests_dir)
            split = ValidationSplit(spec, ratio=0.3, seed=42)

            assert len(split.train_files) == 7
            assert len(split.val_files) == 3
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)


class TestSplitDeterministic:
    """test_split_deterministic — same seed = same split."""

    def test_split_deterministic(self) -> None:
        tests_dir = _make_tests_dir(10)
        try:
            spec = Spec.from_tests(tests_dir)
            split_a = ValidationSplit(spec, ratio=0.3, seed=123)
            split_b = ValidationSplit(spec, ratio=0.3, seed=123)

            assert split_a.train_files == split_b.train_files
            assert split_a.val_files == split_b.val_files
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)


class TestTrainSpecExcludesVal:
    """test_train_spec_excludes_val — no file overlap between train and val."""

    def test_train_spec_excludes_val(self) -> None:
        tests_dir = _make_tests_dir(10)
        try:
            spec = Spec.from_tests(tests_dir)
            split = ValidationSplit(spec, ratio=0.3, seed=42)

            train_set = set(split.train_files)
            val_set = set(split.val_files)

            # No overlap
            assert train_set & val_set == set()

            # All original files accounted for
            original = {f"test_{i}.py" for i in range(10)}
            assert train_set | val_set == original

            # Spec dirs are different
            assert split.train_spec.tests_dir != split.val_spec.tests_dir

            # Files in train_spec dir match train_files
            train_dir_files = set(os.listdir(split.train_spec.tests_dir))
            val_dir_files = set(os.listdir(split.val_spec.tests_dir))
            assert train_dir_files == train_set
            assert val_dir_files == val_set
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)


class TestEvaluateReturnsBothRates:
    """test_evaluate_returns_both_rates — both rates computed."""

    def test_evaluate_returns_both_rates(self) -> None:
        tests_dir = _make_tests_dir(10)
        workdir = _make_workdir()
        try:
            spec = Spec.from_tests(tests_dir)
            split = ValidationSplit(spec, ratio=0.3, seed=42)
            env = _make_env(workdir)

            result = split.evaluate(env)

            assert isinstance(result, ValidationResult)
            # All tests are trivially passing (assert 1+1==2), so
            # both rates should be 1.0 (or close to it).
            assert result.train_pass_rate >= 0.0
            assert result.val_pass_rate >= 0.0
            assert result.train_total == result.train_passed + (
                result.train_total - result.train_passed
            )
            assert result.val_total == result.val_passed + (
                result.val_total - result.val_passed
            )
            # Since all tests pass:
            assert result.train_passed == result.train_total
            assert result.val_passed == result.val_total
            assert result.train_pass_rate == 1.0
            assert result.val_pass_rate == 1.0
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)
            shutil.rmtree(workdir, ignore_errors=True)


class TestOverfitGapCalculation:
    """test_overfit_gap_calculation — gap = train - val."""

    def test_overfit_gap_calculation(self) -> None:
        # Manually construct a ValidationResult to verify the gap formula.
        result = ValidationResult(
            train_pass_rate=0.9,
            val_pass_rate=0.6,
            overfit_gap=0.9 - 0.6,
            train_passed=9,
            val_passed=6,
            train_total=10,
            val_total=10,
        )
        assert result.overfit_gap == pytest.approx(0.3)
        assert result.overfit_gap == pytest.approx(
            result.train_pass_rate - result.val_pass_rate
        )

    def test_overfit_gap_from_evaluate(self) -> None:
        """Verify evaluate() computes gap = train - val."""
        tests_dir = _make_tests_dir(6)
        workdir = _make_workdir()
        try:
            spec = Spec.from_tests(tests_dir)
            split = ValidationSplit(spec, ratio=0.3, seed=7)
            env = _make_env(workdir)

            result = split.evaluate(env)
            assert result.overfit_gap == pytest.approx(
                result.train_pass_rate - result.val_pass_rate
            )
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)
            shutil.rmtree(workdir, ignore_errors=True)


class TestSplitSingleFile:
    """test_split_single_file — 1 file -> goes to train, val is empty."""

    def test_split_single_file(self) -> None:
        tests_dir = _make_tests_dir(1)
        try:
            spec = Spec.from_tests(tests_dir)
            split = ValidationSplit(spec, ratio=0.3, seed=42)

            assert len(split.train_files) == 1
            assert len(split.val_files) == 0
            assert split.train_files[0] == "test_0.py"

            # val_spec tests_dir should be empty
            val_files = os.listdir(split.val_spec.tests_dir)
            assert len(val_files) == 0
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)

    def test_evaluate_with_empty_val(self) -> None:
        """Evaluate with empty val set returns 0 for val metrics."""
        tests_dir = _make_tests_dir(1)
        workdir = _make_workdir()
        try:
            spec = Spec.from_tests(tests_dir)
            split = ValidationSplit(spec, ratio=0.3, seed=42)
            env = _make_env(workdir)

            result = split.evaluate(env)
            assert result.val_total == 0
            assert result.val_passed == 0
            assert result.val_pass_rate == 0.0
        finally:
            shutil.rmtree(tests_dir, ignore_errors=True)
            shutil.rmtree(workdir, ignore_errors=True)
