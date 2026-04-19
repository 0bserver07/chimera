from __future__ import annotations

from typing import Any

from chimera.eval.harness import Benchmark


class CustomBenchmark(Benchmark):
    """User-defined benchmark from a directory of task specs or a task list.

    Tasks can be provided either as an in-memory list of dicts or loaded
    from a directory of JSON files (one file per task, sorted by filename).

    Each task JSON should have at minimum an 'id' and 'prompt' field.
    """

    def __init__(
        self,
        tasks_dir: str | None = None,
        tasks_list: list[dict[str, Any]] | None = None,
    ) -> None:
        self._tasks_dir = tasks_dir
        self._tasks_list = tasks_list or []

    def name(self) -> str:
        return "custom"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks_dir:
            return self._load_from_dir()
        return self._tasks_list

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Evaluate by running tests in the environment.

        Falls back to checking if agent output is non-empty when no env.
        """
        if env is None:
            return False
        return bool(env.run_tests().all_passed)

    def _load_from_dir(self) -> list[dict[str, Any]]:
        import json
        from pathlib import Path

        tasks: list[dict[str, Any]] = []
        tasks_path = Path(self._tasks_dir)  # type: ignore[arg-type]
        for f in sorted(tasks_path.glob("*.json")):
            tasks.append(json.loads(f.read_text()))
        return tasks
