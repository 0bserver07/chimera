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


def test_mbpp_grades_fenced_answer() -> None:
    bench = MBPP()
    task = _task()
    fenced = "```python\n" + task["code"] + "\n```"
    assert bench.evaluate(task, task["code"], None) is True
    assert bench.evaluate(task, fenced, None) is True
    assert bench.evaluate(task, "```python\ndef add(a, b):\n    return a - b\n```", None) is False
