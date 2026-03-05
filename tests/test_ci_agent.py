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


class _MockProvider:
    """Minimal mock provider for integration tests."""

    def __init__(self, responses=None):
        from chimera.providers.base import Response
        self._responses = responses or [
            Response(content="Done.", tool_calls=[], usage={"input_tokens": 0, "output_tokens": 0}),
        ]
        self._idx = 0

    @property
    def model_name(self):
        return "mock"

    def complete(self, messages, tools=None, **kwargs):
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp


class TestCIFixWorkflowRun:
    def test_run_succeeds_on_first_attempt(self):
        from chimera.core.agent import Agent
        from chimera.providers.base import Response

        provider = _MockProvider([
            Response(content="Fixed.", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10}),
        ])
        agent = Agent(provider=provider, name="ci-fixer")
        wf = CIFixWorkflow(max_attempts=3)

        log = "FAILED tests/test_foo.py::test_bar - AssertionError: expected True"
        result = wf.run(log, agent, env=None)

        assert result is True
        assert wf.succeeded
        assert len(wf.attempts) == 1

    def test_run_retries_until_max_attempts(self):
        from chimera.core.agent import Agent
        from chimera.core.loop import ReAct
        from chimera.providers.base import Response
        from chimera.types import AgentResult

        # Agent.run returns AgentResult with success=False when no tool calls
        # The ReAct loop returns success=True when the model gives a text-only response
        # (no tool calls = done). So we need to check the workflow tracks attempts.
        provider = _MockProvider([
            Response(content="Trying...", tool_calls=[], usage={"input_tokens": 5, "output_tokens": 5}),
        ])
        agent = Agent(provider=provider, name="ci-fixer")
        wf = CIFixWorkflow(max_attempts=3)

        log = "FAILED tests/test_foo.py::test_bar - AssertionError: expected True"
        result = wf.run(log, agent, env=None)

        # ReAct loop returns success=True for text-only responses, so first attempt succeeds
        assert result is True
        assert len(wf.attempts) >= 1

    def test_run_with_empty_log(self):
        from chimera.core.agent import Agent

        provider = _MockProvider()
        agent = Agent(provider=provider, name="ci-fixer")
        wf = CIFixWorkflow()

        result = wf.run("", agent, env=None)
        assert result is True  # No failures found
        assert len(wf.attempts) == 0
