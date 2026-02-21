from __future__ import annotations

from typing import Any

from chimera.eval.harness import Benchmark


class SWEBench(Benchmark):
    """SWE-bench benchmark adapter.

    Loads tasks from a local JSON dataset file. Each task contains a
    problem statement (prompt) and a test patch used for evaluation.

    In production, tasks could also be loaded from HuggingFace datasets.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        split: str = "test",
        limit: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._split = split
        self._limit = limit
        self._tasks: list[dict] | None = None

    def name(self) -> str:
        return "swe-bench"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        """Evaluate by running the task's test patch.

        Requires an environment with run_tests() capability.
        Returns True if all tests pass after the agent's changes.
        """
        if env is None:
            return False
        test_result = env.run_tests()
        return test_result.all_passed

    def _load_tasks(self) -> list[dict]:
        if self._dataset_path:
            import json
            from pathlib import Path

            data = json.loads(Path(self._dataset_path).read_text())
            tasks = data if isinstance(data, list) else data.get("tasks", [])
        else:
            tasks = []  # Would load from HuggingFace in production
        if self._limit:
            tasks = tasks[: self._limit]
        return tasks
