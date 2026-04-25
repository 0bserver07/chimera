"""DPAI Arena benchmark adapter.

JetBrains Developer Productivity AI Arena (DPAI Arena) is a multi-track,
multi-language benchmarking platform for AI coding agents. The initial
release focuses on enterprise-grade Java/Spring workloads with 140+ tasks
derived from real GitHub issues across 15 open-source Spring projects.

Tracks:
    - issue-to-patch  (bug fix / feature request)
    - pr-review       (code review of pull requests)
    - coverage        (test generation)
    - static-analysis (find/fix lint and analyzer findings)
    - upgrade         (dependency / framework upgrades)
    - compliance      (license/policy compliance)

Reference:
    https://dpaia.dev/
    https://blog.jetbrains.com/blog/2025/10/28/introducing-developer-productivity-ai-arena-an-open-platform-for-ai-coding-agents-benchmarks/
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


SUPPORTED_TRACKS = (
    "issue-to-patch",
    "pr-review",
    "coverage",
    "static-analysis",
    "upgrade",
    "compliance",
)


@dataclass
class DPAITask:
    """A single DPAI Arena task instance.

    Captures the union of fields used across tracks. Track-specific payloads
    (e.g. ``test_patch`` for ``issue-to-patch``, ``pr_diff`` for ``pr-review``)
    are kept optional so a single dataclass covers all six tracks.
    """

    instance_id: str
    track: str
    repo: str
    base_commit: str
    language: str = "java"
    framework: str = ""
    problem_statement: str = ""
    hints_text: str = ""
    test_patch: str = ""
    pr_diff: str = ""
    target_files: list[str] = field(default_factory=list)
    build_tool: str = "maven"  # maven | gradle
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "track": self.track,
            "prompt": self.problem_statement,
            "description": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "language": self.language,
            "framework": self.framework,
            "hints": self.hints_text,
            "test_patch": self.test_patch,
            "pr_diff": self.pr_diff,
            "target_files": list(self.target_files),
            "build_tool": self.build_tool,
            "metadata": dict(self.metadata),
        }


class DPAIArena(Benchmark):
    """DPAI Arena benchmark: multi-track, multi-language SDLC workflows.

    The adapter is dataset-driven: pass a JSONL or JSON-array file containing
    task instances. The ``track`` parameter selects which evaluation routine
    is used by :meth:`evaluate`.

    Args:
        dataset_path: Path to JSONL / JSON array file with DPAI tasks.
        track: Which track to load and evaluate. Tasks whose ``track`` field
            does not match are filtered out at load time. Use ``"all"`` to
            keep every task and dispatch per-instance.
        limit: Maximum number of tasks to load.
        language: Optional language filter (e.g. ``"java"``).
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        track: str = "issue-to-patch",
        limit: int | None = None,
        language: str | None = None,
    ) -> None:
        if track != "all" and track not in SUPPORTED_TRACKS:
            raise ValueError(
                f"Unknown track {track!r}. Supported: {SUPPORTED_TRACKS} or 'all'."
            )
        self._dataset_path = dataset_path
        self._track = track
        self._limit = limit
        self._language = language
        self._instances: list[DPAITask] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        if dataset_path:
            self._load(dataset_path)

    def _load(self, path: str) -> None:
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        try:
            items = json.loads(text)
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
            task_track = item.get("track", "issue-to-patch")
            if self._track != "all" and task_track != self._track:
                continue
            if self._language and item.get("language", "java") != self._language:
                continue
            self._instances.append(DPAITask(
                instance_id=item.get("instance_id", item.get("id", "")),
                track=task_track,
                repo=item.get("repo", ""),
                base_commit=item.get("base_commit", ""),
                language=item.get("language", "java"),
                framework=item.get("framework", ""),
                problem_statement=item.get(
                    "problem_statement",
                    item.get("description", item.get("prompt", "")),
                ),
                hints_text=item.get("hints_text", ""),
                test_patch=item.get("test_patch", ""),
                pr_diff=item.get("pr_diff", ""),
                target_files=list(item.get("target_files", [])),
                build_tool=item.get("build_tool", "maven"),
                metadata=dict(item.get("metadata", {})),
            ))

        if self._limit:
            self._instances = self._instances[:self._limit]

    def name(self) -> str:
        return f"dpai-arena[{self._track}]"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [inst.to_task() for inst in self._instances]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Dispatch evaluation by track.

        Each track has a different success signal:

        - issue-to-patch / upgrade: apply test patch, run build/tests
        - coverage: run tests with coverage, check delta
        - pr-review / static-analysis / compliance: rubric scoring (TODO)

        When ``env`` is ``None`` or missing required hooks, falls back to a
        non-empty-output check so unit tests can exercise the dispatch logic.
        """
        if env is None:
            return False

        track = task.get("track", self._track)
        if track in ("issue-to-patch", "upgrade"):
            return self._evaluate_patch(task, agent_output, env)
        if track == "coverage":
            return self._evaluate_coverage(task, agent_output, env)
        if track in ("pr-review", "static-analysis", "compliance"):
            return self._evaluate_rubric(task, agent_output, env)
        return bool(agent_output and len(agent_output.strip()) > 10)

    def _evaluate_patch(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Apply the gold test patch and run the project's tests."""
        test_patch = task.get("test_patch", "")
        if test_patch and hasattr(env, "write_file") and hasattr(env, "run_command"):
            try:
                env.write_file("_dpai_test_patch.diff", test_patch)
                result = env.run_command("git apply _dpai_test_patch.diff")
                if not result.success:
                    return False
            except Exception:
                return False

        if hasattr(env, "run_tests"):
            try:
                test_result = env.run_tests()
                return bool(test_result.all_passed)
            except Exception:
                return False
        return bool(agent_output and len(agent_output.strip()) > 10)

    def _evaluate_coverage(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Coverage track: run tests with coverage instrumentation.

        TODO: parse coverage report and compare against baseline. For now
        this returns whether the test suite still passes after the agent's
        changes.
        """
        if hasattr(env, "run_tests"):
            try:
                test_result = env.run_tests()
                return bool(test_result.all_passed)
            except Exception:
                return False
        return False

    def _evaluate_rubric(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Rubric-style tracks (PR review, static analysis, compliance).

        TODO: wire up an LLM judge or DPAI Arena's official scoring tool.
        Placeholder returns ``True`` only when the agent produced substantive
        output, matching the SWE-bench fallback behaviour.
        """
        return bool(agent_output and len(agent_output.strip()) > 20)

    @property
    def track(self) -> str:
        return self._track

    @property
    def instances(self) -> list[DPAITask]:
        return list(self._instances)

    def add_instance(self, instance: DPAITask) -> None:
        """Add an instance programmatically (useful for testing)."""
        self._instances.append(instance)
        self._cached_tasks = None
