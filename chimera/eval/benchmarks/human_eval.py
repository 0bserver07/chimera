from __future__ import annotations

from typing import Any

from chimera.eval.harness import Benchmark


class HumanEval(Benchmark):
    """HumanEval benchmark adapter for code generation tasks.

    Each task contains a function signature, docstring, and test cases.
    The agent generates a function body, which is then tested by executing
    the provided test cases against the generated code.

    Tasks can be loaded from a local JSON file.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit
        self._tasks: list[dict] | None = None

    def name(self) -> str:
        return "human-eval"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        """Evaluate by executing test cases against the generated code.

        The task should contain a 'test' field with test code. The agent_output
        is combined with the test code and executed. If no env is available,
        we attempt in-process evaluation.
        """
        test_code = task.get("test", "")
        if not test_code:
            # Fall back to env-based testing
            if env is None:
                return False
            return env.run_tests().all_passed

        # Combine generated code with test harness
        full_code = f"{agent_output}\n\n{test_code}"
        if env is not None:
            env.write_file("solution.py", full_code)
            result = env.run_command("python solution.py")
            return result.exit_code == 0

        # In-process execution fallback
        try:
            exec(full_code, {})  # noqa: S102
            return True
        except Exception:
            return False

    def _load_tasks(self) -> list[dict]:
        if self._dataset_path:
            import json
            from pathlib import Path

            data = json.loads(Path(self._dataset_path).read_text())
            tasks = data if isinstance(data, list) else data.get("tasks", [])
        else:
            tasks = []
        if self._limit:
            tasks = tasks[: self._limit]
        return tasks
