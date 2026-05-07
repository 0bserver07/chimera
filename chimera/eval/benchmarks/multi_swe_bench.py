"""MultiSWE-bench: multi-language repository-level benchmark.

MultiSWE-bench generalizes SWE-bench beyond Python — each instance carries a
``language`` field and is graded by the language's native test runner
(pytest / mvn / go test / npm test / cargo test).

This module mirrors :mod:`chimera.eval.benchmarks.swe_bench` in shape so that
the harness can swap them transparently. The key differences:

* Instances expose ``language`` and we keep an in-memory bucket per language
  for filtering and per-language pass-rate breakdowns.
* :meth:`MultiSWEBench.evaluate` dispatches to a
  :class:`~chimera.eval.benchmarks.runners.base.LanguageRunner` instead of
  hard-coding ``pytest``.
* If the language toolchain isn't installed in the execution environment,
  the runner returns ``skipped`` and :meth:`evaluate` falls back to
  ``False`` while exposing the reason on
  :attr:`MultiSWEBench.last_skip_reasons`.

References:
    - GitHub: github.com/multi-swe-bench/multi-swe-bench
    - Paper: arXiv:2504.02605
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.runners import RUNNERS, LanguageRunner, get_runner
from chimera.eval.benchmarks.runners.base import RunnerResult, SkipReason
from chimera.eval.harness import Benchmark

#: Languages supported by the bundled runners.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {"python", "java", "go", "javascript", "typescript", "rust"}
)


def _normalize_language(language: str | None) -> str:
    """Normalize language strings to the canonical lowercase identifier."""
    if not language:
        return "python"
    norm = language.lower().strip()
    aliases = {"js": "javascript", "ts": "typescript", "golang": "go"}
    return aliases.get(norm, norm)


@dataclass
class MultiSWEBenchInstance:
    """A single MultiSWE-bench task instance.

    Attributes:
        instance_id: Unique identifier for the task.
        repo: Source repository (e.g. ``"owner/name"``).
        base_commit: Commit SHA the task is rooted at.
        problem_statement: Issue / feature description.
        language: Language identifier (``python``, ``java``, ``go``,
            ``javascript``, ``typescript``, or ``rust``).
        test_patch: Diff that introduces or modifies verification tests.
        patch: Gold patch (reference only).
        hints_text: Optional hints from the upstream dataset.
        version: Optional version label kept for parity with upstream JSON.
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    language: str = "python"
    test_patch: str = ""
    patch: str = ""
    hints_text: str = ""
    version: str = ""

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "prompt": self.problem_statement,
            "description": self.problem_statement,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "language": self.language,
            "test_patch": self.test_patch,
            "hints": self.hints_text,
            "version": self.version,
        }


@dataclass
class _SkipRecord:
    """Internal record of skipped evaluations for diagnostics."""

    task_id: str
    language: str
    reason: SkipReason


class MultiSWEBench(Benchmark):
    """MultiSWE-bench: polyglot, execution-based agent grading.

    Loads instances from a JSON or JSON-lines file (mirroring
    :class:`~chimera.eval.benchmarks.swe_bench.SWEBench`) and dispatches
    evaluation to the appropriate language runner.

    Args:
        dataset_path: Path to a JSON or JSON-lines file with instances.
            If ``None``, the benchmark starts empty and instances may be
            added programmatically via :meth:`add_instance` (useful for
            tests and smoke runs).
        language: Optional filter restricting the loaded instances to a
            single language.
        limit: Maximum number of tasks to keep after filtering.
        skip_unsupported: When ``True`` (default), instances with
            languages outside :data:`SUPPORTED_LANGUAGES` are silently
            dropped. When ``False``, they are kept but
            :meth:`evaluate` will record them in
            :attr:`last_skip_reasons`.

    Raises:
        ValueError: If ``language`` is set to an unsupported value.
        FileNotFoundError: If ``dataset_path`` is set but missing.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        language: str | None = None,
        limit: int | None = None,
        skip_unsupported: bool = True,
    ) -> None:
        if language is not None:
            normalized = _normalize_language(language)
            if normalized not in SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"Unsupported language '{language}'. Choose one of "
                    f"{sorted(SUPPORTED_LANGUAGES)}."
                )
            self._language: str | None = normalized
        else:
            self._language = None

        self._dataset_path = dataset_path
        self._limit = limit
        self._skip_unsupported = skip_unsupported
        self._instances: list[MultiSWEBenchInstance] = []
        self._cached_tasks: list[dict[str, Any]] | None = None
        self._skip_records: list[_SkipRecord] = []
        if dataset_path:
            self._load(dataset_path)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load(self, path: str) -> None:
        data_path = Path(path)
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")

        text = data_path.read_text()
        try:
            items = json.loads(text)
            if isinstance(items, dict) and "tasks" in items:
                items = items["tasks"]
            if isinstance(items, dict) and "instances" in items:
                items = items["instances"]
            if not isinstance(items, list):
                items = [items]
        except json.JSONDecodeError:
            items = []
            for raw_line in text.strip().splitlines():
                line = raw_line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            language = _normalize_language(item.get("language"))
            if self._skip_unsupported and language not in SUPPORTED_LANGUAGES:
                continue
            if self._language and language != self._language:
                continue
            self._instances.append(
                MultiSWEBenchInstance(
                    instance_id=item.get("instance_id", item.get("id", "")),
                    repo=item.get("repo", ""),
                    base_commit=item.get("base_commit", ""),
                    problem_statement=item.get(
                        "problem_statement",
                        item.get("description", item.get("prompt", "")),
                    ),
                    language=language,
                    test_patch=item.get("test_patch", ""),
                    patch=item.get("patch", ""),
                    hints_text=item.get("hints_text", ""),
                    version=item.get("version", ""),
                )
            )

        if self._limit:
            self._instances = self._instances[: self._limit]

    # ------------------------------------------------------------------
    # Benchmark interface
    # ------------------------------------------------------------------
    def name(self) -> str:
        suffix = f"-{self._language}" if self._language else ""
        return f"multi-swe-bench{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._cached_tasks is None:
            self._cached_tasks = [inst.to_task() for inst in self._instances]
        return self._cached_tasks

    def evaluate(
        self, task: dict[str, Any], agent_output: str, env: Any = None
    ) -> bool:
        """Grade a task by running its language's native test command.

        The flow:

        1. Look up the language runner for ``task["language"]``.
        2. If the runner is missing or the toolchain is not installed, record
           the skip reason and return ``False``.
        3. Otherwise, apply the test patch (if any) and run the test command
           via ``env.run_command``. ``True`` iff the command exits ``0``.

        When ``env`` is ``None`` (e.g. unit tests without a real sandbox),
        the runner short-circuits to a skip with reason
        :attr:`SkipReason.NO_ENV` and ``False`` is returned.

        Args:
            task: Task dictionary returned by :meth:`tasks`.
            agent_output: The agent's final textual output (used only as
                a last-resort fallback).
            env: Execution environment exposing ``write_file`` and
                ``run_command``, or ``None``.

        Returns:
            ``True`` if the language test command passes in ``env``.
        """
        language = _normalize_language(task.get("language"))
        runner = get_runner(language)
        if runner is None:
            self._record_skip(task, language, SkipReason.EXECUTION_ERROR)
            return False

        result = runner.run(env, task.get("test_patch", ""))
        if result.skipped:
            self._record_skip(
                task,
                language,
                result.skip_reason or SkipReason.EXECUTION_ERROR,
            )
            return False
        return result.passed

    # ------------------------------------------------------------------
    # Diagnostics & helpers
    # ------------------------------------------------------------------
    def evaluate_detailed(
        self, task: dict[str, Any], env: Any = None
    ) -> RunnerResult:
        """Like :meth:`evaluate` but returns the full :class:`RunnerResult`.

        Useful for callers that want to surface skip reasons or stderr
        without re-running the command.
        """
        language = _normalize_language(task.get("language"))
        runner = get_runner(language)
        if runner is None:
            return RunnerResult(
                passed=False,
                skipped=True,
                skip_reason=SkipReason.EXECUTION_ERROR,
                stderr=f"no runner registered for language '{language}'",
            )
        return runner.run(env, task.get("test_patch", ""))

    @property
    def instances(self) -> list[MultiSWEBenchInstance]:
        return list(self._instances)

    @property
    def last_skip_reasons(self) -> list[tuple[str, str, str]]:
        """List of ``(task_id, language, reason)`` for every skipped eval.

        Cleared via :meth:`reset_skip_log`.
        """
        return [(r.task_id, r.language, r.reason.value) for r in self._skip_records]

    def language_breakdown(self) -> dict[str, int]:
        """Return a count of loaded instances per language."""
        counts: Counter[str] = Counter()
        for inst in self._instances:
            counts[inst.language] += 1
        return dict(counts)

    def reset_skip_log(self) -> None:
        """Clear :attr:`last_skip_reasons` (useful between harness runs)."""
        self._skip_records = []

    def add_instance(self, instance: MultiSWEBenchInstance) -> None:
        """Add an instance programmatically (useful for tests)."""
        self._instances.append(instance)
        self._cached_tasks = None

    @staticmethod
    def supported_languages() -> list[str]:
        """Return the sorted list of canonical supported languages."""
        return sorted(SUPPORTED_LANGUAGES)

    @staticmethod
    def runner_for(language: str) -> LanguageRunner | None:
        """Look up the registered runner for ``language``."""
        return get_runner(language)

    @staticmethod
    def all_runners() -> dict[str, LanguageRunner]:
        """Return a copy of the runner registry."""
        return dict(RUNNERS)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _record_skip(
        self, task: dict[str, Any], language: str, reason: SkipReason
    ) -> None:
        self._skip_records.append(
            _SkipRecord(
                task_id=str(task.get("id", "")),
                language=language,
                reason=reason,
            )
        )


__all__ = [
    "MultiSWEBench",
    "MultiSWEBenchInstance",
    "SUPPORTED_LANGUAGES",
]
