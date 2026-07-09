# chimera/eval/benchmarks/aimo.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


SYSTEM_PROMPT = """\
You are a mathematical problem solver. Given an olympiad-level math problem:

1. Reason step-by-step about the approach
2. Write Python code to compute the answer (you may use sympy, numpy, scipy, itertools)
3. Execute the code using the bash tool
4. Verify your answer if possible using the verify_answer tool
5. State your final answer as a single integer on the last line

Your final answer MUST be a non-negative integer. State it clearly as: ANSWER: <number>"""


def extract_answer(text: str) -> int | None:
    """Extract the answer integer from agent output.

    Looks for (in priority order):
    1. "ANSWER: <number>" pattern
    2. \\boxed{<number>} LaTeX pattern
    3. Last integer in the text
    """
    # Try ANSWER: pattern
    match = re.search(r"ANSWER:\s*(-?\d+)", text)
    if match:
        return abs(int(match.group(1)))

    # Try \\boxed{} pattern
    match = re.search(r"\\boxed\{(-?\d+)\}", text)
    if match:
        return abs(int(match.group(1)))

    # Fall back to last integer in text
    integers = re.findall(r"\d+", text)
    if integers:
        return abs(int(integers[-1]))

    return None


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    """Parse each non-empty line of *text* as one JSON object."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _read_problem_records(path: Path) -> list[dict[str, Any]]:
    """Read problems from a JSON list/dict **or** a JSON-lines file.

    A set staged by ``chimera bench-fetch aimo`` is JSON-lines (one problem
    per line); a hand-authored set is usually a JSON list or a
    ``{"problems": [...]}`` wrapper. The JSON-lines shape is taken from the
    ``.jsonl`` suffix, or as a fallback when a whole-file ``json.loads``
    fails, so both stage through the same path.
    """
    text = path.read_text()
    if path.suffix == ".jsonl":
        return _parse_jsonl(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _parse_jsonl(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        records = data.get("problems", [])
        return records if isinstance(records, list) else []
    return []


def _coerce_answer(value: Any) -> Any:
    """Coerce a ground-truth answer to a non-negative int for grading.

    Public AIMO validation rows carry the answer as a string (``"116"``) or a
    float-like string (``"142.0"``); :func:`extract_answer` yields
    ``abs(int(...))`` from the agent output, so the ground truth must be the
    same int type for a correct answer to compare equal. Non-numeric answers
    pass through unchanged.
    """
    if isinstance(value, int):  # bool is an int subclass; harmless to pass through
        return value
    try:
        return abs(int(round(float(value))))
    except (TypeError, ValueError):
        return value


class AIMOBenchmark(Benchmark):
    """AIMO Progress Prize 3 benchmark.

    Loads olympiad-level math problems and evaluates by comparing
    the agent's extracted integer answer to the ground truth.
    """

    def __init__(
        self,
        problems_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        self._problems_path = problems_path
        self._limit = limit
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return "aimo3"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        expected = task.get("answer")
        if expected is None:
            return False
        extracted = extract_answer(agent_output)
        if extracted is None:
            return False
        return bool(extracted == expected)

    def _load_tasks(self) -> list[dict[str, Any]]:
        if self._problems_path:
            problems = _read_problem_records(Path(self._problems_path))
        else:
            problems = []

        tasks = []
        for p in problems:
            tasks.append({
                "id": p["id"],
                "prompt": self._format_prompt(p["problem"]),
                "answer": _coerce_answer(p["answer"]),
            })

        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _format_prompt(self, problem_text: str) -> str:
        return f"Solve the following math problem. {SYSTEM_PROMPT}\n\nPROBLEM:\n{problem_text}"
