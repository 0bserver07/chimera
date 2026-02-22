"""Tests for TestConvergence strategy and Strategy base class."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from chimera.types import AgentResult, TestResult
from chimera.training.strategies.convergence import TestConvergence
from chimera.training.strategies.base import (
    EpochResult,
    SynthesisResult,
    Callback,
    Strategy,
)
from chimera.training.spec import Spec


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
def mock_env():
    env = MagicMock()
    env.checkpoint.return_value = "cp-0"
    env.run_tests.return_value = TestResult(
        passed=10, failed=0, errors=0, output="ok"
    )
    return env


@pytest.fixture
def spec():
    return Spec(text="Implement a calculator module")


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestEpochResult:
    def test_epoch_result(self):
        er = EpochResult(
            epoch=1,
            pass_rate=0.8,
            passed=8,
            total=10,
            agent_output="some code",
            checkpoint_id="cp-1",
            improved=True,
            cost=0.05,
        )
        assert er.epoch == 1
        assert er.pass_rate == 0.8
        assert er.passed == 8
        assert er.total == 10
        assert er.agent_output == "some code"
        assert er.checkpoint_id == "cp-1"
        assert er.improved is True
        assert er.cost == 0.05

    def test_epoch_result_defaults(self):
        er = EpochResult(
            epoch=1, pass_rate=0.0, passed=0, total=10, agent_output="x"
        )
        assert er.checkpoint_id is None
        assert er.improved is False
        assert er.cost == 0.0


class TestSynthesisResult:
    def test_synthesis_result_converged(self):
        sr = SynthesisResult(
            converged=True,
            iterations=3,
            total_cost=0.15,
            best_pass_rate=1.0,
            history=[
                EpochResult(epoch=1, pass_rate=0.5, passed=5, total=10, agent_output="a"),
                EpochResult(epoch=2, pass_rate=0.8, passed=8, total=10, agent_output="b"),
                EpochResult(epoch=3, pass_rate=1.0, passed=10, total=10, agent_output="c"),
            ],
        )
        assert sr.converged is True
        assert sr.iterations == 3
        assert sr.total_cost == 0.15
        assert sr.best_pass_rate == 1.0
        assert len(sr.history) == 3
        assert sr.failure_reason is None

    def test_synthesis_result_not_converged(self):
        sr = SynthesisResult(
            converged=False,
            iterations=5,
            total_cost=0.25,
            best_pass_rate=0.6,
            failure_reason="Did not converge after 5 iterations (best: 60.0%)",
        )
        assert sr.converged is False
        assert sr.failure_reason is not None
        assert "60.0%" in sr.failure_reason


# ---------------------------------------------------------------------------
# Strategy ABC
# ---------------------------------------------------------------------------


class TestStrategyABC:
    def test_strategy_is_abstract(self):
        """Strategy cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Strategy()  # type: ignore[abstract]

    def test_subclass_must_implement_run(self):
        """A subclass that doesn't implement run cannot be instantiated."""

        class Incomplete(Strategy):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# TestConvergence: convergence scenarios
# ---------------------------------------------------------------------------


class TestConvergenceImmediate:
    """Agent solves on first try -- tests pass immediately."""

    def test_convergence_immediate(self, mock_agent, mock_env, spec):
        strategy = TestConvergence(max_iterations=10, patience=3)
        result = strategy.run(mock_agent, spec, mock_env)

        assert result.converged is True
        assert result.iterations == 1
        assert result.best_pass_rate == 1.0
        assert len(result.history) == 1
        assert result.history[0].improved is True
        assert result.failure_reason is None
        mock_agent.run.assert_called_once()
        mock_env.run_tests.assert_called_once()


class TestConvergenceGradual:
    """Agent improves over 3 epochs then converges."""

    def test_convergence_gradual(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"
        env.run_tests.side_effect = [
            TestResult(passed=3, failed=7, errors=0, output="partial"),
            TestResult(passed=7, failed=3, errors=0, output="better"),
            TestResult(passed=10, failed=0, errors=0, output="ok"),
        ]

        strategy = TestConvergence(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is True
        assert result.iterations == 3
        assert result.best_pass_rate == 1.0
        assert len(result.history) == 3
        # Each epoch should be an improvement
        assert result.history[0].improved is True
        assert result.history[1].improved is True
        assert result.history[2].improved is True
        # Total cost = 3 * 0.01
        assert result.total_cost == pytest.approx(0.03)


class TestConvergencePatienceExceeded:
    """No improvement -- patience runs out."""

    def test_convergence_patience_exceeded(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"
        # First epoch improves from 0, then every subsequent epoch stays the same
        env.run_tests.side_effect = [
            TestResult(passed=5, failed=5, errors=0, output="..."),
            TestResult(passed=5, failed=5, errors=0, output="..."),
            TestResult(passed=5, failed=5, errors=0, output="..."),
            TestResult(passed=5, failed=5, errors=0, output="..."),
        ]

        strategy = TestConvergence(max_iterations=50, patience=3)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is False
        # Epoch 1: improved (0->0.5). Epochs 2,3,4: no improvement (patience=3 hit at epoch 4)
        assert result.iterations == 4
        assert result.best_pass_rate == 0.5
        assert result.failure_reason is not None


class TestConvergenceRollback:
    """Pass rate drops -- rollback to best checkpoint."""

    def test_convergence_rollback(self, mock_agent, spec):
        env = MagicMock()
        checkpoint_counter = iter(["cp-1", "cp-2", "cp-3", "cp-4"])
        env.checkpoint.side_effect = lambda: next(checkpoint_counter)
        env.run_tests.side_effect = [
            TestResult(passed=5, failed=5, errors=0, output="..."),  # epoch 1: improve
            TestResult(passed=3, failed=7, errors=0, output="..."),  # epoch 2: regress
            TestResult(passed=8, failed=2, errors=0, output="..."),  # epoch 3: improve
            TestResult(passed=10, failed=0, errors=0, output="ok"),  # epoch 4: converge
        ]

        strategy = TestConvergence(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is True
        assert result.iterations == 4
        # Epoch 2 regressed -> should have rolled back
        env.restore.assert_called_once_with("cp-1")
        # Epoch 1 improved, epoch 2 did not, epoch 3 improved, epoch 4 improved
        assert result.history[0].improved is True
        assert result.history[1].improved is False
        assert result.history[2].improved is True
        assert result.history[3].improved is True


class TestConvergenceMaxIterations:
    """Hits max iterations without converging."""

    def test_convergence_max_iterations(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"
        # Always 5/10, never improves after first epoch
        env.run_tests.return_value = TestResult(
            passed=5, failed=5, errors=0, output="..."
        )

        strategy = TestConvergence(max_iterations=3, patience=10)
        result = strategy.run(mock_agent, spec, env)

        assert result.converged is False
        assert result.iterations == 3
        assert result.failure_reason is not None


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestConvergenceCallbacks:
    """Callbacks are called correctly."""

    def test_convergence_with_callbacks(self, mock_agent, mock_env, spec):
        cb = MagicMock(spec=Callback)
        cb.on_epoch_end.return_value = True  # keep going

        strategy = TestConvergence(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, mock_env, callbacks=[cb])

        assert result.converged is True
        cb.on_synthesis_start.assert_called_once()
        cb.on_epoch_start.assert_called_once_with(1)
        cb.on_epoch_end.assert_called_once()
        # on_epoch_end receives (epoch, epoch_result)
        epoch_arg = cb.on_epoch_end.call_args[0][0]
        result_arg = cb.on_epoch_end.call_args[0][1]
        assert epoch_arg == 1
        assert isinstance(result_arg, EpochResult)
        cb.on_synthesis_end.assert_called_once()

    def test_convergence_callback_stops_early(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"
        env.run_tests.return_value = TestResult(
            passed=5, failed=5, errors=0, output="..."
        )

        cb = MagicMock(spec=Callback)
        cb.on_epoch_end.return_value = False  # stop immediately

        strategy = TestConvergence(max_iterations=50, patience=50)
        result = strategy.run(mock_agent, spec, env, callbacks=[cb])

        # Should stop after first epoch because callback returned False
        assert result.converged is False
        assert result.iterations == 1


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


class TestConvergenceConstraints:
    """Tests pass but constraints fail -- should keep going."""

    def test_convergence_with_constraints(self, mock_agent, spec):
        env = MagicMock()
        env.checkpoint.return_value = "cp-0"
        # Tests pass every time
        env.run_tests.return_value = TestResult(
            passed=10, failed=0, errors=0, output="ok"
        )

        constraint = MagicMock()
        # Constraint fails first two times, passes on third
        from chimera.training.constraint import ConstraintResult

        constraint.evaluate.side_effect = [
            ConstraintResult(name="style", satisfied=False, message="bad style"),
            ConstraintResult(name="style", satisfied=False, message="bad style"),
            ConstraintResult(name="style", satisfied=True, message="good style"),
        ]

        strategy = TestConvergence(max_iterations=10, patience=5)
        result = strategy.run(mock_agent, spec, env, constraints=[constraint])

        # Should converge on epoch 3 when constraint finally passes
        assert result.converged is True
        assert result.iterations == 3


# ---------------------------------------------------------------------------
# _build_task
# ---------------------------------------------------------------------------


class TestBuildTask:
    def test_build_task_initial(self, spec):
        """First epoch uses spec only."""
        strategy = TestConvergence()
        task = strategy._build_task(spec, [])
        assert spec.to_prompt() in task
        # No failure info since no history
        assert "Previous attempt" not in task

    def test_build_task_with_history(self, spec):
        """Subsequent epochs include failure info."""
        strategy = TestConvergence()
        history = [
            EpochResult(
                epoch=1,
                pass_rate=0.3,
                passed=3,
                total=10,
                agent_output="attempt",
            ),
        ]
        task = strategy._build_task(spec, history)
        assert spec.to_prompt() in task
        assert "3/10 tests passed" in task
        assert "Fix the failing tests" in task
