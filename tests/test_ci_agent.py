"""Tests for CI failure parsing and fix workflow."""
from __future__ import annotations

import pytest

from chimera.ci.failure_parser import FailureInfo, parse_ci_log
from chimera.ci.fix_workflow import CIFixWorkflow


class TestFailureParser:
    def test_pytest_failure(self):
        log = "FAILED tests/test_foo.py::test_bar - AssertionError: expected True"
        failures = parse_ci_log(log)
        assert len(failures) >= 1
        f = failures[0]
        assert f.file_path == "tests/test_foo.py"
        assert f.test_name == "test_bar"

    def test_pytest_traceback(self):
        log = "src/utils.py:42: TypeError"
        failures = parse_ci_log(log)
        assert len(failures) >= 1
        assert failures[0].line_number == 42

    def test_jest_failure(self):
        log = "FAIL src/components/App.test.tsx"
        failures = parse_ci_log(log)
        assert len(failures) >= 1
        assert "App.test.tsx" in failures[0].file_path

    def test_go_failure(self):
        log = "--- FAIL: TestCreateUser (0.02s)"
        failures = parse_ci_log(log)
        assert len(failures) >= 1
        assert failures[0].test_name == "TestCreateUser"

    def test_cargo_failure(self):
        log = "test utils::tests::test_parse ... FAILED"
        failures = parse_ci_log(log)
        assert len(failures) >= 1
        assert "test_parse" in failures[0].test_name

    def test_generic_error(self):
        log = "Error: connection refused to database"
        failures = parse_ci_log(log)
        assert len(failures) >= 1
        assert "connection refused" in failures[0].error_message

    def test_empty_log(self):
        assert parse_ci_log("") == []

    def test_summary(self):
        f = FailureInfo(
            test_name="test_foo",
            file_path="test.py",
            line_number=10,
            error_type="TypeError",
        )
        s = f.summary
        assert "test.py:10" in s
        assert "test_foo" in s


class TestCIFixWorkflow:
    def test_diagnose(self):
        wf = CIFixWorkflow()
        failures = wf.diagnose("FAILED tests/test.py::test_x - ValueError: bad")
        assert len(failures) >= 1

    def test_build_prompt(self):
        wf = CIFixWorkflow()
        failures = [FailureInfo(test_name="test_foo", error_message="bad value")]
        prompt = wf.build_prompt(failures, context="Python project")
        assert "test_foo" in prompt
        assert "Python project" in prompt

    def test_record_attempt(self):
        wf = CIFixWorkflow()
        failures = [FailureInfo(test_name="test_x")]
        attempt = wf.record_attempt(failures, "fix it", success=True, cost=0.05)
        assert attempt.success
        assert wf.succeeded
        assert wf.total_cost == 0.05

    def test_max_attempts(self):
        wf = CIFixWorkflow(max_attempts=5)
        assert wf.max_attempts == 5

    def test_not_succeeded_initially(self):
        wf = CIFixWorkflow()
        assert not wf.succeeded
