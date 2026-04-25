"""Cline Bench benchmark adapter.

Cline Bench evaluates coding agents on real-world engineering tasks derived from
actual Cline user sessions. Tasks are containerized RL environments built from
real repo snapshots with ground-truth tests based on the code that shipped.

Source: https://github.com/cline/cline-bench
Website: https://cline.bot/blog/cline-bench-initiative
License: Open source

Evaluation is binary (test suite passes or fails). Each task includes a Docker
image / repo snapshot, task instructions, and a test script.

Example:
    >>> bench = ClineBench(dataset_dir="path/to/cline-bench/tasks", limit=5)
    >>> tasks = bench.tasks()
    >>> # Run agent against each task in a docker env, then:
    >>> ok = bench.evaluate(tasks[0], agent_output="...", env=docker_env)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


@dataclass
class ClineBenchTask:
    """A single Cline Bench task instance.

    Attributes:
        task_id: Unique task identifier (derived from directory name when absent).
        instructions: Natural-language task prompt given to the agent.
        repo_snapshot: Path or URL to the repo snapshot the task starts from.
        docker_image: Container image used for the RL environment, when provided.
        test_command: Shell command that runs the test suite (binary pass/fail).
        setup_commands: Commands to run before the agent starts (env bootstrap).
        metadata: Free-form task metadata (domain, difficulty, source session).
    """

    task_id: str
    instructions: str
    repo_snapshot: str = ""
    docker_image: str = ""
    test_command: str = ""
    setup_commands: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "prompt": self.instructions,
            "description": self.instructions,
            "repo_snapshot": self.repo_snapshot,
            "docker_image": self.docker_image,
            "test_command": self.test_command,
            "setup_commands": list(self.setup_commands),
            "metadata": dict(self.metadata),
        }


class ClineBench(Benchmark):
    """Cline Bench: real-world repo-based development evaluation.

    Loads task definitions from a directory of task specs (one JSON per task)
    or a single JSON-lines / JSON-array file. Each task entry should describe
    the repo snapshot, instructions, and test command.

    Args:
        dataset_dir: Directory containing per-task JSON files (``*.json``) or
            ``task.json`` files in subdirectories.
        dataset_path: Alternative single-file dataset (JSON or JSONL).
        limit: Maximum number of tasks to load.
    """

    def __init__(
        self,
        dataset_dir: str | None = None,
        dataset_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        self._dataset_dir = dataset_dir
        self._dataset_path = dataset_path
        self._limit = limit
        self._tasks: list[ClineBenchTask] = []
        self._cached_tasks: list[dict[str, Any]] | None = None

        if dataset_dir:
            self._load_dir(dataset_dir)
        elif dataset_path:
            self._load_file(dataset_path)

    # ------------------------------------------------------------------ loading
    def _load_dir(self, path: str) -> None:
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"Cline Bench dataset directory not found: {path}")

        candidates: list[Path] = []
        # Per-task subdirectory style: <root>/<task_id>/task.json
        candidates.extend(sorted(root.glob("*/task.json")))
        # Flat style: <root>/<task_id>.json
        candidates.extend(sorted(p for p in root.glob("*.json") if p.is_file()))

        for spec_file in candidates:
            try:
                item = json.loads(spec_file.read_text())
            except json.JSONDecodeError:
                continue
            self._tasks.append(self._parse_item(item, default_id=spec_file.parent.name
                                                if spec_file.name == "task.json"
                                                else spec_file.stem))

        if self._limit:
            self._tasks = self._tasks[: self._limit]

    def _load_file(self, path: str) -> None:
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Cline Bench dataset not found: {path}")

        text = data_path.read_text()
        items: list[Any]
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "tasks" in parsed:
                items = parsed["tasks"]
            elif isinstance(parsed, list):
                items = parsed
            else:
                items = [parsed]
        except json.JSONDecodeError:
            items = []
            for line in text.strip().splitlines():
                line = line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            self._tasks.append(self._parse_item(item))

        if self._limit:
            self._tasks = self._tasks[: self._limit]

    def _parse_item(self, item: dict[str, Any], default_id: str = "") -> ClineBenchTask:
        return ClineBenchTask(
            task_id=item.get("task_id") or item.get("id") or default_id,
            instructions=item.get("instructions")
            or item.get("prompt")
            or item.get("description", ""),
            repo_snapshot=item.get("repo_snapshot") or item.get("repo", ""),
            docker_image=item.get("docker_image") or item.get("image", ""),
            test_command=item.get("test_command") or item.get("test", ""),
            setup_commands=list(item.get("setup_commands") or item.get("setup") or []),
            metadata=dict(item.get("metadata") or {}),
        )

    # ------------------------------------------------------------------ Benchmark API
    def name(self) -> str:
        return "cline-bench"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [t.to_task() for t in self._tasks]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Run the task's test command in the provided environment.

        Cline Bench is binary: tests pass or they don't. We delegate to the env's
        ``run_command`` (preferred) or ``run_tests`` hook. Without an env, we
        cannot verify a real run, so we return False rather than guess.
        """
        if env is None:
            return False

        test_command = task.get("test_command", "")

        if test_command and hasattr(env, "run_command"):
            try:
                result = env.run_command(test_command)
            except Exception:
                return False
            success = getattr(result, "success", None)
            if success is not None:
                return bool(success)
            returncode = getattr(result, "returncode", None)
            if returncode is not None:
                return returncode == 0
            return False

        if hasattr(env, "run_tests"):
            try:
                test_result = env.run_tests()
                return bool(getattr(test_result, "all_passed", False))
            except Exception:
                return False

        return False

    # ------------------------------------------------------------------ helpers
    @property
    def instances(self) -> list[ClineBenchTask]:
        return list(self._tasks)

    def add_task(self, task: ClineBenchTask) -> None:
        """Add a task programmatically (useful for tests and smoke runs)."""
        self._tasks.append(task)
        self._cached_tasks = None
