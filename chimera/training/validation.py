"""Validation splits for detecting overfitting during synthesis.

Splits a test suite into training and validation sets so the agent
synthesizes against training tests only.  Validation tests are held out
and used for evaluation after synthesis completes.

The split is done at the **file** level (not individual test functions)
to avoid import/fixture issues.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.training.spec import Spec

if TYPE_CHECKING:
    from chimera.env.base import Environment


@dataclass
class ValidationResult:
    """Result of evaluating against held-out validation tests."""

    train_pass_rate: float
    val_pass_rate: float
    overfit_gap: float  # train_pass_rate - val_pass_rate
    train_passed: int
    val_passed: int
    train_total: int
    val_total: int


class ValidationSplit:
    """Split a test suite into training and validation sets.

    The agent synthesizes against training tests only.  Validation tests
    are held out and used for evaluation after synthesis completes.

    Args:
        spec: A Spec that has ``tests_dir`` set.
        ratio: Fraction of test files to hold out for validation
            (default ``0.3``).
        seed: Random seed for reproducible splits.  When *None*, the
            split is non-deterministic.

    Raises:
        ValueError: If ``spec.tests_dir`` is not set.
    """

    def __init__(
        self,
        spec: Spec,
        ratio: float = 0.3,
        seed: int | None = None,
    ) -> None:
        if not spec.tests_dir:
            raise ValueError("spec.tests_dir must be set to use ValidationSplit")

        self._spec = spec
        self._ratio = ratio
        self._seed = seed

        self._train_dir = tempfile.mkdtemp(prefix="chimera_train_")
        self._val_dir = tempfile.mkdtemp(prefix="chimera_val_")

        self._train_files: list[str] = []
        self._val_files: list[str] = []

        self._split()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def train_spec(self) -> Spec:
        """Spec with only training test files."""
        return Spec(
            text=self._spec.text,
            files=self._spec.files,
            tests_dir=self._train_dir,
            source_file=self._spec.source_file,
        )

    @property
    def val_spec(self) -> Spec:
        """Spec with only validation test files."""
        return Spec(
            text=self._spec.text,
            files=self._spec.files,
            tests_dir=self._val_dir,
            source_file=self._spec.source_file,
        )

    @property
    def train_files(self) -> list[str]:
        """Filenames assigned to the training set."""
        return list(self._train_files)

    @property
    def val_files(self) -> list[str]:
        """Filenames assigned to the validation set."""
        return list(self._val_files)

    # ------------------------------------------------------------------
    # Splitting
    # ------------------------------------------------------------------

    def _split(self) -> None:
        """Discover test files, shuffle, and copy to temp directories."""
        tests_dir = Path(self._spec.tests_dir)  # type: ignore[arg-type]
        test_files = sorted(
            f.name
            for f in tests_dir.iterdir()
            if f.is_file() and f.name.startswith("test_") and f.name.endswith(".py")
        )

        rng = random.Random(self._seed)
        rng.shuffle(test_files)

        val_count = int(len(test_files) * self._ratio)
        # At least 0 val files; if only 1 file total, it goes to train.
        self._train_files = test_files[val_count:]
        self._val_files = test_files[:val_count]

        # Sort for deterministic ordering within each set.
        self._train_files.sort()
        self._val_files.sort()

        for fname in self._train_files:
            shutil.copy2(tests_dir / fname, os.path.join(self._train_dir, fname))

        for fname in self._val_files:
            shutil.copy2(tests_dir / fname, os.path.join(self._val_dir, fname))

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, env: Environment) -> ValidationResult:
        """Run both train and val tests against current env state.

        Uses ``subprocess`` to invoke ``pytest`` on each temp directory,
        with the environment's working directory as the ``cwd`` (obtained
        from ``env.run_command``).

        Args:
            env: The environment containing the code under test.

        Returns:
            A :class:`ValidationResult` with pass rates and overfit gap.
        """
        # Determine the working directory from the environment.
        workdir = self._get_workdir(env)

        train_passed, train_total = self._run_pytest(self._train_dir, workdir)
        val_passed, val_total = self._run_pytest(self._val_dir, workdir)

        train_rate = train_passed / train_total if train_total > 0 else 0.0
        val_rate = val_passed / val_total if val_total > 0 else 0.0

        return ValidationResult(
            train_pass_rate=train_rate,
            val_pass_rate=val_rate,
            overfit_gap=train_rate - val_rate,
            train_passed=train_passed,
            val_passed=val_passed,
            train_total=train_total,
            val_total=val_total,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_workdir(env: Environment) -> str:
        """Extract the working directory from an environment.

        Tries the ``workdir`` attribute first (LocalEnvironment), then
        falls back to running ``pwd`` in the environment.
        """
        if hasattr(env, "workdir"):
            return str(env.workdir)
        result = env.run_command("pwd")
        return result.stdout.strip()

    @staticmethod
    def _run_pytest(tests_dir: str, workdir: str) -> tuple[int, int]:
        """Run pytest on *tests_dir* and return ``(passed, total)``.

        If the tests directory is empty (no test files), returns
        ``(0, 0)``.
        """
        test_files = [
            f
            for f in os.listdir(tests_dir)
            if f.startswith("test_") and f.endswith(".py")
        ]
        if not test_files:
            return 0, 0

        try:
            result = subprocess.run(
                [
                    "python",
                    "-m",
                    "pytest",
                    tests_dir,
                    "-v",
                    "--tb=no",
                    "-q",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=workdir,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 0, len(test_files)

        return _parse_pytest_summary(result.stdout)


def _parse_pytest_summary(output: str) -> tuple[int, int]:
    """Parse pytest short summary to extract passed and total counts.

    Handles lines like:
        ``3 passed in 0.05s``
        ``2 passed, 1 failed in 0.10s``
        ``3 failed in 0.05s``

    Returns:
        ``(passed, total)`` — where *total* = passed + failed + errors.
    """
    passed = 0
    failed = 0
    errors = 0

    for line in output.splitlines():
        line = line.strip()
        # Look for the summary line (e.g., "3 passed, 1 failed in 0.05s"
        # or "=== 3 passed in 0.05s ===")
        if "passed" in line or "failed" in line or "error" in line:
            parts = line.replace("=", "").strip().split(",")
            for part in parts:
                part = part.strip()
                tokens = part.split()
                for i, token in enumerate(tokens):
                    if token == "passed" and i > 0:
                        try:
                            passed = int(tokens[i - 1])
                        except ValueError:
                            pass
                    elif token == "failed" and i > 0:
                        try:
                            failed = int(tokens[i - 1])
                        except ValueError:
                            pass
                    elif token in ("error", "errors") and i > 0:
                        try:
                            errors = int(tokens[i - 1])
                        except ValueError:
                            pass

    total = passed + failed + errors
    return passed, total
