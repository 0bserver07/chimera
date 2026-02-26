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
        self._tasks: list[dict] | None = None

    def name(self) -> str:
        return "aimo3"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_tasks()
        return self._tasks

    def evaluate(self, task: dict, agent_output: str, env: Any) -> bool:
        expected = task.get("answer")
        if expected is None:
            return False
        extracted = extract_answer(agent_output)
        if extracted is None:
            return False
        return extracted == expected

    def _load_tasks(self) -> list[dict]:
        if self._problems_path:
            data = json.loads(Path(self._problems_path).read_text())
            problems = data if isinstance(data, list) else data.get("problems", [])
        else:
            problems = []

        tasks = []
        for p in problems:
            tasks.append({
                "id": p["id"],
                "prompt": self._format_prompt(p["problem"]),
                "answer": p["answer"],
            })

        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _format_prompt(self, problem_text: str) -> str:
        return f"Solve the following math problem. {SYSTEM_PROMPT}\n\nPROBLEM:\n{problem_text}"
