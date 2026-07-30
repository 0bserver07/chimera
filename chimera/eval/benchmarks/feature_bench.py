"""FeatureBench benchmark adapter.

FeatureBench evaluates agentic coding on end-to-end *feature development*
in real-world Python repositories. Tasks span multiple commits/PRs and are
judged via the repository's test suite (test-driven evaluation protocol).

Dataset: https://huggingface.co/datasets/LiberCoders/FeatureBench
GitHub:  https://github.com/LiberCoders/FeatureBench
Paper:   arXiv:2602.10975 (ICLR 2026)

Splits:
- ``lite``: 30 tasks (26 lv1 + 4 lv2)
- ``full``: 200 tasks across 24 Python repos

Task levels:
- ``lv1``: agent receives masked code with interface signatures
- ``lv2``: agent receives only test files; must implement interface +
  functionality

This adapter mirrors :class:`chimera.eval.benchmarks.swe_bench.SWEBench`:
a problem loader (HuggingFace dataset, JSON, or JSONL), a task driver via
:meth:`tasks`, and a Docker-aware grader via :meth:`evaluate` that runs the
target test files inside the FeatureBench-provided container.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


@dataclass
class FeatureBenchTask:
    """A single FeatureBench task instance.

    Attributes:
        task_id: Unique task identifier (e.g. ``"sympy__sympy-12345-lv1"``).
        repo: ``owner/name`` of the source repository.
        base_commit: Commit SHA pinned for the task.
        level: Task level (``"lv1"`` or ``"lv2"``).
        prompt: Natural-language feature description shown to the agent.
        test_files: Test files (relative paths) that must pass.
        masked_files: For lv1, files that contain interface signatures with
            implementations stubbed/masked.
        docker_image: FeatureBench-prebuilt Docker image for this task.
        metadata: Any additional fields preserved from the source row.
    """

    task_id: str
    repo: str
    base_commit: str
    level: str = "lv1"
    prompt: str = ""
    test_files: list[str] = field(default_factory=list)
    masked_files: list[str] = field(default_factory=list)
    docker_image: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "prompt": self.prompt,
            "description": self.prompt,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "level": self.level,
            "test_files": list(self.test_files),
            "masked_files": list(self.masked_files),
            "docker_image": self.docker_image,
            "metadata": dict(self.metadata),
        }


class FeatureBench(Benchmark):
    """FeatureBench: end-to-end feature development evaluation.

    Loads tasks from one of three sources (in priority order):

    1. ``dataset_path`` — a local JSON, JSON-array, or JSONL file (handy
       for offline reproductions and unit tests).
    2. The HuggingFace ``datasets`` library, when installed, via
       ``load_dataset("LiberCoders/FeatureBench", split=split)``.
    3. Programmatic injection via :meth:`add_task` (used by tests).

    The ``evaluate`` method is Docker-aware: when the supplied environment
    exposes ``run_command`` and the task carries a ``docker_image``, tests
    are run inside the container. If the env only exposes ``run_tests``,
    that is used directly. Otherwise the grader falls back to a
    unresolved verdict — nothing ran, so nothing is verified.

    Args:
        dataset_path: Optional path to a local JSON/JSONL dump.
        split: Which FeatureBench split to load (``"lite"`` or ``"full"``).
        limit: Maximum number of tasks to load.
        level_filter: Optional level filter (``"lv1"`` or ``"lv2"``).
    """

    DATASET_NAME = "LiberCoders/FeatureBench"

    def __init__(
        self,
        dataset_path: str | None = None,
        split: str = "lite",
        limit: int | None = None,
        level_filter: str | None = None,
    ) -> None:
        self._dataset_path = dataset_path
        self._split = split
        self._limit = limit
        self._level_filter = level_filter
        self._tasks: list[FeatureBenchTask] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        if dataset_path:
            self._load_local(dataset_path)

    # ------------------------------------------------------------------ loaders

    def _load_local(self, path: str) -> None:
        """Load tasks from a local JSON, JSON-array, or JSONL file."""
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        items: list[dict[str, Any]]
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "tasks" in parsed:
                items = list(parsed["tasks"])
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

        for row in items:
            self._tasks.append(self._row_to_task(row))

        self._apply_filters()

    def load_from_hub(self) -> None:
        """Load tasks from the HuggingFace hub.

        Requires the optional ``datasets`` package. Raises ``ImportError``
        with a helpful hint when missing.
        """
        try:
            from datasets import load_dataset  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - network/optional dep
            raise ImportError(
                "FeatureBench.load_from_hub() requires the `datasets` "
                "package. Install with: pip install datasets"
            ) from exc

        ds = load_dataset(self.DATASET_NAME, split=self._split)
        for row in ds:
            self._tasks.append(self._row_to_task(dict(row)))
        self._cached_tasks = None
        self._apply_filters()

    @staticmethod
    def _row_to_task(row: dict[str, Any]) -> FeatureBenchTask:
        """Map a raw dataset row to a :class:`FeatureBenchTask`."""
        return FeatureBenchTask(
            task_id=row.get("task_id", row.get("instance_id", row.get("id", ""))),
            repo=row.get("repo", row.get("repository", "")),
            base_commit=row.get("base_commit", row.get("commit", "")),
            level=row.get("level", row.get("task_level", "lv1")),
            prompt=row.get(
                "prompt",
                row.get("description", row.get("problem_statement", "")),
            ),
            test_files=list(row.get("test_files", row.get("tests", []) or [])),
            masked_files=list(row.get("masked_files", []) or []),
            docker_image=row.get("docker_image", row.get("image", "")),
            metadata={
                k: v
                for k, v in row.items()
                if k
                not in {
                    "task_id",
                    "instance_id",
                    "id",
                    "repo",
                    "repository",
                    "base_commit",
                    "commit",
                    "level",
                    "task_level",
                    "prompt",
                    "description",
                    "problem_statement",
                    "test_files",
                    "tests",
                    "masked_files",
                    "docker_image",
                    "image",
                }
            },
        )

    def _apply_filters(self) -> None:
        if self._level_filter:
            self._tasks = [t for t in self._tasks if t.level == self._level_filter]
        if self._limit is not None:
            self._tasks = self._tasks[: self._limit]
        self._cached_tasks = None

    # ------------------------------------------------------------- benchmark API

    def name(self) -> str:
        return f"feature-bench-{self._split}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [t.to_task() for t in self._tasks]
        return self._cached_tasks

    def evaluate(
        self,
        task: dict[str, Any],
        agent_output: str,
        env: Any = None,
    ) -> bool:
        """Run the task's test files and return True iff all pass.

        Resolution order:

        1. If ``env`` exposes ``run_tests`` and the task lists ``test_files``,
           pass them through and report the aggregate result.
        2. Else if ``env`` exposes ``run_command``, invoke ``pytest`` against
           the listed test files (inside the container if the env wraps one).
        3. Else grade as unresolved (this used to be a non-empty-output
           heuristic, which scored prose as a solved task) so smoke tests
           against a stub env still produce a deterministic answer.
        """
        if env is None:
            return False

        test_files = task.get("test_files") or []

        if hasattr(env, "run_tests"):
            try:
                if test_files:
                    result = env.run_tests(test_files)
                else:
                    result = env.run_tests()
                return bool(getattr(result, "all_passed", False))
            except TypeError:
                # env.run_tests() may not accept positional args
                try:
                    result = env.run_tests()
                    return bool(getattr(result, "all_passed", False))
                except Exception:
                    return False
            except Exception:
                return False

        if hasattr(env, "run_command") and test_files:
            cmd = "python -m pytest -x " + " ".join(test_files)
            try:
                result = env.run_command(cmd)
                return bool(getattr(result, "success", False))
            except Exception:
                return False

        # MEASUREMENT INTEGRITY: inability to grade is not a pass. This was
        # `len(agent_output.strip()) > 10`, which graded a sentence of prose as a
        # solved task — the same defect class as a sandbox degrading to local:
        # the result becomes indistinguishable from a real solve. A column of
        # these reads as a uniform zero, which `scripts/render_observatory.py`
        # already refuses to publish as a score. Pinned by
        # tests/eval/test_no_length_grading.py.
        return False

    # ----------------------------------------------------------------- helpers

    @property
    def loaded_tasks(self) -> list[FeatureBenchTask]:
        return list(self._tasks)

    def add_task(self, task: FeatureBenchTask) -> None:
        """Inject a task programmatically (used by tests)."""
        self._tasks.append(task)
        self._cached_tasks = None
