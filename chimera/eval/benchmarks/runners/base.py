"""Common types for language-specific MultiSWE-bench runners."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SkipReason(str, Enum):
    """Why a runner declined to execute."""

    NO_ENV = "no_env"
    TOOLCHAIN_MISSING = "toolchain_missing"
    PATCH_FAILED = "patch_failed"
    EXECUTION_ERROR = "execution_error"


@dataclass(frozen=True)
class RunnerResult:
    """Outcome of a single language runner invocation.

    Attributes:
        passed: ``True`` if the language test command exited with code ``0``.
        skipped: ``True`` if the runner declined to execute (toolchain
            missing, no env, etc).
        skip_reason: Populated when ``skipped`` is ``True``.
        stdout: Captured stdout from the test command (best-effort, empty
            when not available).
        stderr: Captured stderr from the test command.
        exit_code: Exit code from the test command, or ``None`` if not run.
    """

    passed: bool
    skipped: bool = False
    skip_reason: SkipReason | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


@dataclass(frozen=True)
class LanguageRunner:
    """Adapter that runs a language's native test command inside an env.

    Runners are intentionally pure data + small methods so they can be
    constructed at import time (one frozen instance per language) and
    shared across benchmark calls.

    Attributes:
        language: Canonical language name (lowercase).
        test_command: Command executed via ``env.run_command`` to run
            the verification suite (e.g. ``"pytest -x"``).
        toolchain_command: Quick probe used by :meth:`is_toolchain_available`
            (e.g. ``"python --version"``). Should exit ``0`` when the
            toolchain is installed.
        display_name: Human-readable name used in reports.
    """

    language: str
    test_command: str
    toolchain_command: str
    display_name: str

    def is_toolchain_available(self, env: Any) -> bool:
        """Return ``True`` if the language toolchain is callable in ``env``.

        Falls back to ``True`` when ``env`` does not implement
        ``run_command`` so that pure unit tests using bare mocks still
        proceed; in that case any subsequent runner call also exercises
        the same path.

        Args:
            env: Execution environment exposing ``run_command``.
        """
        if env is None:
            return False
        run_command = getattr(env, "run_command", None)
        if run_command is None:
            return True
        try:
            result = run_command(self.toolchain_command)
        except Exception:
            return False
        success = getattr(result, "success", None)
        if success is not None:
            return bool(success)
        exit_code = getattr(result, "exit_code", None)
        if exit_code is not None:
            return bool(exit_code == 0)
        # Last-resort: truthy result counts as available.
        return bool(result)

    def apply_test_patch(self, env: Any, test_patch: str) -> bool:
        """Apply ``test_patch`` to the working tree via ``git apply``.

        Args:
            env: Execution environment with ``write_file`` + ``run_command``.
            test_patch: Diff text introducing or modifying tests.

        Returns:
            ``True`` if the patch applied (or there was nothing to apply).
        """
        if not test_patch:
            return True
        write_file = getattr(env, "write_file", None)
        run_command = getattr(env, "run_command", None)
        if write_file is None or run_command is None:
            return False
        try:
            write_file("_test_patch.diff", test_patch)
            result = run_command("git apply _test_patch.diff")
        except Exception:
            return False
        return bool(getattr(result, "success", False))

    def run(self, env: Any, test_patch: str = "") -> RunnerResult:
        """Apply the test patch and run the language test command.

        Args:
            env: Execution environment.
            test_patch: Optional test patch to apply before running tests.

        Returns:
            A :class:`RunnerResult` describing the outcome.
        """
        if env is None:
            return RunnerResult(passed=False, skipped=True, skip_reason=SkipReason.NO_ENV)

        if not self.is_toolchain_available(env):
            return RunnerResult(
                passed=False,
                skipped=True,
                skip_reason=SkipReason.TOOLCHAIN_MISSING,
            )

        if test_patch and not self.apply_test_patch(env, test_patch):
            return RunnerResult(
                passed=False,
                skipped=True,
                skip_reason=SkipReason.PATCH_FAILED,
            )

        run_command = getattr(env, "run_command", None)
        if run_command is None:
            return RunnerResult(
                passed=False,
                skipped=True,
                skip_reason=SkipReason.EXECUTION_ERROR,
            )
        try:
            result = run_command(self.test_command)
        except Exception as exc:  # pragma: no cover - defensive
            return RunnerResult(
                passed=False,
                skipped=True,
                skip_reason=SkipReason.EXECUTION_ERROR,
                stderr=str(exc),
            )

        success = getattr(result, "success", None)
        exit_code = getattr(result, "exit_code", None)
        passed = bool(success) if success is not None else (exit_code == 0)
        return RunnerResult(
            passed=passed,
            skipped=False,
            stdout=str(getattr(result, "stdout", "") or ""),
            stderr=str(getattr(result, "stderr", "") or ""),
            exit_code=exit_code,
        )
