# chimera/eval/benchmarks/math500.py
"""MATH-500 benchmark adapter.

MATH-500 is a 500-problem subset of the MATH benchmark spanning seven
competition-math subjects (algebra, counting & probability, geometry,
intermediate algebra, number theory, prealgebra, precalculus) at
difficulty levels 1-5. Answers are LaTeX expressions wrapped in
``\\boxed{...}``.

Dataset: https://huggingface.co/datasets/HuggingFaceH4/MATH-500
Paper: https://arxiv.org/abs/2305.20050 ("Let's Verify Step by Step")

The adapter loads problems either from a local JSON/JSONL file or from
HuggingFace ``datasets`` if installed. Evaluation extracts the agent's
final ``\\boxed{...}`` answer and compares it to the ground truth using
normalized string equivalence first, then optional symbolic equivalence
via ``sympy`` if available.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chimera.eval.harness import Benchmark


SYSTEM_PROMPT = """\
You are a competition mathematics solver. Given a problem from the MATH
benchmark:

1. Reason step-by-step about the solution.
2. Optionally write Python code (sympy, numpy) and execute it via the
   bash tool to compute or verify intermediate values.
3. Present your final answer wrapped in \\boxed{...} on the last line.

Your final answer MUST appear inside \\boxed{...}. The contents may be
an integer, fraction, radical, or other LaTeX expression."""


_BOXED_RE = re.compile(r"\\boxed\s*\{")
_ANSWER_RE = re.compile(r"ANSWER:\s*(.+?)(?:\n|$)")


def _extract_boxed(text: str) -> str | None:
    """Extract the contents of the last ``\\boxed{...}`` in text.

    Handles nested braces by counting depth, since LaTeX answers often
    contain ``\\frac{a}{b}`` and similar constructs.

    Args:
        text: Agent output to scan.

    Returns:
        The string inside the last ``\\boxed{...}``, or ``None`` if no
        well-formed boxed expression is found.
    """
    last: str | None = None
    for match in _BOXED_RE.finditer(text):
        i = match.end()
        depth = 1
        start = i
        while i < len(text) and depth > 0:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last = text[start:i]
                    break
            i += 1
    return last


def extract_answer(text: str) -> str | None:
    """Extract the agent's final answer from output.

    Looks for (in priority order):

    1. The contents of the last ``\\boxed{...}``.
    2. The text following an ``ANSWER:`` marker.

    Args:
        text: Raw agent output.

    Returns:
        The extracted answer string, or ``None`` if neither pattern matches.
    """
    boxed = _extract_boxed(text)
    if boxed is not None:
        return boxed.strip()
    match = _ANSWER_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def normalize_answer(answer: str) -> str:
    """Normalize a math answer for string-equivalence comparison.

    Strips whitespace, removes ``\\left``/``\\right``, ``\\!``, ``\\,``,
    ``\\;``, ``\\ ``, and ``\\quad`` spacing macros, removes a leading
    ``+``, removes wrapping ``$`` delimiters, and collapses internal
    whitespace.

    Args:
        answer: Raw extracted answer string.

    Returns:
        Normalized form suitable for direct equality comparison.
    """
    if answer is None:
        return ""
    s = answer.strip()
    s = s.replace("\\left", "").replace("\\right", "")
    for macro in ("\\!", "\\,", "\\;", "\\ ", "\\quad", "\\qquad"):
        s = s.replace(macro, "")
    s = s.strip()
    if s.startswith("$") and s.endswith("$"):
        s = s[1:-1].strip()
    if s.startswith("+"):
        s = s[1:].strip()
    s = re.sub(r"\s+", "", s)
    return s


def answers_equivalent(extracted: str, expected: str) -> bool:
    """Compare two math answers for equivalence.

    First normalizes both strings and compares directly. If that fails
    and ``sympy`` is importable, attempts symbolic equivalence by
    parsing both sides and checking ``simplify(a - b) == 0``. Sympy
    failures fall back to ``False`` rather than propagating exceptions.

    Args:
        extracted: Answer extracted from agent output.
        expected: Ground-truth answer from the dataset.

    Returns:
        ``True`` if the answers are equivalent, otherwise ``False``.
    """
    if extracted is None or expected is None:
        return False
    if normalize_answer(extracted) == normalize_answer(expected):
        return True
    try:
        from sympy import simplify  # type: ignore[import-not-found]
        from sympy.parsing.latex import parse_latex  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        diff = simplify(parse_latex(extracted) - parse_latex(expected))
        return bool(diff == 0)
    except Exception:
        return False


class MATH500Benchmark(Benchmark):
    """MATH-500 benchmark adapter.

    Loads 500 competition math problems and evaluates by extracting the
    agent's ``\\boxed{...}`` answer and comparing against the ground
    truth using normalized string equivalence with optional sympy
    symbolic fallback.

    Args:
        problems_path: Optional path to a local JSON or JSONL file with
            MATH-500 problems. Each entry should contain ``problem``,
            ``answer``, ``subject``, ``level``, and optionally
            ``unique_id``. If omitted, the adapter attempts to load from
            HuggingFace via the ``datasets`` library.
        limit: Optional cap on the number of tasks returned.
        subject: Optional subject filter (e.g. ``"Algebra"``).
        level: Optional difficulty filter (1-5).
    """

    def __init__(
        self,
        problems_path: str | None = None,
        limit: int | None = None,
        subject: str | None = None,
        level: int | None = None,
    ) -> None:
        self._problems_path = problems_path
        self._limit = limit
        self._subject = subject
        self._level = level
        self._tasks: list[dict[str, Any]] | None = None

    def name(self) -> str:
        return "math500"

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
        return answers_equivalent(extracted, str(expected))

    def _load_tasks(self) -> list[dict[str, Any]]:
        problems = self._load_problems()
        tasks: list[dict[str, Any]] = []
        for i, p in enumerate(problems):
            subject = p.get("subject") or p.get("type")
            level = p.get("level")
            if self._subject and subject != self._subject:
                continue
            if self._level is not None and level != self._level:
                continue
            tasks.append({
                "id": p.get("unique_id") or p.get("id") or f"math500-{i}",
                "prompt": self._format_prompt(p["problem"]),
                "answer": p["answer"],
                "subject": subject,
                "level": level,
            })
        if self._limit:
            tasks = tasks[: self._limit]
        return tasks

    def _load_problems(self) -> list[dict[str, Any]]:
        if self._problems_path:
            path = Path(self._problems_path)
            text = path.read_text()
            if path.suffix == ".jsonl":
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            data = json.loads(text)
            return data if isinstance(data, list) else data.get("problems", [])
        # HuggingFace fallback
        try:
            from datasets import load_dataset  # type: ignore[import-not-found]
        except Exception as e:
            raise RuntimeError(
                "MATH500Benchmark requires either problems_path= or the "
                "`datasets` package installed (pip install datasets)."
            ) from e
        ds = load_dataset("HuggingFaceH4/MATH-500", split="test")
        return [dict(row) for row in ds]

    def _format_prompt(self, problem_text: str) -> str:
        return f"{SYSTEM_PROMPT}\n\nPROBLEM:\n{problem_text}"
