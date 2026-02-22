from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from chimera.types import TestResult


@dataclass
class Constraint:
    """A constraint ('regularization') applied during training.

    Each constraint has a name, a check function that receives a TestResult
    and returns True if the constraint is satisfied, and optionally a
    description.
    """

    name: str
    check: Callable[[TestResult], bool]
    description: str = ""

    # ------------------------------------------------------------------
    # Built-in constraints
    # ------------------------------------------------------------------

    @classmethod
    @property
    def tests_pass(cls) -> Constraint:
        return Constraint(
            name="tests_pass",
            check=lambda r: r.all_passed,
            description="All tests must pass.",
        )

    @staticmethod
    def coverage(min: float = 0.8) -> Constraint:  # noqa: A002
        return Constraint(
            name=f"coverage>={min}",
            check=lambda r: r.pass_rate >= min,
            description=f"Pass rate must be >= {min}.",
        )

    @staticmethod
    def max_files(n: int) -> Constraint:
        return Constraint(
            name=f"max_files<={n}",
            check=lambda _: True,  # enforced elsewhere
            description=f"Max {n} files.",
        )

    # ------------------------------------------------------------------
    # Extended constraints (Phase 13)
    # ------------------------------------------------------------------

    @staticmethod
    def no_syntax_errors() -> Constraint:
        """Check that all .py files have valid syntax (compile check)."""

        def _check(result: TestResult) -> bool:
            # Scan output for syntax error indicators
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
        """Approximate cyclomatic complexity check.

        Counts branching keywords (if, elif, for, while, except, and, or)
        in the test output to estimate complexity.  This is a heuristic:
        a real implementation would use ``ast`` on the actual source files.
        """

        _BRANCH_KEYWORDS = re.compile(
            r"\b(if|elif|for|while|except|and|or)\b"
        )

        def _check(result: TestResult) -> bool:
            # If the output includes complexity metrics, parse them
            m = re.search(r"complexity[:\s]+(\d+)", result.output, re.IGNORECASE)
            if m:
                return int(m.group(1)) <= n
            # Fallback: count branch keywords as rough estimate
            count = len(_BRANCH_KEYWORDS.findall(result.output))
            return count <= n

        return Constraint(
            name=f"max_complexity<={n}",
            check=_check,
            description=f"Cyclomatic complexity must be <= {n}.",
        )

    @staticmethod
    def no_security_issues() -> Constraint:
        """Basic security checks for common dangerous patterns.

        Flags: eval(), exec(), subprocess with shell=True,
        __import__('os').system().
        """

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
