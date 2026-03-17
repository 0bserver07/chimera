"""Tests for IncrementalStrategy."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.training.spec import Spec
from chimera.training.strategies.base import EpochResult
from chimera.training.strategies.incremental import (
    IncrementalStrategy,
    SynthesisTarget,
)
from chimera.types import AgentResult, TestResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.run.return_value = AgentResult(
        output="done", steps=1, tool_calls_total=1, cost=0.01, success=True
    )
    return agent


@pytest.fixture
def spec():
    return Spec(text="Implement a calculator module")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIncrementalConverges:
    """All tests pass on first check -- converges immediately."""

    def test_incremental_converges(self, mock_agent, spec):
        env = MagicMock()
        env.run_tests.return_value = TestResult(
            passed=10, failed=0, errors=0, output="ok"
        )

        strategy = IncrementalStrategy(max_iterations=10, patience=3)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is True
        assert result.best_pass_rate == 1.0
        assert result.iterations == 1
        assert len(result.history) == 1
        assert result.history[0].pass_rate == 1.0
        assert result.failure_reason is None
        # Agent should NOT have been called -- tests already pass
        mock_agent.run.assert_not_called()


class TestIdentifyTargets:
    """Extract file:line from test output and find enclosing function."""

    def test_identify_targets(self):
        strategy = IncrementalStrategy()

        source = (
            "def helper():\n"
            "    pass\n"
            "\n"
            "def compute(x):\n"
            "    return x + 1\n"
            "\n"
            "def other():\n"
            "    pass\n"
        )

        env = MagicMock()
        env.read_file.return_value = source

        test_result = MagicMock()
        test_result.output = (
            "FAILED test_calc.py:12 - AssertionError\n"
            "  File calc.py:5 in compute\n"
            "    return x + 1\n"
        )

        targets = strategy._identify_targets(env, test_result)

        assert len(targets) == 1
        assert targets[0].file == "calc.py"
        assert targets[0].function_name == "compute"
        assert targets[0].line_start == 4
        assert targets[0].line_end == 5
        assert "return x + 1" in targets[0].source

    def test_identify_targets_skips_test_files(self):
        """References to test files should be ignored."""
        strategy = IncrementalStrategy()

        env = MagicMock()
        test_result = MagicMock()
        test_result.output = "FAILED test_foo.py:10 - AssertionError"

        targets = strategy._identify_targets(env, test_result)
        assert targets == []
        env.read_file.assert_not_called()


class TestFindEnclosingFunction:
    """AST finds function containing a given line."""

    def test_find_enclosing_function(self):
        strategy = IncrementalStrategy()

        source = (
            "import os\n"
            "\n"
            "def foo():\n"
            "    x = 1\n"
            "    return x\n"
            "\n"
            "def bar():\n"
            "    return 42\n"
        )

        # Line 4 is inside foo()
        result = strategy._find_enclosing_function(source, 4)
        assert result is not None
        assert result["name"] == "foo"
        assert result["start"] == 3
        assert result["end"] == 5
        assert "x = 1" in result["source"]

    def test_find_enclosing_function_second_function(self):
        strategy = IncrementalStrategy()

        source = (
            "def foo():\n"
            "    return 1\n"
            "\n"
            "def bar():\n"
            "    return 42\n"
        )

        # Line 5 is inside bar()
        result = strategy._find_enclosing_function(source, 5)
        assert result is not None
        assert result["name"] == "bar"

    def test_find_enclosing_function_no_match(self):
        strategy = IncrementalStrategy()

        source = (
            "def foo():\n"
            "    return 1\n"
            "\n"
            "x = 10\n"
        )

        # Line 4 is not inside any function
        result = strategy._find_enclosing_function(source, 4)
        assert result is None

    def test_find_enclosing_function_syntax_error(self):
        strategy = IncrementalStrategy()

        result = strategy._find_enclosing_function("def broken(:\n", 1)
        assert result is None


class TestTargetedPrompt:
    """Prompt mentions specific function name and file."""

    def test_targeted_prompt(self, spec):
        strategy = IncrementalStrategy()

        target = SynthesisTarget(
            file="calc.py",
            function_name="compute",
            line_start=4,
            line_end=6,
            source="def compute(x):\n    return x + 1",
            related_failure="AssertionError: expected 3 got 2",
        )

        test_result = MagicMock()
        test_result.output = "FAILED: expected 3 got 2"

        prompt = strategy._build_targeted_prompt(spec, target, test_result)

        assert "compute()" in prompt
        assert "calc.py" in prompt
        assert "lines 4-6" in prompt
        assert "def compute(x):" in prompt
        assert "return x + 1" in prompt
        assert "Fix ONLY this function" in prompt
        assert spec.to_prompt() in prompt


class TestPatience:
    """Stops after N stale epochs with no improvement."""

    def test_patience(self, mock_agent, spec):
        env = MagicMock()
        # Tests always fail with same rate -- no improvement
        env.run_tests.return_value = TestResult(
            passed=5, failed=5, errors=0, output="FAILED stuff.py:10"
        )
        env.read_file.return_value = "def stuff():\n    pass\n"

        strategy = IncrementalStrategy(max_iterations=50, patience=3)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is False
        # Epoch 1: improved (0 -> 0.5), stale_count=0
        # Epoch 2: no improvement, stale_count=1
        # Epoch 3: no improvement, stale_count=2
        # Epoch 4: no improvement, stale_count=3 >= patience=3 -> stop
        assert result.iterations == 4
        assert result.best_pass_rate == 0.5
        assert result.failure_reason == "Did not converge"
