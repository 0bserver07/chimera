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


# ---------------------------------------------------------------------------
# MBPP+ grades with the EXPANDED harness, not the base asserts (#19).
#
# The published 99.7% was "MBPP+ tasks, base-graded": MBPPPlus inherited MBPP's
# test_list path — three or four assertions against a suite designed to have
# hundreds. The gap was disclosed on the observatory rather than hidden; these
# tests close it and pin that it stays closed.
# ---------------------------------------------------------------------------

from chimera.eval.benchmarks.mbpp import MBPPPlus  # noqa: E402

# is_not_prime, with the real MBPP+ shape: a self-driving harness that names
# the function itself (no check(candidate), no entry_point).
_PLUS_TASK = {
    "task_id": 3,
    "test_list": [
        "assert is_not_prime(2) == False",
        "assert is_not_prime(10) == True",
    ],
    "test": (
        "def assertion(out, exp, atol):\n"
        "    assert out == exp, (out, exp)\n"
        "inputs = [[2],[3],[4],[9],[10],[11],[15],[17],[21],[25],[35],[37],[49]]\n"
        "results = [False,False,True,True,True,False,True,False,True,True,True,False,True]\n"
        "for inp, exp in zip(inputs, results):\n"
        "    assertion(is_not_prime(*inp), exp, 0)\n"
    ),
    "code": (
        "def is_not_prime(n):\n"
        "    if n < 2:\n        return True\n"
        "    for i in range(2, int(n**0.5) + 1):\n"
        "        if n % i == 0:\n            return True\n"
        "    return False\n"
    ),
}

# Satisfies every base assertion by lookup and nothing else.
_CHEAT = "def is_not_prime(n):\n    return {2: False, 10: True}.get(n, False)\n"


class TestMBPPPlusGradesWithTheExpandedHarness:
    def test_canonical_solution_passes(self) -> None:
        assert MBPPPlus().evaluate(_PLUS_TASK, _PLUS_TASK["code"], None) is True

    def test_the_plus_harness_is_strictly_stronger_than_the_base_asserts(self) -> None:
        """The whole justification for MBPP+ existing.

        A hardcoded lookup satisfies every base assertion and is obviously not
        a prime test. Base accepts it; plus must not. If this ever fails,
        mbpp-plus has silently reverted to base-strength grading and its
        published number is overstated again.
        """
        bench = MBPPPlus()
        assert MBPP.evaluate(bench, _PLUS_TASK, _CHEAT, None) is True, "base should accept the cheat"
        assert bench.evaluate(_PLUS_TASK, _CHEAT, None) is False, "plus must reject it"

    def test_empty_and_prose_answers_are_rejected(self) -> None:
        bench = MBPPPlus()
        assert bench.evaluate(_PLUS_TASK, "", None) is False
        assert bench.evaluate(_PLUS_TASK, "I could not solve this.", None) is False

    def test_fenced_answers_are_normalised(self) -> None:
        fenced = f"Here:\n\n```python\n{_PLUS_TASK['code']}```\n"
        assert MBPPPlus().evaluate(_PLUS_TASK, fenced, None) is True

    def test_a_row_without_the_plus_blob_degrades_to_base_not_to_a_pass(self) -> None:
        """A partially-staged dataset must grade weaker, never vacuously pass."""
        row = dict(_PLUS_TASK, test="")
        bench = MBPPPlus()
        assert bench.graded_strength(row) == "base"
        assert bench.evaluate(row, _PLUS_TASK["code"], None) is True
        assert bench.evaluate(row, "def is_not_prime(n):\n    return True\n", None) is False

    def test_graded_strength_names_the_contract_that_ran(self) -> None:
        assert MBPPPlus.graded_strength(_PLUS_TASK) == "plus"
        assert MBPPPlus.graded_strength({"test": "   "}) == "base"
