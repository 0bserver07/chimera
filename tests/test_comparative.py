"""Tests for chimera.eval.comparative — comparative agent benchmarking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chimera.eval.comparative import ComparativeEval, ComparisonReport, TaskResult


# -- Helpers ------------------------------------------------------------------


@dataclass
class FakeAgentResult:
    output: str
    cost: float
    steps: int


class FakeAgent:
    """Agent that returns configurable outputs per prompt."""

    def __init__(self, responses: dict[str, str] | None = None, default: str = "ok"):
        self._responses = responses or {}
        self._default = default
        self.cost = 0.01
        self.steps = 3

    def run(self, task: str, env: Any) -> FakeAgentResult:
        output = self._responses.get(task, self._default)
        return FakeAgentResult(output=output, cost=self.cost, steps=self.steps)


class FakeProvider:
    """Minimal provider stub."""

    pass


# -- Tests --------------------------------------------------------------------


class TestComparativeEval:
    def test_add_configs(self):
        """add_config registers named configurations for later execution."""
        problems = [{"id": "p1", "prompt": "do stuff"}]
        comp = ComparativeEval(FakeProvider(), problems)

        comp.add_config("fast", lambda p: FakeAgent(default="ok"))
        comp.add_config("slow", lambda p: FakeAgent(default="ok"))

        # Internal state should have both configs
        assert "fast" in comp._configs
        assert "slow" in comp._configs
        assert len(comp._configs) == 2

    def test_run_with_mock_provider(self):
        """run() executes all problems through all configs and collects results."""
        problems = [
            {"id": "p1", "prompt": "task A", "expected": "ok"},
            {"id": "p2", "prompt": "task B", "expected": "ok"},
        ]
        comp = ComparativeEval(FakeProvider(), problems)

        comp.add_config(
            "good",
            lambda p: FakeAgent(default="ok"),
        )
        comp.add_config(
            "bad",
            lambda p: FakeAgent(default="fail"),
        )

        report = comp.run()

        assert isinstance(report, ComparisonReport)
        assert set(report.configs) == {"good", "bad"}
        # "good" config should pass both
        assert all(r.passed for r in report.results["good"])
        # "bad" config should fail both (output "fail" does not contain "ok")
        assert all(not r.passed for r in report.results["bad"])

    def test_comparison_report_summary(self):
        """summary() returns a human-readable multi-line string."""
        results = {
            "alpha": [
                TaskResult("p1", "ok", 0.01, 3, True),
                TaskResult("p2", "ok", 0.02, 5, True),
            ],
            "beta": [
                TaskResult("p1", "ok", 0.01, 2, True),
                TaskResult("p2", "nope", 0.03, 4, False),
            ],
        }
        report = ComparisonReport(configs=["alpha", "beta"], results=results)
        summary = report.summary()

        assert "Comparative Evaluation Summary" in summary
        assert "alpha" in summary
        assert "beta" in summary
        assert "pass_rate=100.0%" in summary
        assert "pass_rate=50.0%" in summary

    def test_best_config_selection(self):
        """best_config() returns the config with the highest pass rate."""
        results = {
            "weak": [
                TaskResult("p1", "x", 0.01, 3, False),
                TaskResult("p2", "x", 0.01, 3, False),
            ],
            "strong": [
                TaskResult("p1", "ok", 0.01, 3, True),
                TaskResult("p2", "ok", 0.01, 3, True),
            ],
            "medium": [
                TaskResult("p1", "ok", 0.01, 3, True),
                TaskResult("p2", "x", 0.01, 3, False),
            ],
        }
        report = ComparisonReport(
            configs=["weak", "strong", "medium"], results=results
        )
        assert report.best_config() == "strong"

    def test_by_problem_breakdown(self):
        """by_problem() pivots results so each problem shows all configs."""
        results = {
            "A": [
                TaskResult("p1", "a1", 0.01, 2, True),
                TaskResult("p2", "a2", 0.02, 3, False),
            ],
            "B": [
                TaskResult("p1", "b1", 0.03, 4, False),
                TaskResult("p2", "b2", 0.04, 5, True),
            ],
        }
        report = ComparisonReport(configs=["A", "B"], results=results)
        breakdown = report.by_problem()

        assert set(breakdown.keys()) == {"p1", "p2"}
        assert breakdown["p1"]["A"].output == "a1"
        assert breakdown["p1"]["B"].output == "b1"
        assert breakdown["p2"]["A"].passed is False
        assert breakdown["p2"]["B"].passed is True
