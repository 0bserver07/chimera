"""MBPP (Mostly Basic Python Problems) benchmark adapter.

Issue: #94. MBPP is a 974-problem code-generation benchmark of crowd-sourced
entry-level Python tasks. Each problem ships a natural-language prompt, a
canonical solution, and a ``test_list`` of 3 ``assert``-style cases. A
hand-verified ``sanitized`` subset of 427 problems is the recommended
evaluation split.

This adapter follows the same shape as :class:`chimera.eval.benchmarks.human_eval.HumanEval`:
the dataset is loaded from a local JSON/JSONL file (the harness is
zero-dependency core, so HuggingFace ``datasets`` is intentionally NOT
imported here). Tests can be executed in-process or against an
``Environment`` via ``run_command``.

Dataset format (one record per problem)::

    {
        "task_id": 1,
        "text": "Write a function to find the minimum cost path...",
        "code": "def min_cost(...): ...",
        "test_list": [
            "assert min_cost(...) == 8",
            "assert min_cost(...) == 12",
            "assert min_cost(...) == 16",
        ],
        "test_setup_code": "",
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


class MBPP(Benchmark):
    """MBPP benchmark adapter for basic Python code generation.

    Each task contains a natural-language description and a list of
    ``assert`` test cases. The agent generates a Python function which is
    then executed against the assertions. A task passes only when *all*
    assertions in ``test_list`` pass.

    Args:
        dataset_path: Path to a JSON or JSONL file containing MBPP records.
            When ``None``, ``tasks()`` returns an empty list (useful for
            unit tests and dry-run wiring checks).
        split: Logical split name surfaced via ``name()`` (e.g.
            ``"sanitized"``, ``"test"``). Does not filter records on its own.
        limit: Optional cap on the number of tasks returned.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        split: str = "sanitized",
        limit: int | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._split = split
        self._limit = limit
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return f"mbpp-{self._split}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Execute ``test_list`` assertions against the agent's output.

        Args:
            task: MBPP record with ``test_list`` (list of assert strings)
                and optional ``test_setup_code``.
            agent_output: The candidate function source produced by the
                agent. May include surrounding prose; we treat it as
                executable Python and let parse errors register as a fail.
            env: Optional execution environment. When provided, the
                combined source is written to ``solution.py`` and run via
                ``run_command``. When ``None``, falls back to in-process
                ``exec`` in a fresh namespace.

        Returns:
            ``True`` if every assertion in ``test_list`` passes.
        """
        test_list = task.get("test_list") or []
        if not test_list:
            return False

        setup = task.get("test_setup_code") or ""
        assertions = "\n".join(test_list)
        full_code = (
            f"{setup}\n{agent_output}\n{assertions}\n"
            if setup
            else f"{agent_output}\n{assertions}\n"
        )

        if env is not None:
            env.write_file("solution.py", full_code)
            result = env.run_command("python solution.py")
            return bool(result.exit_code == 0)

        try:
            exec(full_code, {})  # noqa: S102
            return True
        except Exception:
            return False

    def _load_tasks(self) -> list[dict[str, Any]]:
        if not self._dataset_path:
            return []
        text = Path(self._dataset_path).read_text()
        records: list[dict[str, Any]] = []
        # Accept either a JSON array, a top-level {"tasks": [...]} envelope,
        # or JSONL (one record per line).
        stripped = text.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            data = json.loads(text)
            records = data if isinstance(data, list) else data.get("tasks", [])
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))

        normalized = [self._normalize(r) for r in records]
        if self._limit:
            normalized = normalized[: self._limit]
        return normalized

    @staticmethod
    def _normalize(record: dict[str, Any]) -> dict[str, Any]:
        """Normalize an MBPP record to the harness task shape.

        The harness expects ``id`` and ``prompt`` keys. MBPP records use
        ``task_id`` and ``text``; we copy across without mutating the
        original so ``test_list`` and ``code`` remain accessible.
        """
        task_id = record.get("task_id", record.get("id", "unknown"))
        prompt = record.get("text") or record.get("prompt", "")
        out = dict(record)
        out.setdefault("id", f"Mbpp/{task_id}")
        out.setdefault("prompt", prompt)
        return out
