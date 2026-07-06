"""HumanEval+ benchmark adapter (EvalPlus extended test suite).

HumanEval+ is part of the EvalPlus framework
(https://github.com/evalplus/evalplus). It uses the same 164 problem
prompts as the original HumanEval, but augments each problem with
roughly 80x more test cases, exposing brittle solutions that pass the
canonical tests but break on edge cases.

Typical performance drop relative to base HumanEval is 5-29% across
frontier models. Chimera's baseline HumanEval pass@1 is 90.9% (GLM-5),
so a HumanEval+ run is the natural follow-up.

This adapter mirrors :class:`chimera.eval.benchmarks.human_eval.HumanEval`
but pulls problems and tests from EvalPlus when the optional ``evalplus``
package is installed. When ``evalplus`` is not available it transparently
falls back to a local JSONL/JSON dataset path.

Example:
    >>> from chimera.eval.benchmarks.humaneval_plus import HumanEvalPlus
    >>> from chimera.eval.harness import Harness
    >>> bench = HumanEvalPlus(limit=20)
    >>> # harness = Harness(benchmark=bench, agent=my_agent)
    >>> # result = harness.run()

The expected EvalPlus output JSONL format is::

    {"task_id": "HumanEval/0", "solution": "<full function source>"}

which can be evaluated externally via::

    evalplus.evaluate --dataset humaneval --samples samples.jsonl --version plus
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


class HumanEvalPlus(Benchmark):
    """HumanEval+ benchmark adapter.

    Loads the 164 HumanEval problems and runs the EvalPlus extended test
    suite (``base + plus``) against an agent's generated code. Falls back
    to a local dataset file when the ``evalplus`` package is unavailable.

    Attributes:
        dataset_path: Optional path to a local JSON/JSONL dataset.
        limit: Optional maximum number of tasks to load.
        version: ``"plus"`` (extended tests) or ``"base"`` (canonical
            HumanEval tests). Defaults to ``"plus"``.
        use_evalplus_runner: When True (default) and ``evalplus`` is
            installed, evaluation shells out to the official runner for
            authoritative scores. When False, evaluation is in-process.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        limit: int | None = None,
        version: str = "plus",
        use_evalplus_runner: bool = True,
    ) -> None:
        if version not in ("plus", "base"):
            raise ValueError(f"version must be 'plus' or 'base', got {version!r}")
        self._dataset_path = dataset_path
        self._limit = limit
        self._version = version
        self._use_evalplus_runner = use_evalplus_runner
        self._tasks: list[dict[str, Any]] | None = None
        self._evalplus_available: bool | None = None

    def name(self) -> str:
        return f"human-eval-{self._version}"

    def tasks(self) -> list[dict[str, Any]]:
        """Load the 164 HumanEval problems.

        Each task dict contains:
            - ``id`` / ``task_id``: e.g. ``"HumanEval/0"``
            - ``prompt``: function signature + docstring
            - ``entry_point``: function name to be tested
            - ``test``: canonical test code (base tests)
            - ``test_plus``: extended test code (when EvalPlus is loaded)
        """
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Evaluate generated code against extended (or base) tests.

        Strategy:
            1. If EvalPlus runner is enabled and available, write the
               solution to a temporary JSONL file and shell out to
               ``evalplus.evaluate``.
            2. Otherwise, splice the generated code with the in-memory
               test harness (``test_plus`` when version=='plus',
               else ``test``) and execute in-process or via ``env``.

        Args:
            task: Task dict from :meth:`tasks`.
            agent_output: Generated function body / full solution string.
            env: Optional execution environment.

        Returns:
            ``True`` if the solution passes the selected test suite.
        """
        # Normalize markdown-fenced answers to bare source (see _code_extract).
        from chimera.eval.benchmarks._code_extract import extract_code

        agent_output = extract_code(agent_output)

        if self._use_evalplus_runner and self._has_evalplus():
            return self._evaluate_with_evalplus(task, agent_output)

        test_code = task.get("test_plus" if self._version == "plus" else "test", "")
        if not test_code:
            test_code = task.get("test", "")
        if not test_code:
            return False

        full_code = f"{agent_output}\n\n{test_code}"
        if env is not None:
            env.write_file("solution.py", full_code)
            result = env.run_command("python solution.py")
            return bool(result.exit_code == 0)
        try:
            exec(full_code, {})  # noqa: S102
            return True
        except Exception:
            return False

    def to_evalplus_jsonl(
        self,
        solutions: dict[str, str],
        output_path: str | Path,
    ) -> Path:
        """Serialise agent solutions to the EvalPlus JSONL format.

        Args:
            solutions: Mapping of ``task_id`` (e.g. ``"HumanEval/0"``) to
                the full solution source.
            output_path: Destination ``.jsonl`` path.

        Returns:
            The output path as :class:`pathlib.Path`.
        """
        out = Path(output_path)
        with out.open("w", encoding="utf-8") as f:
            for task_id, solution in solutions.items():
                f.write(json.dumps({"task_id": task_id, "solution": solution}) + "\n")
        return out

    def _has_evalplus(self) -> bool:
        if self._evalplus_available is None:
            try:
                import evalplus  # noqa: F401

                self._evalplus_available = True
            except Exception:
                self._evalplus_available = False
        return self._evalplus_available

    def _load_tasks(self) -> list[dict[str, Any]]:
        if self._has_evalplus():
            try:
                from evalplus.data import get_human_eval_plus  # type: ignore

                problems = get_human_eval_plus()
                tasks = [
                    {
                        "id": tid,
                        "task_id": tid,
                        "prompt": p.get("prompt", ""),
                        "entry_point": p.get("entry_point", ""),
                        "canonical_solution": p.get("canonical_solution", ""),
                        "test": p.get("base_input", p.get("test", "")),
                        "test_plus": p.get("plus_input", p.get("test", "")),
                    }
                    for tid, p in problems.items()
                ]
            except Exception:
                tasks = self._load_from_path()
        else:
            tasks = self._load_from_path()

        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _load_from_path(self) -> list[dict[str, Any]]:
        if not self._dataset_path:
            return []
        path = Path(self._dataset_path)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("tasks", [])

    def _evaluate_with_evalplus(
        self, task: dict[str, Any], agent_output: str
    ) -> bool:
        """Shell out to the official ``evalplus.evaluate`` CLI."""
        task_id = task.get("task_id") or task.get("id") or ""
        if not task_id:
            return False
        with tempfile.TemporaryDirectory() as tmp:
            samples = Path(tmp) / "samples.jsonl"
            self.to_evalplus_jsonl({task_id: agent_output}, samples)
            cmd = [
                "evalplus.evaluate",
                "--dataset",
                "humaneval",
                "--samples",
                str(samples),
            ]
            if self._version == "plus":
                cmd.extend(["--version", "plus"])
            try:
                proc = subprocess.run(  # noqa: S603
                    cmd, capture_output=True, text=True, timeout=120
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False
            if proc.returncode != 0:
                return False
            return self._parse_evalplus_result(proc.stdout, task_id)

    @staticmethod
    def _parse_evalplus_result(stdout: str, task_id: str) -> bool:
        """Parse EvalPlus CLI output for a single task pass/fail."""
        for line in stdout.splitlines():
            if task_id in line and "pass" in line.lower():
                return "fail" not in line.lower()
        return "all tests passed" in stdout.lower()
