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

import ast
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.swe_bench import (
    DEFAULT_PYTEST_CMD,
    DEFAULT_TEST_CHUNK_SIZE,
    _chunk_test_ids,
)
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


def _as_str_list(value: Any) -> list[str]:
    """Normalize a list-valued field to a list of strings.

    The upstream HF dataset stores ``modified_nodes`` (and similar columns) as
    a *JSON-encoded string* (e.g. ``'["a", "b"]'``); other dumps hand over a
    native list. Both — plus the empty/absent case — resolve here.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return [text]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_task_type(value: Any) -> str:
    """Canonicalize an upstream task label to snake_case (``"Bug Fix"`` ->
    ``"bug_fix"``). Falls back to ``"bug_fix"`` for blank/missing values."""
    text = str(value or "").strip().lower().replace(" ", "_")
    return text or "bug_fix"


def _coerce_sequence(value: Any) -> list[Any]:
    """Coerce a possibly-encoded list field to a native Python list.

    Unlike SWE-bench (JSON strings) and unlike this module's own
    ``modified_nodes`` column (a *double-quoted* JSON string), SWE-PolyBench
    stores its ``F2P`` / ``P2P`` columns as **Python-repr strings** — single
    quoted, e.g. ``"['a', 'b']"`` — which :func:`json.loads` rejects. This
    tries JSON first (for dumps that re-encoded the column), then
    :func:`ast.literal_eval` (the native upstream encoding, restricted to
    literals so it never executes code), and finally treats the whole string
    as a single element.

    Args:
        value: A native list/tuple, a JSON or Python-repr string, ``None``,
            or a bare scalar.

    Returns:
        A list (empty when *value* is ``None`` / blank).
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except (ValueError, SyntaxError):
                continue
            return list(parsed) if isinstance(parsed, (list, tuple)) else [parsed]
        return [text]
    return [value]


def _polybench_node_id(raw: Any) -> str:
    """Convert a PolyBench ``file:class:test`` identifier to a pytest node id.

    SWE-PolyBench encodes each Python test as ``<file>:<class-or-None>:<test>``
    (a literal ``None`` in the middle segment marks a module-level test).
    Pytest node ids use ``::`` separators and omit the class when absent:

    * ``pkg/test_x.py:None:test_a`` -> ``pkg/test_x.py::test_a``
    * ``pkg/test_x.py:Cls:test_a``  -> ``pkg/test_x.py::Cls::test_a``
    * ``pkg/test_x.py:test_a``      -> ``pkg/test_x.py::test_a`` (2-segment form)

    Already-converted ids (those already containing ``::``) and colon-free
    strings pass through unchanged, so the conversion is idempotent — safe to
    re-run in :meth:`SWEPolyBench.evaluate` on ids surfaced by
    :meth:`SWEPolyBenchInstance.to_task`.

    Args:
        raw: One raw PolyBench test identifier.

    Returns:
        The pytest node id (empty string when *raw* is blank).
    """
    text = str(raw).strip()
    if not text or "::" in text or ":" not in text:
        return text
    parts = text.split(":")
    file_part = parts[0]
    if len(parts) == 2:
        return f"{file_part}::{parts[1]}"
    class_part = parts[1]
    test_part = ":".join(parts[2:])
    if class_part and class_part != "None":
        return f"{file_part}::{class_part}::{test_part}"
    return f"{file_part}::{test_part}"


def _polybench_test_list(value: Any) -> list[str]:
    """Parse a PolyBench ``F2P`` / ``P2P`` column into pytest node ids.

    Combines :func:`_coerce_sequence` (decode the Python-repr list) with
    :func:`_polybench_node_id` (colon form -> pytest node id), dropping blanks.
    """
    ids = [_polybench_node_id(item) for item in _coerce_sequence(value)]
    return [tid for tid in ids if tid]


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
        fail_to_pass: Pytest node ids that must pass once the fix is applied,
            parsed from the ``F2P`` column. Populated for Python instances
            only (the PolyBench colon form converts to a real pytest node id);
            empty for Java / JavaScript / TypeScript, whose test ids are in the
            language's own convention and grade via the blanket command.
        pass_to_pass: Pytest node ids that must still pass after the fix (the
            regression guard), parsed from the ``P2P`` column. Python only, as
            for :attr:`fail_to_pass`.
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
            "task_type": self.task_type,
            "test_patch": self.test_patch,
            "modified_files": list(self.modified_files),
            "cst_nodes": list(self.cst_nodes),
            "hints": self.hints_text,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
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
        pytest_cmd: Base command used to run a Python instance's named
            ``F2P`` / ``P2P`` tests during faithful grading. Defaults to
            :data:`~chimera.eval.benchmarks.swe_bench.DEFAULT_PYTEST_CMD`
            (``python -m pytest``); the node ids are appended, shell-quoted.
        test_chunk_size: Maximum test node ids per pytest invocation; long
            ``P2P`` lists are chunked to respect the OS argument-length limit.

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
        pytest_cmd: str = DEFAULT_PYTEST_CMD,
        test_chunk_size: int = DEFAULT_TEST_CHUNK_SIZE,
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
        self._pytest_cmd = pytest_cmd
        self._test_chunk_size = test_chunk_size
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
            # Faithful FAIL_TO_PASS / PASS_TO_PASS grading only applies where the
            # named tests are genuine pytest node ids — i.e. Python. The upstream
            # ``F2P`` / ``P2P`` columns for Java / JS / TS use those languages'
            # own test-id conventions, so surfacing them as pytest ids would fake
            # ids that cannot run; those instances keep the blanket-command path.
            is_python = language == "python"
            fail_to_pass = (
                _polybench_test_list(item.get("F2P", item.get("fail_to_pass")))
                if is_python
                else []
            )
            pass_to_pass = (
                _polybench_test_list(item.get("P2P", item.get("pass_to_pass")))
                if is_python
                else []
            )
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
                    # Upstream HF column is ``task_category`` ("Bug Fix" etc.);
                    # older JSON dumps used ``task_type``.
                    task_type=_normalize_task_type(
                        item.get("task_category", item.get("task_type"))
                    ),
                    test_patch=item.get("test_patch", ""),
                    patch=item.get("patch", ""),
                    modified_files=_as_str_list(item.get("modified_files")),
                    # Upstream HF column is ``modified_nodes`` (a JSON string);
                    # older dumps used ``cst_nodes``.
                    cst_nodes=_as_str_list(
                        item.get("modified_nodes", item.get("cst_nodes"))
                    ),
                    hints_text=item.get("hints_text", ""),
                    fail_to_pass=fail_to_pass,
                    pass_to_pass=pass_to_pass,
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
            - **Faithful path** — for a Python task carrying
              ``fail_to_pass`` / ``pass_to_pass`` node ids (from the ``F2P`` /
              ``P2P`` columns) when ``env`` can run commands: run exactly those
              tests and pass iff every one passes (see
              :meth:`_grade_named_tests`). This fires *before* the blanket
              paths, so a Python instance with named tests is graded to the
              official FAIL_TO_PASS / PASS_TO_PASS contract rather than a
              whole-suite run.
            - If ``env`` exposes ``run_tests``, that is the preferred
              blanket path. Otherwise, runs the language-appropriate command
              from :data:`LANGUAGE_TEST_COMMANDS` via ``env.run_command`` and
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

        # Faithful FAIL_TO_PASS / PASS_TO_PASS grading (Python node ids only).
        # ``_polybench_test_list`` is idempotent, so it accepts both the node
        # ids surfaced by ``to_task`` and a raw ``F2P`` / ``P2P`` column on an
        # unprocessed row.
        language = (task.get("language") or "python").lower()
        if language == "python" and hasattr(env, "run_command"):
            fail_to_pass = _polybench_test_list(
                task.get("fail_to_pass", task.get("F2P"))
            )
            pass_to_pass = _polybench_test_list(
                task.get("pass_to_pass", task.get("P2P"))
            )
            if fail_to_pass or pass_to_pass:
                return self._grade_named_tests(env, fail_to_pass, pass_to_pass)

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

    def _grade_named_tests(
        self,
        env: Any,
        fail_to_pass: list[str],
        pass_to_pass: list[str],
    ) -> bool:
        """Run a Python instance's FAIL_TO_PASS / PASS_TO_PASS tests explicitly.

        Both lists are run (chunked to respect ``ARG_MAX``) via
        ``env.run_command``. Because grading happens *after* the fix +
        ``test_patch`` are applied, the resolve criterion collapses to: every
        named test must pass now. A pytest run's exit code is authoritative
        (``0`` iff every selected test passed and none errored), so any
        non-zero chunk — a FAIL_TO_PASS still failing, a PASS_TO_PASS
        regressing, or a collection error — fails the instance.

        Args:
            env: The environment to run commands in.
            fail_to_pass: Node ids that must pass once the fix is applied.
            pass_to_pass: Node ids that must still pass (regression guard).

        Returns:
            ``True`` iff every named test in both lists passes.
        """
        for group in (fail_to_pass, pass_to_pass):
            for chunk in _chunk_test_ids(group, self._test_chunk_size):
                command = self._pytest_command(chunk)
                try:
                    result = env.run_command(command)
                except Exception:
                    return False
                if not getattr(result, "success", False):
                    return False
        return True

    def _pytest_command(self, test_ids: list[str]) -> str:
        """Build a ``python -m pytest <ids...>`` command for *test_ids*.

        Each node id is shell-quoted so parametrized ids (``test[a-b]``) and
        other shell metacharacters reach pytest literally.
        """
        quoted = " ".join(shlex.quote(t) for t in test_ids)
        return f"{self._pytest_cmd} {quoted}"

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
