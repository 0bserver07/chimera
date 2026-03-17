"""Tests for CEGISStrategy (Counterexample-Guided Inductive Synthesis)."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from chimera.types import AgentResult, TestResult
from chimera.training.strategies.cegis import CEGISStrategy
from chimera.training.strategies.base import (
    Callback,
    EpochResult,
    SynthesisResult,
)
from chimera.training.spec import Spec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def spec():
    return Spec(text="Implement a calculator module")


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.run.return_value = AgentResult(
        output="fixed", steps=1, tool_calls_total=1, cost=0.01, success=True
    )
    return agent


def _make_test_result(passed: int, failed: int, failed_names: list[str] | None = None) -> TestResult:
    """Build a TestResult with pytest-style FAILED lines in output."""
    total = passed + failed
    if failed == 0:
        output = f"{passed} passed"
    else:
        names = failed_names or [f"test_file.py::test_{i}" for i in range(failed)]
        lines = [f"FAILED {name} - AssertionError: expected value" for name in names]
        lines.append(f"{passed} passed, {failed} failed")
        output = "\n".join(lines)
    return TestResult(passed=passed, failed=failed, errors=0, output=output)


# ---------------------------------------------------------------------------
# Test 1: CEGIS converges -- fixes tests one at a time
# ---------------------------------------------------------------------------


class TestCEGISConverges:
    """All tests pass after fixing failures one at a time."""

    def test_cegis_converges(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"

        # Sequence: initial run_tests -> 2 failed, fix one, re-test -> 1 failed,
        # next epoch initial run_tests -> 1 failed, fix, re-test -> 0 failed
        env.run_tests.side_effect = [
            # Epoch 1: initial test shows 2 failures
            _make_test_result(3, 2, ["test_file.py::test_add", "test_file.py::test_sub"]),
            # Epoch 1: after agent fix, 1 failure left
            _make_test_result(4, 1, ["test_file.py::test_sub"]),
            # Epoch 2: initial test shows 1 failure
            _make_test_result(4, 1, ["test_file.py::test_sub"]),
            # Epoch 2: after agent fix, all pass
            _make_test_result(5, 0),
        ]

        strategy = CEGISStrategy(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is True
        assert result.iterations == 2
        assert len(result.history) == 2
        assert result.best_pass_rate == 1.0


# ---------------------------------------------------------------------------
# Test 2: Prompt contains only one test name
# ---------------------------------------------------------------------------


class TestCEGISSingleFailurePrompt:
    """Prompt sent to agent contains only the first failing test."""

    def test_cegis_single_failure_prompt(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"

        env.run_tests.side_effect = [
            # Epoch 1: initial test -- two failures
            _make_test_result(3, 2, ["test_file.py::test_add", "test_file.py::test_sub"]),
            # Epoch 1: after fix -- all pass
            _make_test_result(5, 0),
        ]

        strategy = CEGISStrategy(max_iterations=10, patience=5)
        strategy.run(mock_agent, spec, env)

        # The agent should have been called once
        assert mock_agent.run.call_count == 1
        prompt = mock_agent.run.call_args[0][0]
        # Prompt should contain the first failure
        assert "test_add" in prompt
        # Should contain "Fix THIS" instruction
        assert "Fix THIS" in prompt


# ---------------------------------------------------------------------------
# Test 3: History of fixed counterexamples grows
# ---------------------------------------------------------------------------


class TestCEGISHistoryGrows:
    """Fixed counterexamples accumulate across epochs."""

    def test_cegis_history_grows(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"

        env.run_tests.side_effect = [
            # Epoch 1: initial -- 3 failures
            _make_test_result(2, 3, ["test_f.py::test_a", "test_f.py::test_b", "test_f.py::test_c"]),
            # Epoch 1: after fix -- 2 failures (test_a fixed)
            _make_test_result(3, 2, ["test_f.py::test_b", "test_f.py::test_c"]),
            # Epoch 2: initial -- 2 failures
            _make_test_result(3, 2, ["test_f.py::test_b", "test_f.py::test_c"]),
            # Epoch 2: after fix -- 1 failure (test_b fixed)
            _make_test_result(4, 1, ["test_f.py::test_c"]),
            # Epoch 3: initial -- 1 failure
            _make_test_result(4, 1, ["test_f.py::test_c"]),
            # Epoch 3: after fix -- all pass
            _make_test_result(5, 0),
        ]

        strategy = CEGISStrategy(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is True
        assert mock_agent.run.call_count == 3

        # Epoch 2 prompt should mention previously fixed test_a
        epoch2_prompt = mock_agent.run.call_args_list[1][0][0]
        assert "test_a" in epoch2_prompt
        assert "Previously fixed" in epoch2_prompt

        # Epoch 3 prompt should mention both test_a and test_b
        epoch3_prompt = mock_agent.run.call_args_list[2][0][0]
        assert "test_a" in epoch3_prompt
        assert "test_b" in epoch3_prompt


# ---------------------------------------------------------------------------
# Test 4: Patience -- stops after N epochs without progress
# ---------------------------------------------------------------------------


class TestCEGISPatience:
    """Stops after patience epochs without new tests passing."""

    def test_cegis_patience(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"

        # Every epoch: 2 failures, no improvement after fix
        stagnant = _make_test_result(3, 2, ["test_f.py::test_x", "test_f.py::test_y"])
        # Always returns the same result -- no progress
        env.run_tests.return_value = stagnant

        strategy = CEGISStrategy(max_iterations=50, patience=3)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is False
        assert result.failure_reason is not None
        assert "No improvement" in result.failure_reason
        # Epoch 1: improved (0->3), stale_epochs=0
        # Epochs 2,3,4: no improvement, stale_epochs hits 3 at epoch 4
        assert result.iterations == 4


# ---------------------------------------------------------------------------
# Test 5: Callbacks (on_epoch_start / on_epoch_end) are called
# ---------------------------------------------------------------------------


class TestCEGISCallbacksCalled:
    """on_epoch_start and on_epoch_end are called for each epoch."""

    def test_cegis_callbacks_called(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"

        env.run_tests.side_effect = [
            # Epoch 1: initial -- 1 failure
            _make_test_result(4, 1, ["test_f.py::test_z"]),
            # Epoch 1: after fix -- all pass
            _make_test_result(5, 0),
        ]

        cb = MagicMock(spec=Callback)
        cb.on_epoch_end.return_value = True

        strategy = CEGISStrategy(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, env, callbacks=[cb])

        assert result.converged is True
        cb.on_synthesis_start.assert_called_once()
        cb.on_epoch_start.assert_called_once_with(1)
        cb.on_epoch_end.assert_called_once()
        # on_epoch_end receives (epoch_num, epoch_result)
        epoch_arg = cb.on_epoch_end.call_args[0][0]
        result_arg = cb.on_epoch_end.call_args[0][1]
        assert epoch_arg == 1
        assert isinstance(result_arg, EpochResult)
        cb.on_synthesis_end.assert_called_once()
