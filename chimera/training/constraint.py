from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from chimera.types import TestResult


@dataclass
class ConstraintResult:
    """Result of evaluating a constraint."""

    name: str
    satisfied: bool
    message: str
    value: Any = None


@dataclass
class Constraint:
    """A constraint ('regularization') applied during training.

    Supports two modes of operation:

    1. **TestResult mode** (extension API): A ``check`` callable that receives
       a ``TestResult`` and returns True/False.  Used by extended constraints
       like ``no_syntax_errors()``.

    2. **Environment mode** (Phase 6-8 API): An ``evaluate(env)`` method that
       receives the environment, runs tests/inspects files, and returns a
       ``ConstraintResult``.  Factory class methods (``tests_pass()``,
       ``min_pass_rate()``, etc.) produce constraints in this mode.
    """

    name: str
    check: Callable[..., bool | ConstraintResult] | None = None
    description: str = ""

    def evaluate(self, env: Any) -> ConstraintResult:
        """Evaluate this constraint against an environment.

        If ``check`` returns a ``ConstraintResult`` directly, use it.
        Otherwise, wrap the boolean in a ``ConstraintResult``.
        """
        if self.check is None:
            return ConstraintResult(
                name=self.name, satisfied=True, message="No check defined"
            )
        result = self.check(env)
        if isinstance(result, ConstraintResult):
            return result
        return ConstraintResult(
            name=self.name,
            satisfied=bool(result),
            message="Satisfied" if result else "Not satisfied",
        )

    # ------------------------------------------------------------------
    # Phase 6-8 factory methods (env-based evaluation)
    # ------------------------------------------------------------------

    @classmethod
    def tests_pass(cls) -> Constraint:
        """All tests must pass."""

        def _check(env: Any) -> ConstraintResult:
            tr = env.run_tests()
            rate = tr.pass_rate if hasattr(tr, "pass_rate") else (
                tr.passed / (tr.passed + tr.failed + tr.errors)
                if (tr.passed + tr.failed + tr.errors) > 0
                else 0.0
            )
            satisfied = tr.failed == 0 and tr.errors == 0
            return ConstraintResult(
                name="tests_pass",
                satisfied=satisfied,
                message="All tests pass" if satisfied else "Some tests failed",
                value=rate,
            )

        return cls(name="tests_pass", check=_check, description="All tests must pass.")

    @classmethod
    def min_pass_rate(cls, rate: float) -> Constraint:
        """Pass rate must be >= rate."""

        def _check(env: Any) -> ConstraintResult:
            tr = env.run_tests()
            total = tr.passed + tr.failed + tr.errors
            actual_rate = tr.passed / total if total > 0 else 0.0
            satisfied = actual_rate >= rate
            return ConstraintResult(
                name=f"min_pass_rate({rate})",
                satisfied=satisfied,
                message=f"Pass rate {actual_rate:.1%} >= {rate:.1%}"
                if satisfied
                else f"Pass rate {actual_rate:.1%} < {rate:.1%}",
                value=actual_rate,
            )

        return cls(
            name=f"min_pass_rate({rate})",
            check=_check,
            description=f"Pass rate must be >= {rate}.",
        )

    @staticmethod
    def max_files(n: int) -> Constraint:
        """Maximum number of generated files."""

        def _check(env: Any) -> ConstraintResult:
            files = env.list_files()
            count = len(files)
            satisfied = count <= n
            return ConstraintResult(
                name=f"max_files({n})",
                satisfied=satisfied,
                message=f"{count} files (<= {n})" if satisfied else f"{count} files (> {n})",
                value=count,
            )

        return Constraint(
            name=f"max_files({n})",
            check=_check,
            description=f"Max {n} files.",
        )

    @staticmethod
    def max_total_lines(n: int) -> Constraint:
        """Maximum total lines of generated code."""

        def _check(env: Any) -> ConstraintResult:
            files = env.list_files()
            total_lines = 0
            for f in files:
                try:
                    content = env.read_file(f)
                    total_lines += len(content.splitlines()) if content else 0
                except (OSError, IOError):
                    pass  # Skip unreadable files
            satisfied = total_lines <= n
            return ConstraintResult(
                name=f"max_total_lines({n})",
                satisfied=satisfied,
                message=f"{total_lines} lines (<= {n})"
                if satisfied
                else f"{total_lines} lines (> {n})",
                value=total_lines,
            )

        return Constraint(
            name=f"max_total_lines({n})",
            check=_check,
            description=f"Max {n} total lines.",
        )

    @staticmethod
    def custom(
        name: str,
        fn: Callable[..., bool],
        message: str | None = None,
    ) -> Constraint:
        """Create a custom constraint from a callable."""

        def _check(env: Any) -> ConstraintResult:
            result = fn(env)
            if result:
                msg = message if message else "Satisfied"
            else:
                msg = message if message else "Not satisfied"
            return ConstraintResult(name=name, satisfied=result, message=msg)

        return Constraint(name=name, check=_check, description=message or "")

    # ------------------------------------------------------------------
    # Extension constraints (Phase 13) -- TestResult-based
    # ------------------------------------------------------------------

    @staticmethod
    def coverage(min: float = 0.8) -> Constraint:  # noqa: A002
        return Constraint(
            name=f"coverage>={min}",
            check=lambda r: r.pass_rate >= min,
            description=f"Pass rate must be >= {min}.",
        )

    @staticmethod
    def no_syntax_errors() -> Constraint:
        """Check that all .py files have valid syntax (compile check)."""

        def _check(result: TestResult) -> bool:
            output = result.output.lower()
            if "syntaxerror" in output:
                return False
            if "syntax error" in output:
                return False
            return True

        return Constraint(
            name="no_syntax_errors",
            check=_check,
            description="No Python syntax errors in generated code.",
        )

    @staticmethod
    def max_complexity(n: int) -> Constraint:
        """Approximate cyclomatic complexity check."""

        _BRANCH_KEYWORDS = re.compile(
            r"\b(if|elif|for|while|except|and|or)\b"
        )

        def _check(result: TestResult) -> bool:
            m = re.search(r"complexity[:\s]+(\d+)", result.output, re.IGNORECASE)
            if m:
                return int(m.group(1)) <= n
            count = len(_BRANCH_KEYWORDS.findall(result.output))
            return count <= n

        return Constraint(
            name=f"max_complexity<={n}",
            check=_check,
            description=f"Cyclomatic complexity must be <= {n}.",
        )

    @staticmethod
    def no_security_issues() -> Constraint:
        """Basic security checks for common dangerous patterns."""

        _DANGEROUS = [
            re.compile(r"\beval\s*\("),
            re.compile(r"\bexec\s*\("),
            re.compile(r"subprocess.*shell\s*=\s*True"),
            re.compile(r"__import__\s*\(\s*['\"]os['\"]\s*\)"),
        ]

        def _check(result: TestResult) -> bool:
            for pattern in _DANGEROUS:
                if pattern.search(result.output):
                    return False
            return True

        return Constraint(
            name="no_security_issues",
            check=_check,
            description="No eval(), exec(), or subprocess shell=True.",
        )


def evaluate_all(
    constraints: list[Constraint], env: Any
) -> list[ConstraintResult]:
    """Evaluate all constraints against the environment."""
    return [c.evaluate(env) for c in constraints]


def all_satisfied(results: list[ConstraintResult]) -> bool:
    """Return True if all constraint results are satisfied."""
    return all(r.satisfied for r in results)
