from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    """Base class for benchmarks."""

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def tasks(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool: ...


class Harness:
    """Runs an agent against a benchmark suite."""

    def __init__(
        self,
        benchmark: Benchmark,
        agent: Any,
        env_factory: Any = None,
    ) -> None:
        self.benchmark = benchmark
        self.agent = agent
        self.env_factory = env_factory

    def run(self) -> EvalResult:
        results: list[TaskEvalResult] = []
        for task in self.benchmark.tasks():
            # Create fresh env per task if factory provided
            env = self.env_factory() if self.env_factory else None
            if env:
                env.setup()
            agent_result = self.agent.run(task.get("prompt", ""), env)
            passed = self.benchmark.evaluate(task, agent_result.output, env)
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
