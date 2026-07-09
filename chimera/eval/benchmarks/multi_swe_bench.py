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
import shlex
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.runners import RUNNERS, LanguageRunner, get_runner
from chimera.eval.benchmarks.runners.base import RunnerResult, SkipReason
from chimera.eval.benchmarks.swe_bench import (
    DEFAULT_PYTEST_CMD,
    DEFAULT_TEST_CHUNK_SIZE,
    _as_test_list,
    _chunk_test_ids,
)
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


def _named_test_list(value: Any) -> list[str]:
    """Normalize a MultiSWE-bench FAIL_TO_PASS / PASS_TO_PASS field to node ids.

    Upstream MultiSWE-bench keeps the resolved-test information under a
    *mapping* of test name to its execution record (``f2p_tests`` /
    ``p2p_tests``); other dumps use the SWE-bench JSON-string-list encoding. A
    mapping resolves to its keys (the test node ids); everything else defers to
    :func:`~chimera.eval.benchmarks.swe_bench._as_test_list` (JSON string,
    native list, bare id, or empty).

    Args:
        value: The raw field value.

    Returns:
        A list of test node ids (empty when *value* is absent / blank).
    """
    if isinstance(value, dict):
        return [str(k) for k in value if str(k).strip()]
    return _as_test_list(value)


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
        fail_to_pass: Pytest node ids that must pass once the fix is applied.
            Populated for Python instances only (their ids are genuine pytest
            node ids); empty for Java / Go / JS / TS / Rust, which grade via
            the language runner. Absent from the currently staged Python subset
            (the staging transform drops the upstream ``*_tests`` records), so
            this is empty there too and grading falls back to the runner.
        pass_to_pass: Pytest node ids that must still pass after the fix (the
            regression guard). Python only, as for :attr:`fail_to_pass`.
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
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)

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
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
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
            # Faithful FAIL_TO_PASS / PASS_TO_PASS grading applies only where the
            # named tests are genuine pytest node ids — i.e. Python. Other
            # languages' resolved tests use their own runner conventions and
            # grade via the language runner, so we do not surface them here (that
            # would fake ids pytest cannot run). Accept the SWE-bench column
            # names and the upstream ``f2p_tests`` / ``p2p_tests`` mappings.
            is_python = language == "python"
            fail_to_pass = (
                _named_test_list(
                    item.get(
                        "FAIL_TO_PASS",
                        item.get("fail_to_pass", item.get("f2p_tests")),
                    )
                )
                if is_python
                else []
            )
            pass_to_pass = (
                _named_test_list(
                    item.get(
                        "PASS_TO_PASS",
                        item.get("pass_to_pass", item.get("p2p_tests")),
                    )
                )
                if is_python
                else []
            )
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
                    fail_to_pass=fail_to_pass,
                    pass_to_pass=pass_to_pass,
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
        """Grade a task, preferring faithful FAIL_TO_PASS / PASS_TO_PASS tests.

        The flow:

        1. **Faithful path** — when a Python task carries ``fail_to_pass`` /
           ``pass_to_pass`` node ids and ``env`` can run commands, run exactly
           those tests (see :meth:`_grade_named_tests`): pass iff every one
           passes after the fix + ``test_patch`` are applied. This is the
           official resolve contract and takes precedence over the blanket run.
        2. **Runner path** (default / back-compat) — look up the language
           runner for ``task["language"]``. If the runner is missing or the
           toolchain is not installed, record the skip reason and return
           ``False``. Otherwise apply the test patch (if any) and run the
           blanket test command via ``env.run_command``; ``True`` iff it exits
           ``0``. The staged Python subset carries no named tests (the staging
           transform drops the upstream ``*_tests`` records), so it takes this
           path today.

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
            ``True`` if the graded tests pass in ``env``.
        """
        language = _normalize_language(task.get("language"))

        # Faithful named-test grading for Python instances that carry the lists.
        fail_to_pass = _named_test_list(
            task.get("fail_to_pass", task.get("FAIL_TO_PASS"))
        )
        pass_to_pass = _named_test_list(
            task.get("pass_to_pass", task.get("PASS_TO_PASS"))
        )
        if (
            language == "python"
            and (fail_to_pass or pass_to_pass)
            and env is not None
            and hasattr(env, "run_command")
        ):
            return self._grade_named_tests(
                task, language, env, fail_to_pass, pass_to_pass
            )

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

    def _grade_named_tests(
        self,
        task: dict[str, Any],
        language: str,
        env: Any,
        fail_to_pass: list[str],
        pass_to_pass: list[str],
    ) -> bool:
        """Run a Python instance's FAIL_TO_PASS / PASS_TO_PASS tests explicitly.

        Reuses the Python :class:`LanguageRunner` for the two preconditions the
        blanket path already enforces — toolchain availability and test-patch
        application — recording the same skip reasons for parity, then runs the
        named node ids (chunked to respect ``ARG_MAX``) instead of the blanket
        ``pytest`` command. The instance resolves iff every FAIL_TO_PASS and
        every PASS_TO_PASS test passes; a pytest chunk's non-zero exit fails it.

        Args:
            task: Task dict (for skip-record diagnostics).
            language: Canonical language (``"python"``).
            env: Execution environment exposing ``run_command``.
            fail_to_pass: Node ids that must pass once the fix is applied.
            pass_to_pass: Node ids that must still pass (regression guard).

        Returns:
            ``True`` iff every named test passes.
        """
        runner = get_runner(language)
        if runner is not None and not runner.is_toolchain_available(env):
            self._record_skip(task, language, SkipReason.TOOLCHAIN_MISSING)
            return False

        test_patch = task.get("test_patch", "")
        if (
            test_patch
            and runner is not None
            and not runner.apply_test_patch(env, test_patch)
        ):
            self._record_skip(task, language, SkipReason.PATCH_FAILED)
            return False

        for group in (fail_to_pass, pass_to_pass):
            for chunk in _chunk_test_ids(group, DEFAULT_TEST_CHUNK_SIZE):
                quoted = " ".join(shlex.quote(t) for t in chunk)
                command = f"{DEFAULT_PYTEST_CMD} {quoted}"
                try:
                    result = env.run_command(command)
                except Exception:
                    self._record_skip(task, language, SkipReason.EXECUTION_ERROR)
                    return False
                success = getattr(result, "success", None)
                exit_code = getattr(result, "exit_code", None)
                passed = bool(success) if success is not None else (exit_code == 0)
                if not passed:
                    return False
        return True

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
