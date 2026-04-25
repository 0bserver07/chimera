# tests/test_aimo_integration.py
"""End-to-end integration test for AIMO3 pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from chimera.eval.benchmarks.aimo import AIMOBenchmark
from chimera.eval.harness import Harness


@dataclass
class FakeAgentResult:
    output: str
    steps: int = 3
    tool_calls_total: int = 2
    cost: float = 0.01
    success: bool = True
    error: str | None = None


class FakeAgent:
    def __init__(self, answers: dict[str, int]):
        self._answers = answers

    def run(self, task: str, env: Any) -> FakeAgentResult:
        for problem_text, answer in self._answers.items():
            if problem_text in task:
                return FakeAgentResult(output=f"After solving, ANSWER: {answer}")
        return FakeAgentResult(output="I cannot solve this problem")


class TestAIMOIntegration:
    @pytest.fixture
    def problems_file(self, tmp_path):
        problems = [
            {"id": "p1", "problem": "What is 2^10?", "answer": 1024},
            {"id": "p2", "problem": "What is 13!?", "answer": 6227020800},
            {"id": "p3", "problem": "What is gcd(100, 75)?", "answer": 25},
        ]
        path = tmp_path / "problems.json"
        path.write_text(json.dumps(problems))
        return str(path)

    def test_full_pipeline(self, problems_file):
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = FakeAgent({
            "What is 2^10?": 1024,
            "What is 13!?": 6227020800,
            "What is gcd(100, 75)?": 25,
        })
        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()
        assert result.benchmark == "aimo3"
        assert result.total == 3
        assert result.passed == 3
        assert result.pass_rate == 1.0

    def test_partial_solve(self, problems_file):
        benchmark = AIMOBenchmark(problems_path=problems_file)
        agent = FakeAgent({
            "What is 2^10?": 1024,
            "What is 13!?": 999,
            "What is gcd(100, 75)?": 50,
        })
        harness = Harness(benchmark=benchmark, agent=agent)
        result = harness.run()
        assert result.total == 3
        assert result.passed == 1
        assert result.pass_rate == pytest.approx(1 / 3)

    def test_imports_from_top_level(self):
        from chimera import MajorityVoting, AIMOEnsemble
        from chimera.eval.benchmarks import AIMOBenchmark
        from chimera.tools import VerifyTool, verify
        assert MajorityVoting is not None
        assert AIMOEnsemble is not None
        assert AIMOBenchmark is not None
        assert VerifyTool is not None
        assert verify is not None
