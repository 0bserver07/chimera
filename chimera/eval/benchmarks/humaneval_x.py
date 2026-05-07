"""HumanEval-X: multi-language HumanEval (Python, Java, C++, Go, JavaScript).

HumanEval-X (Zheng et al., 2023) translates the original 164 HumanEval
problems into 5 programming languages — each problem comes with a
language-specific function signature, prompt, canonical solution, and
hand-written test cases. Unlike :mod:`chimera.eval.benchmarks.humaneval_plus`
(which extends Python coverage with more tests), HumanEval-X tests
*the same* problem across multiple languages.

This is a SCAFFOLD: the dataset loader and per-language test routing are
in place, but the live execution path (compile + run for non-Python
languages) is NotImplemented and tracked as a follow-up. Use the existing
:class:`~chimera.eval.benchmarks.multi_swe_bench.MultiSWEBench` runner
infrastructure once live integration lands.

References:
    - HuggingFace: THUDM/humaneval-x
    - GitHub: github.com/THUDM/CodeGeeX
    - Paper: arXiv:2303.17568
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark

#: Languages exposed by the upstream HumanEval-X dataset.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {"python", "java", "cpp", "go", "javascript"}
)


@dataclass
class HumanEvalXTask:
    """A single HumanEval-X problem instance.

    Attributes:
        task_id: Upstream identifier (e.g. ``"Python/0"``, ``"Java/12"``).
        language: One of :data:`SUPPORTED_LANGUAGES`.
        prompt: Function signature + docstring the agent completes.
        declaration: Just the signature line (no docstring).
        canonical_solution: Reference solution (used only for inspection).
        test: Test harness source for the language.
        example_test: Optional public example test text.
    """

    task_id: str
    language: str
    prompt: str
    declaration: str = ""
    canonical_solution: str = ""
    test: str = ""
    example_test: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "prompt": self.prompt,
            "language": self.language,
            "declaration": self.declaration,
            "canonical_solution": self.canonical_solution,
            "test": self.test,
            "example_test": self.example_test,
        }


class HumanEvalX(Benchmark):
    """HumanEval-X scaffold.

    Loads instances from a local JSON / JSON-lines file (the upstream
    HuggingFace dataset can be dumped via the ``datasets`` library).
    Live execution per language is intentionally stubbed — see
    :meth:`evaluate`.

    Args:
        dataset_path: Path to a JSON or JSON-lines file with HumanEval-X
            instances. If ``None``, the benchmark starts empty.
        language: Optional language filter. One of
            :data:`SUPPORTED_LANGUAGES`.
        limit: Maximum number of tasks to keep after filtering.

    Raises:
        ValueError: If ``language`` is unsupported.
        FileNotFoundError: If ``dataset_path`` is set but missing.
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        language: str | None = None,
        limit: int | None = None,
    ) -> None:
        if language is not None and language.lower() not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported language '{language}'. "
                f"Choose one of {sorted(SUPPORTED_LANGUAGES)}."
            )
        self._language = language.lower() if language else None
        self._dataset_path = dataset_path
        self._limit = limit
        self._tasks: list[HumanEvalXTask] = []
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
            for raw_line in text.strip().splitlines():
                line = raw_line.strip()
                if line:
                    items.append(json.loads(line))

        for item in items:
            language = (item.get("language") or "python").lower()
            if language not in SUPPORTED_LANGUAGES:
                continue
            if self._language and language != self._language:
                continue
            self._tasks.append(
                HumanEvalXTask(
                    task_id=item.get("task_id", item.get("id", "")),
                    language=language,
                    prompt=item.get("prompt", ""),
                    declaration=item.get("declaration", ""),
                    canonical_solution=item.get("canonical_solution", ""),
                    test=item.get("test", ""),
                    example_test=item.get("example_test", ""),
                )
            )

        if self._limit:
            self._tasks = self._tasks[: self._limit]

    def name(self) -> str:
        suffix = f"-{self._language}" if self._language else ""
        return f"humaneval-x{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._tasks]

    def evaluate(
        self, task: dict[str, Any], agent_output: str, env: Any = None
    ) -> bool:
        """Grade a HumanEval-X completion.

        **Status:** scaffold. Only the Python path is wired (mirroring
        :class:`~chimera.eval.benchmarks.human_eval.HumanEval`); the other
        four languages need a per-language compiler + runner. Live
        integration is tracked as a follow-up — for now those branches
        return ``False`` rather than raising so a benchmark sweep degrades
        gracefully.

        Args:
            task: Task dictionary returned by :meth:`tasks`.
            agent_output: Agent's completion (typically the full
                function body or full source file).
            env: Execution environment (unused for the Python in-process
                path; required for compiled languages once wired).

        Returns:
            ``True`` if the agent passes the language's test harness.
        """
        language = (task.get("language") or "python").lower()
        if language == "python":
            return self._evaluate_python_in_process(task, agent_output)
        # Stub: other languages require env-driven compile-and-run.
        # See multi_swe_bench.runners for a parallel pattern.
        return False

    @staticmethod
    def _evaluate_python_in_process(
        task: dict[str, Any], agent_output: str
    ) -> bool:
        """Best-effort Python check: ``exec(prompt + completion + test)``.

        The HumanEval-X Python test harness is expected to be self-driving
        (it must call its own check function). This mirrors the upstream
        evaluation script and the in-process path used by
        :class:`~chimera.eval.benchmarks.human_eval.HumanEval`.
        """
        if not agent_output or not task.get("test"):
            return False
        program = "\n".join(
            [
                task.get("prompt", ""),
                agent_output,
                task.get("test", ""),
            ]
        )
        try:
            exec(program, {})  # noqa: S102 - sandbox is the caller's responsibility
        except Exception:
            return False
        return True

    @property
    def instances(self) -> list[HumanEvalXTask]:
        return list(self._tasks)

    def add_instance(self, task: HumanEvalXTask) -> None:
        """Add a task programmatically (useful for tests)."""
        self._tasks.append(task)

    @staticmethod
    def supported_languages() -> list[str]:
        return sorted(SUPPORTED_LANGUAGES)


__all__ = ["HumanEvalX", "HumanEvalXTask", "SUPPORTED_LANGUAGES"]
