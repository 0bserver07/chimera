"""Constraints for the Synthesis Layer.

Constraints are the 'regularization' of synthesis — rules that generated
code must satisfy beyond just passing tests.  They prevent overfitting to
the test suite while missing broader quality goals (file count, code size,
style, security, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from chimera.env.base import Environment
from chimera.types import TestResult


@dataclass
class ConstraintResult:
    """Result of evaluating a constraint."""

    name: str
    satisfied: bool
    message: str
    value: Any = None  # The measured value (e.g., pass_rate=0.8)


class Constraint:
    """A constraint that generated code must satisfy.

    Constraints are the 'regularization' of synthesis — rules beyond
    just passing tests.  They prevent overfitting to tests while missing
    broader quality goals.
    """

    def __init__(
        self,
        name: str,
        check: Callable[[Environment], ConstraintResult],
    ) -> None:
        self.name = name
        self._check = check

    def evaluate(self, env: Environment) -> ConstraintResult:
        """Evaluate this constraint against the environment."""
        return self._check(env)

    # --- Factory methods for common constraints ---

    @classmethod
    def tests_pass(cls) -> Constraint:
        """All tests must pass."""

        def check(env: Environment) -> ConstraintResult:
            result = env.run_tests()
            return ConstraintResult(
                name="tests_pass",
                satisfied=result.all_passed,
                message=f"{result.passed}/{result.total} tests passed",
                value=result.pass_rate,
            )

        return cls("tests_pass", check)

    @classmethod
    def min_pass_rate(cls, rate: float) -> Constraint:
        """At least *rate* fraction of tests must pass."""

        def check(env: Environment) -> ConstraintResult:
            result = env.run_tests()
            return ConstraintResult(
                name=f"min_pass_rate({rate})",
                satisfied=result.pass_rate >= rate,
                message=f"Pass rate {result.pass_rate:.1%} (min: {rate:.1%})",
                value=result.pass_rate,
            )

        return cls(f"min_pass_rate({rate})", check)

    @classmethod
    def max_files(cls, n: int) -> Constraint:
        """Codebase must not exceed *n* files."""

        def check(env: Environment) -> ConstraintResult:
            files = env.list_files("**/*")
            count = len(files)
            return ConstraintResult(
                name=f"max_files({n})",
                satisfied=count <= n,
                message=f"{count} files (max: {n})",
                value=count,
            )

        return cls(f"max_files({n})", check)

    @classmethod
    def max_total_lines(cls, n: int) -> Constraint:
        """Total lines of code must not exceed *n*."""

        def check(env: Environment) -> ConstraintResult:
            files = env.list_files("**/*.py")
            total = 0
            for f in files:
                try:
                    content = env.read_file(f)
                    total += len(content.splitlines())
                except Exception:
                    pass
            return ConstraintResult(
                name=f"max_total_lines({n})",
                satisfied=total <= n,
                message=f"{total} lines (max: {n})",
                value=total,
            )

        return cls(f"max_total_lines({n})", check)

    @classmethod
    def custom(
        cls,
        name: str,
        check_fn: Callable[[Environment], bool],
        message: str = "",
    ) -> Constraint:
        """Create a custom constraint from a simple boolean function."""

        def check(env: Environment) -> ConstraintResult:
            satisfied = check_fn(env)
            return ConstraintResult(
                name=name,
                satisfied=satisfied,
                message=message or ("Satisfied" if satisfied else "Not satisfied"),
            )

        return cls(name, check)


def evaluate_all(
    constraints: list[Constraint],
    env: Environment,
) -> list[ConstraintResult]:
    """Evaluate all constraints and return results."""
    return [c.evaluate(env) for c in constraints]


def all_satisfied(results: list[ConstraintResult]) -> bool:
    """Check if all constraint results are satisfied."""
    return all(r.satisfied for r in results)
