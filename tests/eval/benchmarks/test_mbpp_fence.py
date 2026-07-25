"""Regression: fenced (markdown) answers must grade across code adapters.

Caught by the first full agent x benchmark grid: adapters without fence
extraction scored a uniform 0% across all 13 agents while fence-aware columns
(human-eval, math500) passed.
"""

from __future__ import annotations

from chimera.eval.benchmarks._code_extract import extract_code
from chimera.eval.benchmarks.mbpp import MBPP


def _task() -> dict:
    return {
        "id": "Mbpp/1",
        "task_id": 1,
        "prompt": "Write add.",
        "code": "def add(a, b):\n    return a + b",
        "test_list": ["assert add(1, 2) == 3"],
        "test_imports": [],
    }


def test_extract_code_passthrough_and_fences() -> None:
    bare = "def f():\n    return 1"
    assert extract_code(bare) == bare
    fenced = f"Here you go:\n```python\n{bare}\n```\nDone!"
    assert extract_code(fenced) == bare


def test_extract_code_preserves_leading_indentation() -> None:
    """A fenced *completion* keeps its indentation.

    Completion-style datasets (HumanEval-X) grade ``prompt + answer``, so
    dedenting the first line turns a correct body into an
    ``IndentationError``. The fence regex used to consume the newline with a
    greedy ``\\s*``, which ate the indentation too.
    """
    body = "    return sum(numbers)"
    assert extract_code(f"```python\n{body}\n```") == body
    assert extract_code(f"```\n{body}\n```") == body
    assert extract_code(f"Sure:\n```py\n{body}\n```\nDone.") == body
    # A whole module is unaffected — its first line starts at column 0.
    assert extract_code("```python\ndef f():\n    return 1\n```") == "def f():\n    return 1"


def test_mbpp_grades_fenced_answer() -> None:
    bench = MBPP()
    task = _task()
    fenced = "```python\n" + task["code"] + "\n```"
    assert bench.evaluate(task, task["code"], None) is True
    assert bench.evaluate(task, fenced, None) is True
    assert bench.evaluate(task, "```python\ndef add(a, b):\n    return a - b\n```", None) is False
