"""Evaluation harness for running agents against benchmark suites.

Provides the :class:`Benchmark` abstract base class that benchmark authors
implement, and the :class:`Harness` runner that executes an agent on every
task in a benchmark and aggregates the results into an :class:`EvalResult`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class TaskEvalResult:
    """Result of evaluating a single task."""

    task_id: str
    passed: bool
    output: str
    cost: float
    steps: int


@dataclass
class EvalResult:
    """Aggregated result of running a full benchmark."""

    benchmark: str
    total: int
    passed: int
    pass_rate: float
    results: list[TaskEvalResult]
    total_cost: float


class Benchmark(ABC):
    """Abstract base class for evaluation benchmarks.

    Implement :meth:`name`, :meth:`tasks`, and :meth:`evaluate` to define a
    new benchmark suite that can be run by a :class:`Harness`.
    """

    @abstractmethod
    def name(self) -> str:
        """Return the human-readable benchmark name.

        Returns:
            A short identifier string (e.g. ``"HumanEval"``).
        """
        ...

    @abstractmethod
    def tasks(self) -> list[dict[str, Any]]:
        """Return the list of tasks in this benchmark.

        Each task is a dict that must contain at least a ``"prompt"`` key
        and should include an ``"id"`` key for result tracking.

        Returns:
            List of task dictionaries.
        """
        ...

    @abstractmethod
    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Judge whether the agent's output passes a task.

        Args:
            task: The original task dictionary from :meth:`tasks`.
            agent_output: The agent's final output string.
            env: The execution environment used for this task (may be
                ``None``).

        Returns:
            ``True`` if the agent's output satisfies the task requirements.
        """
        ...


class Harness:
    """Runs an agent against a benchmark suite and aggregates results.

    Attributes:
        benchmark: The benchmark to evaluate against.
        agent: The agent under test.
        env_factory: Optional callable that returns a fresh
            :class:`~chimera.env.base.Environment` per task.
    """

    def __init__(
        self,
        benchmark: Benchmark,
        agent: Any,
        env_factory: Any = None,
        graders: list[Any] | None = None,
    ) -> None:
        """Initialise the harness.

        Args:
            benchmark: Benchmark providing the task list and evaluator.
            agent: The agent to evaluate.
            env_factory: Optional zero-argument callable that produces a
                fresh :class:`~chimera.env.base.Environment` for each task.
                When ``None``, tasks run without an environment.
            graders: Optional list of :class:`~chimera.eval.graders.base.Grader`
                instances.  When provided, graders are run *after* the
                benchmark's own ``evaluate()`` and a task only passes if
                **both** the benchmark evaluator and all graders pass.
        """
        self.benchmark = benchmark
        self.agent = agent
        self.env_factory = env_factory
        self.graders = graders or []

    def run(self) -> EvalResult:
        """Execute the full benchmark and return aggregated results.

        Iterates over every task in the benchmark, optionally creating a
        fresh environment per task via *env_factory*, runs the agent, and
        evaluates the output.

        Returns:
            An :class:`EvalResult` with per-task outcomes, the overall pass
            rate, and the total cost.
        """
        results: list[TaskEvalResult] = []
        for task in self.benchmark.tasks():
            # Create fresh env per task if factory provided
            env = self.env_factory() if self.env_factory else None
            if env:
                env.setup()
            agent_result = self.agent.run(task.get("prompt", ""), env)
            passed = self.benchmark.evaluate(task, agent_result.output, env)
            # Run additional graders if configured
            if passed and self.graders:
                for grader in self.graders:
                    try:
                        grade = grader.grade(task, {"output": agent_result.output})
                        if not grade.passed:
                            passed = False
                            break
                    except Exception:
                        pass  # Grader failure doesn't block
            if env:
                env.cleanup()
            results.append(
                TaskEvalResult(
                    task_id=task.get("id", "unknown"),
                    passed=passed,
                    output=agent_result.output,
                    cost=agent_result.cost,
                    steps=agent_result.steps,
                )
            )
        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        return EvalResult(
            benchmark=self.benchmark.name(),
            total=total,
            passed=passed_count,
            pass_rate=passed_count / total if total > 0 else 0.0,
            results=results,
            total_cost=sum(r.cost for r in results),
        )
