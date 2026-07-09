from __future__ import annotations

from typing import Any

from chimera.eval.harness import Benchmark

# Shared with the other code-grading adapters; kept under the old name for
# in-module/test back-compat.
from chimera.eval.benchmarks._code_extract import CODE_FENCE as _CODE_FENCE


def _extract_code(output: str) -> str:
    """Return executable Python from a model response.

    Chat models wrap solutions in Markdown ``` fences surrounded by prose;
    executing that raw text raises ``SyntaxError`` and grades a correct
    solution as 0. Concatenate the fenced block(s) when present, otherwise
    assume the response is already bare source.
    """
    blocks = _CODE_FENCE.findall(output)
    if blocks:
        return "\n\n".join(block.strip("\n") for block in blocks)
    return output


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
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return "human-eval"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
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
            return bool(env.run_tests().all_passed)

        # Combine the (extracted) generated code with the test harness. A raw
        # chat reply usually wraps the solution in Markdown fences with prose,
        # which is not executable Python on its own.
        code = _extract_code(agent_output)
        if not code.strip():
            # An errored or empty agent run leaves no solution to grade.
            # Executing ``"" + test`` can spuriously succeed when the test only
            # *defines* a checker without calling it, so an empty solution must
            # never grade as a pass (measurement integrity).
            return False
        full_code = f"{code}\n\n{test_code}"
        # HumanEval's `test` field only DEFINES ``check(candidate)``; without an
        # explicit call against the entry point no assertion ever runs, which
        # would pass any output. Append the call when the dataset uses that
        # convention.
        entry_point = task.get("entry_point", "")
        if entry_point and "def check" in test_code:
            full_code += f"\n\ncheck({entry_point})\n"

        if env is not None:
            env.write_file("solution.py", full_code)
            result = env.run_command("python solution.py")
            return bool(result.exit_code == 0)

        # In-process execution fallback
        try:
            exec(full_code, {})  # noqa: S102
            return True
        except Exception:
            return False

    def _load_tasks(self) -> list[dict[str, Any]]:
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
