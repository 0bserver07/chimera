"""Smoke tests for the HumanEval-X scaffold."""
from __future__ import annotations

import json

import pytest

from chimera.eval.benchmarks.humaneval_x import (
    SUPPORTED_LANGUAGES,
    HumanEvalX,
    HumanEvalXTask,
)


class TestImportAndConstruction:
    def test_imports_via_package(self):
        from chimera.eval.benchmarks import HumanEvalX as HEXFromPkg

        assert HEXFromPkg is HumanEvalX

    def test_default_construction(self):
        bench = HumanEvalX()
        assert bench.name() == "humaneval-x"
        assert bench.tasks() == []

    def test_unsupported_language_rejected(self):
        with pytest.raises(ValueError):
            HumanEvalX(language="cobol")

    def test_supported_languages(self):
        assert set(HumanEvalX.supported_languages()) == set(SUPPORTED_LANGUAGES)


class TestLoading:
    @pytest.fixture
    def dataset(self, tmp_path):
        items = [
            {
                "task_id": "Python/0",
                "language": "python",
                "prompt": "def f():\n",
                "test": "def check(c):\n    pass\n",
                "canonical_solution": "    return 1\n",
            },
            {
                "task_id": "Java/0",
                "language": "java",
                "prompt": "// java",
                "test": "// test",
            },
            {
                "task_id": "Other/0",
                "language": "ruby",  # not supported, should be filtered
                "prompt": "",
            },
        ]
        path = tmp_path / "hex.json"
        path.write_text(json.dumps(items))
        return str(path)

    def test_loads_supported_languages_only(self, dataset):
        bench = HumanEvalX(dataset_path=dataset)
        ids = {t["id"] for t in bench.tasks()}
        assert ids == {"Python/0", "Java/0"}

    def test_language_filter(self, dataset):
        bench = HumanEvalX(dataset_path=dataset, language="java")
        assert len(bench.tasks()) == 1
        assert bench.tasks()[0]["language"] == "java"

    def test_name_with_language(self):
        bench = HumanEvalX(language="cpp")
        assert bench.name() == "humaneval-x-cpp"


class TestEvaluate:
    def test_python_in_process_pass(self):
        bench = HumanEvalX()
        task = {
            "language": "python",
            "prompt": "def add(a, b):\n",
            "test": (
                "def check(candidate):\n"
                "    assert candidate(1, 2) == 3\n"
                "check(add)\n"
            ),
        }
        completion = "    return a + b\n"
        assert bench.evaluate(task, completion) is True

    def test_python_in_process_fail(self):
        bench = HumanEvalX()
        task = {
            "language": "python",
            "prompt": "def add(a, b):\n",
            "test": (
                "def check(candidate):\n"
                "    assert candidate(1, 2) == 3\n"
                "check(add)\n"
            ),
        }
        assert bench.evaluate(task, "    return a - b\n") is False

    def test_non_python_returns_false_stub(self):
        bench = HumanEvalX()
        # Java path is intentionally stubbed — must return False, not raise.
        task = {"language": "java", "prompt": "", "test": ""}
        assert bench.evaluate(task, "irrelevant") is False


class TestKnownCorrectAnswerCanary:
    """A known-correct solution MUST grade as a pass, in every answer shape.

    The canary this benchmark lacked. ``coding-agent`` scored a uniform
    0/50 on a live Modal grid with ``status_counts {"completed": 50}`` —
    every task ran clean and none passed — because the grader concatenated
    the raw Markdown-fenced reply onto the prompt stub and every task died
    of ``SyntaxError``. The old tests only fed a bare indented body, the one
    shape an instructed chat agent never produces, so they stayed green
    throughout. Diagnosis: ``docs/notes/bench-diagnosis-darklight1.md``.
    """

    #: The real dataset contract: stub ends on a docstring, the reference
    #: solution is a bare indented body, the test self-drives via check(...).
    TASK = {
        "language": "python",
        "prompt": (
            "from typing import List\n"
            "\n"
            "\n"
            "def add_all(numbers: List[int]) -> int:\n"
            '    """ Sum every number in the list.\n'
            "    >>> add_all([1, 2, 3])\n"
            "    6\n"
            '    """\n'
        ),
        "test": (
            "def check(add_all):\n"
            "    assert add_all([1, 2, 3]) == 6\n"
            "    assert add_all([]) == 0\n"
            "\n"
            "check(add_all)\n"
        ),
        "canonical_solution": "    return sum(numbers)\n",
    }

    def test_bare_body_completion_shape_passes(self):
        """Upstream contract: a bare indented body continuing the stub."""
        bench = HumanEvalX()
        assert bench.evaluate(self.TASK, self.TASK["canonical_solution"]) is True

    def test_fenced_full_function_passes(self):
        """What an instructed chat agent actually returns — prose + fences.

        This is the exact shape ``FINAL_ANSWER_CONTRACT`` asks every matrix
        agent for ("the full code in one fenced code block"). Before the fix
        it graded False for all 164 tasks.
        """
        bench = HumanEvalX()
        answer = (
            "Here's the implementation:\n\n"
            "```python\n"
            f"{self.TASK['prompt']}{self.TASK['canonical_solution']}"
            "```\n\n"
            "It sums the list and returns 0 when empty."
        )
        assert bench.evaluate(self.TASK, answer) is True

    def test_fenced_bare_body_passes(self):
        """A fenced answer carrying only the body still grades."""
        bench = HumanEvalX()
        answer = f"```python\n{self.TASK['canonical_solution']}```"
        assert bench.evaluate(self.TASK, answer) is True

    def test_unfenced_full_source_passes(self):
        """A bare full module with no fences at all."""
        bench = HumanEvalX()
        answer = f"{self.TASK['prompt']}{self.TASK['canonical_solution']}"
        assert bench.evaluate(self.TASK, answer) is True

    def test_wrong_fenced_answer_still_fails(self):
        """Accepting more shapes must not make the grader lenient."""
        bench = HumanEvalX()
        answer = (
            "```python\n"
            "from typing import List\n"
            "\n"
            "\n"
            "def add_all(numbers: List[int]) -> int:\n"
            "    return len(numbers)\n"
            "```"
        )
        assert bench.evaluate(self.TASK, answer) is False

    def test_empty_answer_never_passes(self):
        """An errored run has nothing to grade (measurement integrity)."""
        bench = HumanEvalX()
        assert bench.evaluate(self.TASK, "") is False
        assert bench.evaluate(self.TASK, "   \n  ") is False
        assert bench.evaluate(self.TASK, "```python\n```") is False

    def test_prose_only_answer_never_passes(self):
        """A summary instead of an artifact is a miss, not a pass."""
        bench = HumanEvalX()
        assert bench.evaluate(self.TASK, "I implemented the function.") is False


class TestInstanceShape:
    def test_to_dict_round_trip(self):
        t = HumanEvalXTask(
            task_id="Go/3",
            language="go",
            prompt="func Add(a, b int) int {",
            test="// test",
            canonical_solution="    return a + b\n}",
        )
        assert t.to_dict()["id"] == "Go/3"
        assert t.to_dict()["language"] == "go"

    def test_add_instance(self):
        bench = HumanEvalX()
        bench.add_instance(
            HumanEvalXTask(
                task_id="JavaScript/1",
                language="javascript",
                prompt="function f() {}",
            )
        )
        assert len(bench.tasks()) == 1
