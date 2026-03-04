"""SWE-bench benchmark implementation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


@dataclass
class SWEBenchInstance:
    """A single SWE-bench task instance."""
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    test_patch: str = ""
    patch: str = ""  # gold patch for reference

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "prompt": self.problem_statement,
            "description": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "hints": self.hints_text,
            "test_patch": self.test_patch,
        }


class SWEBench(Benchmark):
    """SWE-bench benchmark: real GitHub issues with test verification.

    Loads instances from a JSON lines file. Each instance contains a
    repository, base commit, problem statement, and test patch.

    Args:
        dataset_path: Path to JSON lines file with SWE-bench instances.
        limit: Maximum number of tasks to load.
        split: Dataset split to use (e.g., "test", "dev").
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        split: str = "test",
    ) -> None:
        self._dataset_path = dataset_path
        self._limit = limit
        self._split = split
        self._instances: list[SWEBenchInstance] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        """Load instances from JSON lines or JSON array file."""
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        # Try JSON array first, then JSON lines
        try:
            items = json.loads(text)
            # Support nested {"tasks": [...]} format
            if isinstance(items, dict) and "tasks" in items:
                items = items["tasks"]
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            items = []
            for line in text.strip().splitlines():
                line = line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            self._instances.append(SWEBenchInstance(
                instance_id=item.get("instance_id", item.get("id", "")),
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                problem_statement=item.get(
                    "problem_statement",
                    item.get("description", item.get("prompt", "")),
                ),
                hints_text=item.get("hints_text", ""),
                test_patch=item.get("test_patch", ""),
                patch=item.get("patch", ""),
            ))

        if self._limit:
            self._instances = self._instances[:self._limit]

    def name(self) -> str:
        return "swe-bench"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [inst.to_task() for inst in self._instances]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Evaluate whether the agent's output resolves the issue.

        If an environment is provided and the task has a test_patch,
        applies the test patch and runs tests. If the env only supports
        ``run_tests()``, runs tests directly. Otherwise falls back
        to checking if the output contains a non-empty patch.
        """
        if env is None:
            return False

        test_patch = task.get("test_patch", "")

        if test_patch and hasattr(env, "write_file") and hasattr(env, "run_command"):
            try:
                env.write_file("_test_patch.diff", test_patch)
                result = env.run_command("git apply _test_patch.diff")
                if not result.success:
                    return False
            except Exception:
                return False

        # Run tests if env supports it
        if hasattr(env, "run_tests"):
            try:
                test_result = env.run_tests()
                return test_result.all_passed
            except Exception:
                return False

        # Fallback: check if output contains meaningful content
        return bool(agent_output and len(agent_output.strip()) > 10)

    @property
    def instances(self) -> list[SWEBenchInstance]:
        return list(self._instances)

    def add_instance(self, instance: SWEBenchInstance) -> None:
        """Add an instance programmatically (useful for testing)."""
        self._instances.append(instance)
