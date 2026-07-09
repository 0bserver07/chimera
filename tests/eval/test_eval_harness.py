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


@dataclass
class FakeAgentResultWithStatus:
    """AgentResult-shaped fake that carries a ``success`` flag."""

    output: str
    cost: float
    steps: int
    success: bool


class ConfigurableAgent:
    """Agent returning a fixed output + success flag for every task."""

    def __init__(self, output: str, success: bool) -> None:
        self._output = output
        self._success = success

    def run(self, task: str, env: Any) -> FakeAgentResultWithStatus:
        return FakeAgentResultWithStatus(
            output=self._output, cost=0.0, steps=1, success=self._success
        )


class AlwaysPassBenchmark(Benchmark):
    """Worst-case lenient grader: every task passes regardless of output.

    Used to prove the harness's measurement-integrity guard, not the grader,
    is what stops an errored/empty run from counting as a pass.
    """

    def __init__(self, task_list: list[dict]) -> None:
        self._tasks = task_list

    def name(self) -> str:
        return "lenient"

    def tasks(self) -> list[dict[str, Any]]:
        return self._tasks

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        return True


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

    # -- measurement integrity: errored runs must not count as passes --------

    def test_errored_empty_run_is_not_graded_as_pass(self):
        """An errored agent run with empty output yields passed=0 even when the
        benchmark evaluator is lenient (belt-and-suspenders integrity guard).
        This is the harness-level fix for the false-positive grid cells."""
        tasks = [{"id": "t1", "prompt": "p1"}, {"id": "t2", "prompt": "p2"}]
        bench = AlwaysPassBenchmark(tasks)
        agent = ConfigurableAgent(output="", success=False)
        result = Harness(bench, agent).run()

        assert result.passed == 0
        assert result.pass_rate == 0.0
        assert all(r.passed is False for r in result.results)

    def test_errored_run_with_nonempty_output_is_still_graded(self):
        """A failed run that still produced a (non-empty) answer is left to the
        grader — the guard only short-circuits empty answers, so env-state
        benchmarks whose answer is intentionally prose are not false-negatived.
        """
        tasks = [{"id": "t1", "prompt": "p1"}]
        bench = AlwaysPassBenchmark(tasks)
        agent = ConfigurableAgent(output="Max steps reached", success=False)
        result = Harness(bench, agent).run()

        assert result.passed == 1

    def test_successful_run_is_graded_normally(self):
        """A completed run is graded by the benchmark exactly as before."""
        tasks = [{"id": "t1", "prompt": "p1"}]
        bench = AlwaysPassBenchmark(tasks)
        agent = ConfigurableAgent(output="anything", success=True)
        result = Harness(bench, agent).run()

        assert result.passed == 1

    def test_missing_success_field_defaults_to_graded(self):
        """Legacy AgentResults without a ``success`` field are treated as
        successful (getattr default True), so their behavior is unchanged —
        even an empty output is still handed to the benchmark evaluator."""
        tasks = [{"id": "t1", "prompt": "p1"}]
        bench = AlwaysPassBenchmark(tasks)
        agent = FakeAgent(default="")  # FakeAgentResult has no `.success`
        result = Harness(bench, agent).run()

        assert result.passed == 1
