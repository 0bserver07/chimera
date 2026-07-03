"""run_matrix over fake runners × fake benchmarks (no LLM, no network).

Drives the real :func:`~chimera.eval.matrix.run_matrix` (which reuses the real
:class:`~chimera.eval.harness.Harness`) with deterministic fakes so the grid
shape, pass-rate maths, summary rendering, and per-cell error isolation are
verified without a model.
"""

from __future__ import annotations

from typing import Any

from chimera.eval.harness import Benchmark
from chimera.eval.matrix import MatrixCell, MatrixReport, run_matrix
from chimera.eval.runners.base import AgentRunResult


class FakeRunner:
    """An :class:`AgentRunner` whose answer is fixed for every task."""

    def __init__(self, id: str, answer: str) -> None:
        self.id = id
        self._answer = answer

    def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
        return AgentRunResult(
            answer=self._answer,
            tool_calls=3,
            llm_calls=2,
            cost_usd=0.01,
            wall_clock_sec=0.5,
            status="completed",
        )


class FakeBenchmark(Benchmark):
    """Two trivial tasks; a task passes iff the agent output equals *golden*."""

    def __init__(self, name: str, golden: str) -> None:
        self._name = name
        self._golden = golden

    def name(self) -> str:
        return self._name

    def tasks(self) -> list[dict[str, Any]]:
        return [
            {"id": f"{self._name}-1", "prompt": "solve one"},
            {"id": f"{self._name}-2", "prompt": "solve two"},
        ]

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        return agent_output == self._golden


def test_run_matrix_builds_full_grid() -> None:
    runners = [FakeRunner("alpha", "GOLD"), FakeRunner("beta", "WRONG")]
    benches = [FakeBenchmark("benchA", "GOLD"), FakeBenchmark("benchB", "GOLD")]

    report = run_matrix(runners, benches, model="glm-5")

    assert isinstance(report, MatrixReport)
    assert report.model == "glm-5"
    assert len(report.cells) == 4  # 2 agents × 2 benchmarks
    assert all(isinstance(c, MatrixCell) for c in report.cells)

    by_agent = report.by_agent()
    # alpha answers GOLD -> passes both tasks on both benchmarks.
    assert by_agent["alpha"]["benchA"].pass_rate == 1.0
    assert by_agent["alpha"]["benchA"].total == 2
    assert by_agent["alpha"]["benchB"].passed == 2
    # beta answers WRONG -> 0% on every column.
    assert by_agent["beta"]["benchA"].pass_rate == 0.0
    assert by_agent["beta"]["benchB"].passed == 0
    # Runner-native counters flow onto the cell.
    assert by_agent["alpha"]["benchA"].tool_calls == 3
    assert by_agent["alpha"]["benchA"].status == "completed"
    assert by_agent["alpha"]["benchA"].cost_usd == 0.02  # 2 tasks × $0.01


def test_matrix_summary_contains_agents_and_benchmarks() -> None:
    runners = [FakeRunner("alpha", "GOLD"), FakeRunner("beta", "GOLD")]
    benches = [FakeBenchmark("benchA", "GOLD"), FakeBenchmark("benchB", "GOLD")]

    text = run_matrix(runners, benches, model="glm-5").summary()

    for token in ("alpha", "beta", "benchA", "benchB"):
        assert token in text


def test_best_per_benchmark_picks_top_agent() -> None:
    runners = [FakeRunner("alpha", "GOLD"), FakeRunner("beta", "WRONG")]
    benches = [FakeBenchmark("benchA", "GOLD")]

    report = run_matrix(runners, benches)

    assert report.best_per_benchmark() == {"benchA": "alpha"}


def test_failing_cell_becomes_error_and_does_not_abort_grid() -> None:
    class BoomRunner:
        id = "boom"

        def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
            raise RuntimeError("kaboom")

    runners: list[Any] = [BoomRunner(), FakeRunner("ok", "GOLD")]
    benches = [FakeBenchmark("benchA", "GOLD")]

    report = run_matrix(runners, benches)
    by_agent = report.by_agent()

    assert by_agent["boom"]["benchA"].status == "error"
    assert by_agent["boom"]["benchA"].budget_honored is False
    assert "kaboom" in by_agent["boom"]["benchA"].budget_note
    # The healthy runner still ran — one bad cell did not abort the sweep.
    assert by_agent["ok"]["benchA"].pass_rate == 1.0
