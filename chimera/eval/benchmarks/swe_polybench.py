"""SWE-PolyBench benchmark implementation.

SWE-PolyBench (Amazon Science) is a multi-language, repository-level
benchmark that evaluates coding agents across Python, Java, JavaScript,
and TypeScript. Tasks include bug fixes, feature additions, and
refactoring with execution-based test verification.

References:
    - HuggingFace: AmazonScience/SWE-PolyBench
    - GitHub: github.com/amazon-science/SWE-PolyBench
    - Paper: arXiv:2504.08703
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark

# Supported languages and the splits exposed by the upstream HF dataset.
SUPPORTED_LANGUAGES = {"python", "java", "javascript", "typescript"}
SUPPORTED_SPLITS = {"full", "pb500", "verified"}

# Language-appropriate test runner hints used by ``evaluate``.
LANGUAGE_TEST_COMMANDS: dict[str, str] = {
    "python": "pytest -x",
    "javascript": "npm test --silent",
    "typescript": "npm test --silent",
    "java": "mvn -q test",
}


@dataclass
class SWEPolyBenchInstance:
    """A single SWE-PolyBench task instance.

    Attributes:
        instance_id: Unique identifier for the task.
        repo: Source repository (e.g. ``"owner/name"``).
        base_commit: Commit SHA the task is rooted at.
        problem_statement: Issue / feature description.
        language: One of ``python``, ``java``, ``javascript``, ``typescript``.
        task_type: ``bug_fix``, ``feature``, or ``refactoring``.
        test_patch: Diff that introduces or modifies the verification tests.
        patch: Gold patch (reference only; not used for grading).
        modified_files: List of files expected to be edited (for
            file-level localization metric).
        cst_nodes: List of CST node identifiers (for node-level retrieval
            metric).
        hints_text: Optional hints text from the upstream dataset.
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    language: str = "python"
    task_type: str = "bug_fix"
    test_patch: str = ""
    patch: str = ""
    modified_files: list[str] = field(default_factory=list)
    cst_nodes: list[str] = field(default_factory=list)
    hints_text: str = ""

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "prompt": self.problem_statement,
            "description": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "language": self.language,
            "task_type": self.task_type,
            "test_patch": self.test_patch,
            "modified_files": list(self.modified_files),
            "cst_nodes": list(self.cst_nodes),
            "hints": self.hints_text,
        }


class SWEPolyBench(Benchmark):
    """SWE-PolyBench: polyglot, execution-based coding agent evaluation.

    Loads instances from a local JSON / JSON-lines file (the upstream
    HuggingFace dataset can be downloaded with the ``datasets`` library
    and dumped to disk). Filters by language and split.

    Args:
        dataset_path: Path to JSON or JSON-lines file with instances.
            If ``None``, the benchmark starts empty and instances may be
            added programmatically via :meth:`add_instance` (useful for
            tests and smoke runs).
        split: One of ``full``, ``pb500``, ``verified``. Used as a label
            and, when present in instance records under ``"split"``, as
            a filter.
        language: Optional filter; one of ``python``, ``java``,
            ``javascript``, ``typescript``.
        limit: Maximum number of tasks to keep after filtering.

    Raises:
        ValueError: If ``split`` or ``language`` is unsupported.
        FileNotFoundError: If ``dataset_path`` is set but missing.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        split: str = "pb500",
        language: str | None = None,
        limit: int | None = None,
    ) -> None:
        if split not in SUPPORTED_SPLITS:
            raise ValueError(
                f"Unsupported split '{split}'. Choose one of {sorted(SUPPORTED_SPLITS)}."
            )
        if language is not None and language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language '{language}'. Choose one of "
                f"{sorted(SUPPORTED_LANGUAGES)}."
            )
        self._dataset_path = dataset_path
        self._split = split
        self._language = language
        self._limit = limit
        self._instances: list[SWEPolyBenchInstance] = []
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
            inst_split = item.get("split")
            if inst_split and inst_split != self._split:
                continue
            language = (item.get("language") or "python").lower()
            if self._language and language != self._language:
                continue
            self._instances.append(
                SWEPolyBenchInstance(
                    instance_id=item.get("instance_id", item.get("id", "")),
                    repo=item.get("repo", ""),
                    base_commit=item.get("base_commit", ""),
                    problem_statement=item.get(
                        "problem_statement",
                        item.get("description", item.get("prompt", "")),
                    ),
                    language=language,
                    task_type=item.get("task_type", "bug_fix"),
                    test_patch=item.get("test_patch", ""),
                    patch=item.get("patch", ""),
                    modified_files=list(item.get("modified_files", []) or []),
                    cst_nodes=list(item.get("cst_nodes", []) or []),
                    hints_text=item.get("hints_text", ""),
                )
            )

        if self._limit:
            self._instances = self._instances[: self._limit]

    def name(self) -> str:
        suffix = f"-{self._language}" if self._language else ""
        return f"swe-polybench-{self._split}{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [inst.to_task() for inst in self._instances]
        return self._cached_tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any = None) -> bool:
        """Run the language-appropriate test suite to grade the patch.

        Behavior:
            - If ``env`` is ``None``, returns False (cannot execute tests).
            - If a ``test_patch`` is present and ``env`` exposes
              ``write_file`` + ``run_command``, applies the patch via
              ``git apply``.
            - If ``env`` exposes ``run_tests``, that is the preferred
              path. Otherwise, runs the language-appropriate command from
              :data:`LANGUAGE_TEST_COMMANDS` via ``env.run_command`` and
              treats exit code ``0`` as a pass.
            - Falls back to a non-empty-output heuristic only as a last
              resort (so unit tests without a Docker env can exercise the
              adapter shape).

        Args:
            task: Task dictionary returned by :meth:`tasks`.
            agent_output: The agent's final textual output.
            env: Execution environment (typically a Docker env per
                language) or ``None``.

        Returns:
            ``True`` if the verification suite passes in ``env``.
        """
        if env is None:
            return False

        test_patch = task.get("test_patch", "")
        if test_patch and hasattr(env, "write_file") and hasattr(env, "run_command"):
            try:
                env.write_file("_test_patch.diff", test_patch)
                result = env.run_command("git apply _test_patch.diff")
                if not getattr(result, "success", False):
                    return False
            except Exception:
                return False

        if hasattr(env, "run_tests"):
            try:
                test_result = env.run_tests()
                return bool(getattr(test_result, "all_passed", False))
            except Exception:
                return False

        if hasattr(env, "run_command"):
            language = (task.get("language") or "python").lower()
            command = LANGUAGE_TEST_COMMANDS.get(language)
            if command:
                try:
                    res = env.run_command(command)
                    return bool(getattr(res, "success", False))
                except Exception:
                    return False

        return bool(agent_output and len(agent_output.strip()) > 10)

    def localization_accuracy(
        self, task: dict[str, Any], predicted_files: list[str]
    ) -> float:
        """File-level localization metric (recall over expected files).

        Args:
            task: Task dictionary returned by :meth:`tasks`.
            predicted_files: Files the agent actually edited.

        Returns:
            Fraction of expected files that the agent touched
            (``0.0`` to ``1.0``). Returns ``0.0`` when no expected
            files are recorded for the task.
        """
        expected = set(task.get("modified_files", []) or [])
        if not expected:
            return 0.0
        predicted = set(predicted_files)
        return len(expected & predicted) / len(expected)

    def cst_node_recall(
        self, task: dict[str, Any], predicted_nodes: list[str]
    ) -> float:
        """CST-node-level retrieval metric (recall over expected nodes).

        Args:
            task: Task dictionary returned by :meth:`tasks`.
            predicted_nodes: CST node identifiers the agent modified.

        Returns:
            Fraction of expected CST nodes covered by the prediction.
        """
        expected = set(task.get("cst_nodes", []) or [])
        if not expected:
            return 0.0
        predicted = set(predicted_nodes)
        return len(expected & predicted) / len(expected)

    @property
    def instances(self) -> list[SWEPolyBenchInstance]:
        return list(self._instances)

    def add_instance(self, instance: SWEPolyBenchInstance) -> None:
        """Add an instance programmatically (useful for tests)."""
        self._instances.append(instance)
        self._cached_tasks = None
