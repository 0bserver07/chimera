"""Comparative agent benchmarking for A/B testing agent architectures.

Run the same set of problems through multiple agent configurations and
compare their performance side-by-side.  Useful for evaluating prompt
variants, tool sets, loop strategies, or entirely different agent designs
on identical workloads.

Example:
    ```python
    from chimera.eval.comparative import ComparativeEval

    comp = ComparativeEval(provider, problems, env_factory=None)
    comp.add_config("react", lambda p: Agent(p, loop=ReAct()))
    comp.add_config("planact", lambda p: Agent(p, loop=PlanAndExecute()))
    report = comp.run()
    print(report.summary())
    print("Winner:", report.best_config())
    ```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from chimera.providers.base import Provider


@dataclass
class TaskResult:
    """Result of running a single problem through one agent configuration.

    Attributes:
        problem_id: Identifier for the problem.
        output: The agent's final output string.
        cost: Token cost incurred.
        steps: Number of reasoning steps taken.
        passed: Whether the output satisfied the problem's expected criteria.
    """

    problem_id: str
    output: str
    cost: float
    steps: int
    passed: bool


@dataclass
class ComparisonReport:
    """Results comparing multiple agent configurations on the same problems.

    Attributes:
        configs: Names of the configurations that were tested.
        results: Mapping from config name to list of per-problem results.
    """

    configs: list[str]
    results: dict[str, list[TaskResult]]

    def summary(self) -> str:
        """Return a human-readable summary table of pass rates and costs.

        Returns:
            Multi-line string with one row per configuration showing pass
            rate, average cost, and average steps.
        """
        lines: list[str] = []
        lines.append("Comparative Evaluation Summary")
        lines.append("=" * 40)
        for config_name in self.configs:
            task_results = self.results.get(config_name, [])
            total = len(task_results)
            passed = sum(1 for r in task_results if r.passed)
            pass_rate = passed / total if total > 0 else 0.0
            avg_cost = (
                sum(r.cost for r in task_results) / total if total > 0 else 0.0
            )
            avg_steps = (
                sum(r.steps for r in task_results) / total if total > 0 else 0.0
            )
            lines.append(
                f"{config_name}: pass_rate={pass_rate:.1%}, "
                f"avg_cost=${avg_cost:.4f}, avg_steps={avg_steps:.1f}"
            )
        return "\n".join(lines)

    def best_config(self) -> str:
        """Return the name of the configuration with the highest pass rate.

        Ties are broken by lower total cost, then by fewer total steps.

        Returns:
            Name of the best-performing configuration.

        Raises:
            ValueError: If no configurations have been evaluated.
        """
        if not self.configs:
            raise ValueError("No configurations to compare")

        def _score(name: str) -> tuple[float, float, float]:
            task_results = self.results.get(name, [])
            total = len(task_results)
            passed = sum(1 for r in task_results if r.passed)
            pass_rate = passed / total if total > 0 else 0.0
            total_cost = sum(r.cost for r in task_results)
            total_steps = sum(r.steps for r in task_results)
            # Higher pass rate is better, lower cost is better, lower steps is better
            return (pass_rate, -total_cost, -total_steps)

        return max(self.configs, key=_score)

    def by_problem(self) -> dict[str, dict[str, TaskResult]]:
        """Pivot results by problem, showing each config's result per problem.

        Returns:
            Mapping from problem_id to a dict of config_name -> TaskResult.
        """
        pivot: dict[str, dict[str, TaskResult]] = {}
        for config_name in self.configs:
            for task_result in self.results.get(config_name, []):
                pid = task_result.problem_id
                if pid not in pivot:
                    pivot[pid] = {}
                pivot[pid][config_name] = task_result
        return pivot


class ComparativeEval:
    """Run the same task set through different agent configs and compare.

    Each configuration is a callable that takes a
    :class:`~chimera.providers.base.Provider` and returns an agent-like object
    with a ``run(task, env)`` method.

    Attributes:
        provider: The LLM provider shared across configurations.
        problems: List of problem dicts, each with ``"id"``, ``"prompt"``,
            and optionally ``"expected"`` for pass/fail evaluation.
        env_factory: Optional callable returning a fresh environment per task.
    """

    def __init__(
        self,
        provider: Provider,
        problems: list[dict[str, Any]],
        env_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Initialise a comparative evaluation.

        Args:
            provider: LLM provider to pass to agent factories.
            problems: List of problem dicts.  Each must have ``"id"`` and
                ``"prompt"`` keys; an ``"expected"`` key enables automatic
                pass/fail checking.
            env_factory: Optional zero-argument callable producing a fresh
                environment for each problem execution.
        """
        self.provider = provider
        self.problems = problems
        self.env_factory = env_factory
        self._configs: dict[str, Callable[[Provider], Any]] = {}

    def add_config(self, name: str, agent_factory: Callable[[Provider], Any]) -> None:
        """Add a named agent configuration to test.

        Args:
            name: Human-readable identifier for this configuration.
            agent_factory: Callable that receives the provider and returns an
                agent-like object with a ``run(task, env)`` method.
        """
        self._configs[name] = agent_factory

    def run(self) -> ComparisonReport:
        """Run all problems through all configurations and return a comparison.

        For each configuration, creates the agent via its factory, then runs
        every problem.  If a problem dict contains an ``"expected"`` key, the
        output is checked for substring containment; otherwise the task is
        marked as passed.

        Returns:
            A :class:`ComparisonReport` with per-config, per-problem results.
        """
        all_results: dict[str, list[TaskResult]] = {}
        config_names = list(self._configs.keys())

        for config_name, factory in self._configs.items():
            agent = factory(self.provider)
            config_results: list[TaskResult] = []

            for problem in self.problems:
                env = self.env_factory() if self.env_factory else None
                prompt = problem.get("prompt", "")
                problem_id = problem.get("id", "unknown")

                agent_result = agent.run(prompt, env)

                # Determine pass/fail
                expected = problem.get("expected")
                if expected is not None:
                    passed = expected in agent_result.output
                else:
                    passed = True

                config_results.append(
                    TaskResult(
                        problem_id=problem_id,
                        output=agent_result.output,
                        cost=agent_result.cost,
                        steps=agent_result.steps,
                        passed=passed,
                    )
                )

            all_results[config_name] = config_results

        return ComparisonReport(configs=config_names, results=all_results)
