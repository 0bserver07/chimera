# tests/test_strategy_aimo_ensemble.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.training.strategies.aimo_ensemble import AIMOEnsemble
from chimera.training.strategies.base import SynthesisResult, EpochResult


@dataclass
class FakeAgentResult:
    output: str
    steps: int = 3
    tool_calls_total: int = 2
    cost: float = 0.01
    success: bool = True
    error: str | None = None


class FakeSpec:
    def to_prompt(self) -> str:
        return "Solve this problem"


class FakeEnv:
    def checkpoint(self) -> str:
        return "cp0"
    def restore(self, checkpoint_id: str) -> None:
        pass
    def run_tests(self):
        return MagicMock(pass_rate=1.0, passed=1, total=1, all_passed=True)


class TestAIMOEnsemble:
    def test_returns_voting_result_when_converged(self):
        class ConvergingAgent:
            _idx = 0
            def run(self, task, env):
                self._idx += 1
                return FakeAgentResult(output="ANSWER: 42")

        strategy = AIMOEnsemble(voting_samples=4, min_agreement=2)
        result = strategy.run(ConvergingAgent(), FakeSpec(), FakeEnv())
        assert result.converged is True

    def test_falls_back_to_tree_search(self):
        class DivergingAgent:
            _idx = 0
            def run(self, task, env):
                self._idx += 1
                return FakeAgentResult(output=f"ANSWER: {self._idx}")

        strategy = AIMOEnsemble(voting_samples=4, min_agreement=3)

        with patch(
            "chimera.training.strategies.aimo_ensemble.TreeSearch.run"
        ) as mock_tree:
            mock_tree.return_value = SynthesisResult(
                converged=True, iterations=5, total_cost=0.1,
                best_pass_rate=1.0, history=[],
            )
            result = strategy.run(DivergingAgent(), FakeSpec(), FakeEnv())

        assert result.converged is True
        assert mock_tree.called

    def test_tracks_total_cost(self):
        class ConvergingAgent:
            def run(self, task, env):
                return FakeAgentResult(output="ANSWER: 42", cost=0.05)

        strategy = AIMOEnsemble(voting_samples=3, min_agreement=2)
        result = strategy.run(ConvergingAgent(), FakeSpec(), FakeEnv())
        assert result.total_cost > 0
