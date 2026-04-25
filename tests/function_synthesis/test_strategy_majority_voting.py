# tests/test_strategy_majority_voting.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.training.strategies.majority_voting import MajorityVoting


@dataclass
class FakeAgentResult:
    output: str
    steps: int = 3
    tool_calls_total: int = 2
    cost: float = 0.01
    success: bool = True
    error: str | None = None


class FakeAgent:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._idx = 0

    def run(self, task: str, env: Any) -> FakeAgentResult:
        output = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return FakeAgentResult(output=output)


class FakeSpec:
    def to_prompt(self) -> str:
        return "What is 2+2?"


class FakeEnv:
    def checkpoint(self) -> str:
        return "cp0"
    def restore(self, checkpoint_id: str) -> None:
        pass
    def run_tests(self):
        return MagicMock(pass_rate=1.0, passed=1, total=1, all_passed=True)


class TestMajorityVoting:
    def test_clear_consensus(self):
        agent = FakeAgent(["ANSWER: 42", "ANSWER: 42", "ANSWER: 42", "ANSWER: 99", "ANSWER: 42"])
        strategy = MajorityVoting(n_samples=5, temperature=0.7)
        result = strategy.run(agent, FakeSpec(), FakeEnv())
        assert result.converged is True
        assert result.best_pass_rate == 1.0
        assert any("42" in str(h.agent_output) for h in result.history)

    def test_no_consensus(self):
        agent = FakeAgent(["ANSWER: 1", "ANSWER: 2", "ANSWER: 3", "ANSWER: 4", "ANSWER: 5"])
        strategy = MajorityVoting(n_samples=5, min_agreement=2)
        result = strategy.run(agent, FakeSpec(), FakeEnv())
        assert result.converged is False

    def test_early_stopping(self):
        call_count = 0
        class CountingAgent(FakeAgent):
            def run(self, task, env):
                nonlocal call_count
                call_count += 1
                return super().run(task, env)
        agent = CountingAgent(["ANSWER: 42"] * 16)
        strategy = MajorityVoting(n_samples=16, min_agreement=3)
        result = strategy.run(agent, FakeSpec(), FakeEnv())
        assert result.converged is True
        assert call_count < 16

    def test_tracks_cost(self):
        # Use diverse answers to prevent early stopping so all 4 samples run
        agent = FakeAgent(["ANSWER: 1", "ANSWER: 2", "ANSWER: 3", "ANSWER: 4"])
        strategy = MajorityVoting(n_samples=4, min_agreement=2)
        result = strategy.run(agent, FakeSpec(), FakeEnv())
        assert result.total_cost == pytest.approx(0.04)

    def test_callbacks_called(self):
        # Use diverse answers to prevent early stopping so all 3 epochs fire
        agent = FakeAgent(["ANSWER: 42", "ANSWER: 99", "ANSWER: 7"])
        strategy = MajorityVoting(n_samples=3, min_agreement=2)
        cb = MagicMock()
        cb.on_epoch_end.return_value = True
        strategy.run(agent, FakeSpec(), FakeEnv(), callbacks=[cb])
        assert cb.on_synthesis_start.called
        assert cb.on_synthesis_end.called
        assert cb.on_epoch_end.call_count == 3

    def test_callback_can_stop_early(self):
        agent = FakeAgent(["ANSWER: 42"] * 10)
        strategy = MajorityVoting(n_samples=10)
        cb = MagicMock()
        cb.on_epoch_end.return_value = False
        result = strategy.run(agent, FakeSpec(), FakeEnv(), callbacks=[cb])
        assert result.iterations == 1

    def test_no_extractable_answer(self):
        agent = FakeAgent(["I don't know"] * 4)
        strategy = MajorityVoting(n_samples=4, min_agreement=2)
        result = strategy.run(agent, FakeSpec(), FakeEnv())
        assert result.converged is False

    def test_winning_answer_in_result(self):
        agent = FakeAgent(["ANSWER: 12345"] * 5)
        strategy = MajorityVoting(n_samples=5)
        result = strategy.run(agent, FakeSpec(), FakeEnv())
        assert result.converged is True
        assert "12345" in result.history[-1].agent_output
