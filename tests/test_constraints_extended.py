"""Tests for extended constraint factory methods (Phase 13)."""
from __future__ import annotations

import pytest

from chimera.training.constraint import Constraint
from chimera.types import TestResult


def _result(output: str = "", passed: int = 1, failed: int = 0) -> TestResult:
    return TestResult(passed=passed, failed=failed, errors=0, output=output)


class TestNoSyntaxErrors:
    def test_passes_clean_output(self):
        c = Constraint.no_syntax_errors()
        assert c.check(_result("5 passed"))

    def test_fails_on_syntax_error(self):
        c = Constraint.no_syntax_errors()
        assert not c.check(_result("SyntaxError: invalid syntax"))

    def test_fails_on_case_variation(self):
        c = Constraint.no_syntax_errors()
        assert not c.check(_result("Found a syntax error in module"))


class TestMaxComplexity:
    def test_under_limit(self):
        c = Constraint.max_complexity(10)
        # Output with few branching keywords
        assert c.check(_result("3 passed"))

    def test_over_limit(self):
        c = Constraint.max_complexity(2)
        # Output with many branching keywords
        output = "if x and y or z for i while True except ValueError"
        assert not c.check(_result(output))

    def test_parses_explicit_complexity_metric(self):
        c = Constraint.max_complexity(5)
        assert c.check(_result("Complexity: 3"))
        assert not c.check(_result("Complexity: 8"))


class TestNoSecurityIssues:
    def test_clean_output(self):
        c = Constraint.no_security_issues()
        assert c.check(_result("5 passed, all clean"))

    def test_detects_eval(self):
        c = Constraint.no_security_issues()
        assert not c.check(_result("result = eval(user_input)"))

    def test_detects_exec(self):
        c = Constraint.no_security_issues()
        assert not c.check(_result("exec(code_string)"))

    def test_detects_subprocess_shell(self):
        c = Constraint.no_security_issues()
        assert not c.check(_result("subprocess.run(cmd, shell=True)"))

    def test_allows_safe_subprocess(self):
        c = Constraint.no_security_issues()
        assert c.check(_result("subprocess.run(['ls', '-la'])"))
