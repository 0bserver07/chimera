"""Context-Bench (Letta) benchmark adapter.

Context-Bench by Letta evaluates an agent's ability to maintain, reuse, and
reason over long-running context across multi-step workflows. Tasks are
generated programmatically from a database of fictional entities (people,
pets, addresses, medical records); SQL queries are converted to natural
language questions and the agent must navigate semi-structured text files
using ``grep``-like and ``open``-like tools to answer them.

Two suites:
    * ``filesystem`` — file ops, entity relationship tracing, multi-step
      retrieval (default).
    * ``skills`` — discovering and loading relevant skills from a library.

This adapter is a scaffold. The Letta Evals framework
(https://github.com/letta-ai/letta-leaderboard) is loaded lazily via
:meth:`_load_tasks`; if the optional dependency is unavailable the adapter
degrades to a user-supplied JSON dataset (same shape as the upstream task
records) so the harness remains usable in offline / CI environments.

Reference scores (Letta leaderboard): Claude Sonnet 4.5 74.0%, GPT-5 72.7%,
GLM-4.6 56.8%, Kimi K2 55.1%. Chimera target: 50%+ baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


class ContextBench(Benchmark):
    """Letta Context-Bench adapter for long-running context evaluation.

    Each task contains a natural-language question, a ground-truth answer
    derived from a SQL query, and a pointer to (or inline copy of) the
    semi-structured filesystem context the agent must search.

    Attributes:
        suite: ``"filesystem"`` or ``"skills"``.
        dataset_size: Number of questions to generate / load (upstream
            default 100).
        timeout: Per-task wall-clock limit in seconds (passed through to
            the agent runner; informational here).
        repeat: Number of repetitions per question for stochastic models
            (averaged downstream).
        dataset_path: Optional path to a pre-generated JSON dataset. When
            provided, no upstream import is attempted.
    """

    def __init__(
        self,
        suite: str = "filesystem",
        dataset_size: int = 100,
        timeout: int = 100,
        repeat: int = 1,
        dataset_path: str | None = None,
        benchmark_variable: str = "core_memory_read_benchmark",
    ) -> None:
        if suite not in ("filesystem", "skills"):
            raise ValueError(
                f"suite must be 'filesystem' or 'skills', got {suite!r}"
            )
        self.suite = suite
        self.dataset_size = dataset_size
        self.timeout = timeout
        self.repeat = repeat
        self.dataset_path = dataset_path
        self.benchmark_variable = benchmark_variable
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return f"context-bench-{self.suite}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(
        self, task: dict[str, Any], agent_output: str, env: Any
    ) -> bool:
        """Compare agent answer against ground truth.

        Upstream Letta uses an LLM judge for free-form answers. This
        scaffold supports two modes:

        * ``exact`` (default): case-insensitive substring match between
          ``agent_output`` and ``task["answer"]``.
        * ``judge``: when ``task["judge"]`` is callable, defer to it.

        Args:
            task: Task dict containing at least ``"answer"``.
            agent_output: Final string produced by the agent.
            env: Unused (in-process filesystem suite); reserved for future
                sandbox integration.

        Returns:
            ``True`` when the agent's answer matches the ground truth.
        """
        del env  # unused; filesystem suite runs in-process
        judge = task.get("judge")
        if callable(judge):
            return bool(judge(task, agent_output))
        truth = task.get("answer", "")
        if not truth:
            return False
        return str(truth).strip().lower() in agent_output.strip().lower()

    def _load_tasks(self) -> list[dict[str, Any]]:
        """Load tasks from a local JSON file or the Letta Evals framework.

        Returns:
            List of task dicts each with ``id``, ``prompt``, ``answer``,
            and optional ``context_dir`` keys.
        """
        if self.dataset_path:
            data = json.loads(Path(self.dataset_path).read_text())
            tasks = data if isinstance(data, list) else data.get("tasks", [])
            return tasks[: self.dataset_size]

        # Lazy upstream import; degrade gracefully if not installed.
        try:
            from leaderboard import letta_bench  # type: ignore[import-not-found]
        except ImportError:
            return []

        generator = getattr(letta_bench, self.benchmark_variable, None)
        if generator is None:
            return []
        raw = generator(dataset_size=self.dataset_size)
        return [
            {
                "id": item.get("id", f"context-bench-{i}"),
                "prompt": item["question"],
                "answer": item["answer"],
                "context_dir": item.get("context_dir"),
            }
            for i, item in enumerate(raw)
        ]
