"""Tests for Trainer and Callbacks (Task 15)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from chimera.training.trainer import Trainer
from chimera.training.callbacks import CostLimit, EpochCheckpoint, HistoryRecorder
from chimera.training.spec import Spec
from chimera.training.architecture import Architecture, Layer
from chimera.training.strategies.base import EpochResult, SynthesisResult, Callback, Strategy


# ---- Callback Tests ----


def test_cost_limit_allows():
    cb = CostLimit(max_cost=1.0)
    result = EpochResult(epoch=1, pass_rate=0.5, passed=5, total=10, agent_output="ok", cost=0.3)
    assert cb.on_epoch_end(1, result) is True


def test_cost_limit_stops():
    cb = CostLimit(max_cost=0.5)
    r1 = EpochResult(epoch=1, pass_rate=0.5, passed=5, total=10, agent_output="ok", cost=0.3)
    r2 = EpochResult(epoch=2, pass_rate=0.6, passed=6, total=10, agent_output="ok", cost=0.3)
    assert cb.on_epoch_end(1, r1) is True  # 0.3 < 0.5
    assert cb.on_epoch_end(2, r2) is False  # 0.6 >= 0.5


def test_epoch_checkpoint_records():
    cb = EpochCheckpoint(every=2)
    r1 = EpochResult(epoch=1, pass_rate=0.5, passed=5, total=10, agent_output="ok", checkpoint_id="cp-1")
    r2 = EpochResult(epoch=2, pass_rate=0.6, passed=6, total=10, agent_output="ok", checkpoint_id="cp-2")
    r3 = EpochResult(epoch=3, pass_rate=0.7, passed=7, total=10, agent_output="ok", checkpoint_id="cp-3")
    cb.on_epoch_end(1, r1)
    cb.on_epoch_end(2, r2)
    cb.on_epoch_end(3, r3)
    assert cb.checkpoints == ["cp-2"]


def test_epoch_checkpoint_skips_none():
    """Checkpoint with no checkpoint_id should not be recorded."""
    cb = EpochCheckpoint(every=1)
    r = EpochResult(epoch=1, pass_rate=0.5, passed=5, total=10, agent_output="ok", checkpoint_id=None)
    cb.on_epoch_end(1, r)
    assert cb.checkpoints == []


def test_history_recorder():
    cb = HistoryRecorder()
    assert cb.started is False
    cb.on_synthesis_start()
    assert cb.started is True
    r = EpochResult(epoch=1, pass_rate=1.0, passed=10, total=10, agent_output="ok")
    cb.on_epoch_end(1, r)
    assert len(cb.epochs) == 1
    sr = SynthesisResult(converged=True, iterations=1, total_cost=0.01, best_pass_rate=1.0)
    cb.on_synthesis_end(sr)
    assert cb.finished is True
    assert cb.final_result is not None


def test_history_recorder_records_multiple_epochs():
    cb = HistoryRecorder()
    for i in range(5):
        r = EpochResult(epoch=i + 1, pass_rate=i * 0.2, passed=i * 2, total=10, agent_output=f"step {i}")
        cb.on_epoch_end(i + 1, r)
    assert len(cb.epochs) == 5
    assert cb.epochs[0].epoch == 1
    assert cb.epochs[4].epoch == 5


# ---- Trainer Tests ----


@pytest.fixture
def mock_agent():
    return MagicMock()


@pytest.fixture
def mock_env():
    return MagicMock()


@pytest.fixture
def spec():
    return Spec.from_string("Build a calculator")


def test_trainer_creation(mock_agent, mock_env, spec):
    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env)
    assert trainer.spec is spec
    assert trainer.agent is mock_agent
    assert trainer.env is mock_env


def test_trainer_default_constraints(mock_agent, mock_env, spec):
    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env)
    assert trainer.constraints == []


def test_trainer_with_architecture(mock_agent, mock_env, spec):
    arch = Architecture([Layer("core"), Layer("api", depends_on=["core"])])
    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env, architecture=arch)
    assert trainer.architecture is arch


def test_trainer_synthesize_delegates_to_strategy(mock_agent, mock_env, spec):
    mock_strategy = MagicMock(spec=Strategy)
    expected = SynthesisResult(converged=True, iterations=1, total_cost=0.01, best_pass_rate=1.0)
    mock_strategy.run.return_value = expected

    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env)
    result = trainer.synthesize(strategy=mock_strategy)

    assert result is expected
    mock_strategy.run.assert_called_once()


def test_trainer_synthesize_default_strategy(mock_agent, mock_env, spec):
    """Without explicit strategy, Trainer uses TestConvergence."""
    with patch("chimera.training.trainer.TestConvergence") as MockTC:
        expected = SynthesisResult(converged=True, iterations=1, total_cost=0.01, best_pass_rate=1.0)
        MockTC.return_value.run.return_value = expected

        trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env)
        result = trainer.synthesize()

        assert result is expected
        MockTC.return_value.run.assert_called_once()


def test_trainer_synthesize_passes_callbacks(mock_agent, mock_env, spec):
    mock_strategy = MagicMock(spec=Strategy)
    mock_strategy.run.return_value = SynthesisResult(
        converged=True, iterations=1, total_cost=0.01, best_pass_rate=1.0
    )

    cb = HistoryRecorder()
    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env)
    trainer.synthesize(strategy=mock_strategy, callbacks=[cb])

    # Verify callbacks were passed through
    call_kwargs = mock_strategy.run.call_args
    assert cb in call_kwargs.kwargs.get("callbacks", call_kwargs[1].get("callbacks", []))


def test_trainer_synthesize_passes_constraints(mock_agent, mock_env, spec):
    mock_strategy = MagicMock(spec=Strategy)
    mock_strategy.run.return_value = SynthesisResult(
        converged=True, iterations=1, total_cost=0.01, best_pass_rate=1.0
    )

    from chimera.training.constraint import Constraint

    constraint = Constraint.tests_pass()

    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env, constraints=[constraint])
    trainer.synthesize(strategy=mock_strategy)

    call_kwargs = mock_strategy.run.call_args
    constraints_passed = call_kwargs.kwargs.get("constraints", call_kwargs[1].get("constraints", []))
    assert constraint in constraints_passed


def test_trainer_synthesize_strategy_receives_correct_args(mock_agent, mock_env, spec):
    """Verify the exact arguments passed to strategy.run()."""
    mock_strategy = MagicMock(spec=Strategy)
    mock_strategy.run.return_value = SynthesisResult(
        converged=True, iterations=1, total_cost=0.01, best_pass_rate=1.0
    )

    trainer = Trainer(spec=spec, agent=mock_agent, env=mock_env)
    trainer.synthesize(strategy=mock_strategy)

    mock_strategy.run.assert_called_once_with(
        agent=mock_agent,
        spec=spec,
        env=mock_env,
        constraints=[],
        callbacks=[],
    )
