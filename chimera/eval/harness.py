"""Evaluation harness for running agents against benchmark suites.

Provides the :class:`Benchmark` abstract base class that benchmark authors
implement, and the :class:`Harness` runner that executes an agent on every
task in a benchmark and aggregates the results into an :class:`EvalResult`.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import IO, Any


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
        progress_path: Optional path to a JSONL sidecar file that records
            each task's outcome as soon as it is graded.
        resume: When ``True`` and *progress_path* already exists, previously
            recorded tasks are skipped rather than re-run.
    """

    def __init__(
        self,
        benchmark: Benchmark,
        agent: Any,
        env_factory: Any = None,
        graders: list[Any] | None = None,
        progress_path: str | None = None,
        resume: bool = False,
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
            progress_path: Optional path to a JSON Lines sidecar file. When
                set, one line per completed task is appended and flushed as
                soon as the task is graded, so a killed or timed-out run
                preserves the tasks that already finished. When ``None`` no
                file I/O is performed and behaviour is unchanged.
            resume: When ``True`` and *progress_path* already exists, the
                task ids it records are loaded up front; those tasks are
                skipped (the agent is not invoked for them) and their cached
                pass/fail outcomes are folded into the final aggregate. When
                ``False``, any existing *progress_path* is truncated so the
                run starts fresh. Ignored when *progress_path* is ``None``.
        """
        self.benchmark = benchmark
        self.agent = agent
        self.env_factory = env_factory
        self.graders = graders or []
        self.progress_path = progress_path
        self.resume = resume

    def run(self) -> EvalResult:
        """Execute the full benchmark and return aggregated results.

        Iterates over every task in the benchmark, optionally creating a
        fresh environment per task via *env_factory*, runs the agent, and
        evaluates the output.

        If the benchmark exposes a ``prepare_agent(agent)`` method
        (e.g. :class:`~chimera.eval.benchmarks.swe_bench_verified.SWEBenchVerified`),
        it is invoked once before the run so the benchmark can wire its
        recommended tools (IPython REPL) and loop config (LLM
        condensation, max-step budget) onto the agent. Idempotent and
        opt-in: benchmarks without this hook see unchanged behaviour.

        Returns:
            An :class:`EvalResult` with per-task outcomes, the overall pass
            rate, and the total cost.
        """
        prepare_agent = getattr(self.benchmark, "prepare_agent", None)
        if callable(prepare_agent):
            prepare_agent(self.agent)

        # When resuming, pre-load the outcomes already recorded on disk so we
        # can skip re-running those tasks and still count them in the total.
        cached: dict[str, TaskEvalResult] = {}
        if self.progress_path is not None and self.resume:
            cached = self._load_progress(self.progress_path)

        progress: IO[str] | None = None
        if self.progress_path is not None:
            # Append when resuming (keep the recorded lines); truncate
            # otherwise so a non-resumed run always starts fresh.
            mode = "a" if self.resume else "w"
            progress = open(self.progress_path, mode, encoding="utf-8")

        results: list[TaskEvalResult] = []
        try:
            for index, task in enumerate(self.benchmark.tasks()):
                task_id = str(task.get("id") or task.get("task_id") or index)

                # Skip tasks already recorded in a resumed run without
                # touching the agent, but keep their outcome in the totals.
                if task_id in cached:
                    results.append(cached[task_id])
                    continue

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
                task_result = TaskEvalResult(
                    task_id=task_id,
                    passed=passed,
                    output=agent_result.output,
                    cost=agent_result.cost,
                    steps=agent_result.steps,
                )
                results.append(task_result)

                # Persist immediately so a mid-run kill preserves this task.
                if progress is not None:
                    progress.write(
                        json.dumps(
                            {
                                "task_id": task_result.task_id,
                                "passed": task_result.passed,
                                "output": task_result.output,
                                "cost": task_result.cost,
                                "steps": task_result.steps,
                            }
                        )
                        + "\n"
                    )
                    progress.flush()
        finally:
            if progress is not None:
                progress.close()

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

    @staticmethod
    def _load_progress(progress_path: str) -> dict[str, TaskEvalResult]:
        """Load previously recorded task outcomes from a JSONL sidecar.

        Args:
            progress_path: Path to the JSON Lines progress file. A missing
                file is treated as an empty record.

        Returns:
            Mapping of ``task_id`` to its recorded :class:`TaskEvalResult`.
            Blank or malformed lines are skipped so a partially written final
            line (e.g. from a hard kill) does not abort the resume.
        """
        cached: dict[str, TaskEvalResult] = {}
        if not os.path.exists(progress_path):
            return cached
        with open(progress_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # Ignore a truncated trailing line.
                task_id = str(record.get("task_id"))
                cached[task_id] = TaskEvalResult(
                    task_id=task_id,
                    passed=bool(record.get("passed", False)),
                    output=str(record.get("output", "")),
                    cost=float(record.get("cost", 0.0)),
                    steps=int(record.get("steps", 0)),
                )
        return cached
