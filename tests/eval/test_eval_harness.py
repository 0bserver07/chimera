from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chimera.eval.harness import Benchmark, Harness, TaskEvalResult


# -- Helpers ------------------------------------------------------------------


class SimpleBenchmark(Benchmark):
    """Benchmark that returns canned tasks and always passes if output contains 'ok'."""

    def __init__(self, task_list: list[dict]):
        self._tasks = task_list

    def name(self) -> str:
        return "simple"

    def tasks(self) -> list[dict[str, Any]]:
        return self._tasks

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        return "ok" in agent_output.lower()


@dataclass
class FakeAgentResult:
    output: str
    cost: float
    steps: int


class FakeAgent:
    """Agent that echoes the prompt with 'ok' or 'fail' based on config."""

    def __init__(self, responses: dict[str, str] | None = None, default: str = "ok"):
        self._responses = responses or {}
        self._default = default
        self._cost = 0.01
        self._steps = 3

    def run(self, task: str, env: Any) -> FakeAgentResult:
        output = self._responses.get(task, self._default)
        return FakeAgentResult(output=output, cost=self._cost, steps=self._steps)


class FakeEnv:
    def __init__(self):
        self.setup_called = False
        self.cleanup_called = False

    def setup(self):
        self.setup_called = True

    def cleanup(self):
        self.cleanup_called = True


# -- Tests --------------------------------------------------------------------


class TestEvalHarness:
    def test_runs_all_tasks(self):
        tasks = [
            {"id": "t1", "prompt": "do A"},
            {"id": "t2", "prompt": "do B"},
            {"id": "t3", "prompt": "do C"},
        ]
        bench = SimpleBenchmark(tasks)
        agent = FakeAgent(default="ok")
        harness = Harness(bench, agent)
        result = harness.run()

        assert result.total == 3
        assert len(result.results) == 3
        assert [r.task_id for r in result.results] == ["t1", "t2", "t3"]

    def test_computes_pass_rate(self):
        tasks = [
            {"id": "t1", "prompt": "p1"},
            {"id": "t2", "prompt": "p2"},
            {"id": "t3", "prompt": "p3"},
            {"id": "t4", "prompt": "p4"},
        ]
        bench = SimpleBenchmark(tasks)
        # Two pass, two fail
        agent = FakeAgent(
            responses={"p1": "ok", "p2": "fail", "p3": "ok", "p4": "fail"}
        )
        harness = Harness(bench, agent)
        result = harness.run()

        assert result.passed == 2
        assert result.total == 4
        assert result.pass_rate == 0.5

    def test_handles_empty_benchmark(self):
        bench = SimpleBenchmark([])
        agent = FakeAgent()
        harness = Harness(bench, agent)
        result = harness.run()

        assert result.total == 0
        assert result.passed == 0
        assert result.pass_rate == 0.0
        assert result.results == []
        assert result.total_cost == 0.0

    def test_tracks_cost(self):
        tasks = [
            {"id": "t1", "prompt": "a"},
            {"id": "t2", "prompt": "b"},
        ]
        bench = SimpleBenchmark(tasks)
        agent = FakeAgent(default="ok")
        agent._cost = 0.05
        harness = Harness(bench, agent)
        result = harness.run()

        assert result.total_cost == 0.10
        assert result.results[0].cost == 0.05
        assert result.results[1].cost == 0.05

    def test_task_eval_result_fields(self):
        r = TaskEvalResult(
            task_id="abc",
            passed=True,
            output="some output",
            cost=0.02,
            steps=5,
        )
        assert r.task_id == "abc"
        assert r.passed is True
        assert r.output == "some output"
        assert r.cost == 0.02
        assert r.steps == 5

    def test_env_factory_creates_fresh_envs(self):
        """When env_factory is provided, setup/cleanup are called per task."""
        envs: list[FakeEnv] = []

        def factory():
            e = FakeEnv()
            envs.append(e)
            return e

        tasks = [{"id": "t1", "prompt": "a"}, {"id": "t2", "prompt": "b"}]
        bench = SimpleBenchmark(tasks)
        agent = FakeAgent(default="ok")
        harness = Harness(bench, agent, env_factory=factory)
        harness.run()

        assert len(envs) == 2
        assert all(e.setup_called for e in envs)
        assert all(e.cleanup_called for e in envs)
