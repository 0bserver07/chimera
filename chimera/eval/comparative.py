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


@dataclass
class CompareReport(ComparisonReport):
    """A :class:`ComparisonReport` plus the controlled-run provenance.

    Attributes:
        budget: The :class:`~chimera.core.budget.BudgetSpec` applied
            uniformly to every (config, problem) run.
        model: Model identifier shared by all configurations.
        task_pool: Benchmark identifier (e.g. ``"harbor:deep-swe?n=10"``).
        seed: Task-sampling seed, recorded for reproducibility.
        budget_hits: Per-config count of runs stopped by the budget —
            kept distinct from task failures.
        budget_reasons: Per-config list of which cap tripped, per hit.
        trajectory_paths: Per-config list of emitted ATIF trajectory
            files (populated when ``run_with_budget`` gets ``atif_dir``).
    """

    budget: Any = None
    model: str = ""
    task_pool: str = ""
    seed: int = 0
    budget_hits: dict[str, int] = field(default_factory=dict)
    budget_reasons: dict[str, list[str]] = field(default_factory=dict)
    trajectory_paths: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> str:
        """Summary table with a distinct budget-hits column per config."""
        lines = [
            "Controlled Comparative Matrix",
            f"model={self.model or '?'}  task_pool={self.task_pool or '?'}  seed={self.seed}",
            "=" * 60,
        ]
        for config_name in self.configs:
            task_results = self.results.get(config_name, [])
            total = len(task_results)
            passed = sum(1 for r in task_results if r.passed)
            pass_rate = passed / total if total > 0 else 0.0
            avg_cost = sum(r.cost for r in task_results) / total if total else 0.0
            avg_steps = sum(r.steps for r in task_results) / total if total else 0.0
            hits = self.budget_hits.get(config_name, 0)
            lines.append(
                f"{config_name}: pass_rate={pass_rate:.1%}, "
                f"avg_cost=${avg_cost:.4f}, avg_steps={avg_steps:.1f}, "
                f"budget_hits={hits}/{total}"
            )
        return "\n".join(lines)


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
        self._configs: dict[str, Callable[..., Any]] = {}

    def add_config(self, name: str, agent_factory: Callable[..., Any]) -> None:
        """Add a named agent configuration to test.

        Args:
            name: Human-readable identifier for this configuration.
            agent_factory: Callable that receives the provider and returns an
                agent-like object with a ``run(task, env)`` method. For
                budgeted runs (:meth:`run_with_budget`) it may instead accept
                ``(provider, loop_config)`` to receive the per-task
                :class:`~chimera.core.loop_config.LoopConfig` carrying the
                budget enforcer and cancellation token.
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

    def run_with_budget(
        self,
        budget: Any,
        model: str = "",
        task_pool: str = "",
        seed: int = 0,
        evaluator: Callable[[dict[str, Any], str, Any], bool] | None = None,
        atif_dir: str | None = None,
    ) -> CompareReport:
        """Run every config under an identical per-task budget.

        For each ``(config, problem)`` pair a fresh
        :class:`~chimera.core.cancellation.CancellationToken` and
        :class:`~chimera.core.budget.BudgetEnforcer` are created, the
        provider is wrapped in a
        :class:`~chimera.core.budget.BudgetedProvider`, and the agent is
        rebuilt via its factory so no budget state leaks across tasks.
        Factories that accept ``(provider, loop_config)`` get full
        tool-call-level enforcement; single-argument factories still get
        provider-level (LLM-call / cost / wall-clock) enforcement.

        Budget hits never raise: a run stopped by the budget is recorded
        as a failed :class:`TaskResult` and counted in
        :attr:`CompareReport.budget_hits` separately from ordinary
        failures.

        Args:
            budget: The :class:`~chimera.core.budget.BudgetSpec` to apply.
            model: Model identifier, recorded in the report.
            task_pool: Benchmark identifier, recorded in the report.
            seed: Task-sampling seed, recorded in the report.
            evaluator: Optional ``(problem, output, env) -> bool`` judge
                (e.g. a benchmark's ``evaluate``). Falls back to the
                ``"expected"`` substring check.
            atif_dir: When set, every (config, problem) run emits an
                ATIF v1.7 trajectory to
                ``<atif_dir>/<config>/<problem_id>.atif.json`` and the
                paths land in :attr:`CompareReport.trajectory_paths`.

        Returns:
            A :class:`CompareReport` over all configs and problems.
        """
        import inspect

        from chimera.core.budget import BudgetedProvider, BudgetEnforcer
        from chimera.core.cancellation import CancellationToken, OperationCancelled
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.presets import AutoApprove

        all_results: dict[str, list[TaskResult]] = {}
        budget_hits: dict[str, int] = {}
        budget_reasons: dict[str, list[str]] = {}
        trajectory_paths: dict[str, list[str]] = {}
        config_names = list(self._configs.keys())

        for config_name, factory in self._configs.items():
            config_results: list[TaskResult] = []
            budget_hits[config_name] = 0
            budget_reasons[config_name] = []
            trajectory_paths[config_name] = []

            for problem in self.problems:
                env = self.env_factory() if self.env_factory else None
                prompt = problem.get("prompt", "")
                problem_id = problem.get("id", "unknown")

                token = CancellationToken()
                enforcer = BudgetEnforcer(budget, cancellation=token)
                emitter = None
                event_bus = None
                if atif_dir is not None:
                    from pathlib import Path

                    from chimera.atif import ATIFEmitter
                    from chimera.events.base import EventBus

                    event_bus = EventBus()
                    emitter = ATIFEmitter(
                        Path(atif_dir) / config_name / f"{problem_id}.atif.json",
                        agent_name=f"chimera-{config_name}",
                        model_name=model or None,
                        session_id=f"{task_pool or 'compare'}::{config_name}::{problem_id}",
                    )
                    emitter.attach(event_bus)
                    emitter.record_user_message(prompt)
                loop_config = LoopConfig(
                    budget_enforcer=enforcer,
                    cancellation=token,
                    permissions=AutoApprove(),
                    event_bus=event_bus,
                )
                provider = BudgetedProvider(self.provider, enforcer)
                try:
                    n_params = len(inspect.signature(factory).parameters)
                except (TypeError, ValueError):
                    n_params = 1
                if n_params >= 2:
                    agent = factory(provider, loop_config)
                else:
                    agent = factory(provider)

                enforcer.start()
                output, cost, steps = "", 0.0, 0
                try:
                    agent_result = agent.run(prompt, env)
                    output = agent_result.output
                    cost = agent_result.cost
                    steps = agent_result.steps
                except OperationCancelled:
                    output = f"[budget exhausted: {enforcer.exhausted_reason}]"
                    cost = enforcer.tally.cost_usd
                    steps = enforcer.tally.tool_calls
                finally:
                    if emitter is not None:
                        trajectory_paths[config_name].append(str(emitter.close()))

                if enforcer.exhausted:
                    budget_hits[config_name] += 1
                    budget_reasons[config_name].append(
                        str(enforcer.exhausted_reason)
                    )
                    passed = False
                elif evaluator is not None:
                    passed = bool(evaluator(problem, output, env))
                else:
                    expected = problem.get("expected")
                    passed = True if expected is None else expected in output

                config_results.append(
                    TaskResult(
                        problem_id=problem_id,
                        output=output,
                        cost=cost,
                        steps=steps,
                        passed=passed,
                    )
                )

            all_results[config_name] = config_results

        return CompareReport(
            configs=config_names,
            results=all_results,
            budget=budget,
            model=model,
            task_pool=task_pool,
            seed=seed,
            budget_hits=budget_hits,
            budget_reasons=budget_reasons,
            trajectory_paths=trajectory_paths,
        )
