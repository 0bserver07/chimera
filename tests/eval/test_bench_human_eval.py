from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass

from chimera.eval.benchmarks.human_eval import HumanEval


@dataclass
class FakeTestResult:
    all_passed: bool


@dataclass
class FakeCommandResult:
    exit_code: int


class FakeEnv:
    def __init__(self, tests_pass: bool = True):
        self._tests_pass = tests_pass
        self._files: dict[str, str] = {}

    def write_file(self, path: str, content: str) -> None:
        self._files[path] = content

    def run_command(self, cmd: str) -> FakeCommandResult:
        return FakeCommandResult(exit_code=0 if self._tests_pass else 1)

    def run_tests(self) -> FakeTestResult:
        return FakeTestResult(all_passed=self._tests_pass)


class TestHumanEval:
    def test_loads_from_json_file(self):
        tasks = [
            {
                "id": "HumanEval/0",
                "prompt": "def has_close_elements(numbers, threshold):",
                "test": "assert has_close_elements([1.0, 2.0], 0.5) == False",
            },
            {
                "id": "HumanEval/1",
                "prompt": "def separate_paren_groups(paren_string):",
                "test": "assert separate_paren_groups('(()())') == ['(()())']",
            },
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(tasks, f)
            f.flush()
            bench = HumanEval(dataset_path=f.name)
            loaded = bench.tasks()

        assert len(loaded) == 2
        assert loaded[0]["id"] == "HumanEval/0"

    def test_evaluate_with_test_code_and_env(self):
        bench = HumanEval()
        task = {
            "id": "HumanEval/0",
            "prompt": "def add(a, b):",
            "test": "assert add(1, 2) == 3",
        }
        env = FakeEnv(tests_pass=True)
        result = bench.evaluate(task, "def add(a, b): return a + b", env)
        assert result is True
        assert "solution.py" in env._files

    def test_evaluate_with_test_code_in_process(self):
        bench = HumanEval()
        task = {
            "id": "HumanEval/0",
            "prompt": "def add(a, b):",
            "test": "assert add(1, 2) == 3",
        }
        # No env, uses in-process exec
        result = bench.evaluate(task, "def add(a, b): return a + b", None)
        assert result is True

    def test_evaluate_in_process_failure(self):
        bench = HumanEval()
        task = {
            "id": "HumanEval/0",
            "prompt": "def add(a, b):",
            "test": "assert add(1, 2) == 3",
        }
        result = bench.evaluate(task, "def add(a, b): return a - b", None)
        assert result is False

    def test_evaluate_extracts_fenced_code_and_calls_check(self):
        # Real-HumanEval shape: prose + a ```python``` fence, and a `test`
        # field that only DEFINES check(candidate). Both must be handled.
        bench = HumanEval()
        task = {
            "id": "HumanEval/0",
            "entry_point": "add",
            "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        }
        reply = (
            "Here is the solution:\n\n```python\n"
            "def add(a, b):\n    return a + b\n```\n\nThat works."
        )
        assert bench.evaluate(task, reply, None) is True

    def test_evaluate_check_style_wrong_solution_fails(self):
        # Proves check() is actually invoked — no silent pass for wrong code.
        bench = HumanEval()
        task = {
            "id": "HumanEval/0",
            "entry_point": "add",
            "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        }
        reply = "```python\ndef add(a, b):\n    return a - b\n```"
        assert bench.evaluate(task, reply, None) is False

    def test_evaluate_without_test_and_without_env(self):
        bench = HumanEval()
        task = {"id": "HumanEval/0", "prompt": "def add(a, b):"}
        result = bench.evaluate(task, "def add(a, b): return a + b", None)
        assert result is False

    def test_evaluate_empty_output_fails(self):
        # An errored/empty agent run must never grade as a pass, even when the
        # `test` field only DEFINES check(candidate) without calling it inline.
        bench = HumanEval()
        task = {
            "id": "HumanEval/0",
            "entry_point": "add",
            "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        }
        assert bench.evaluate(task, "", None) is False
        assert bench.evaluate(task, "   \n\t ", None) is False

    def test_empty_dataset(self):
        bench = HumanEval()
        assert bench.tasks() == []

    def test_name(self):
        bench = HumanEval()
        assert bench.name() == "human-eval"

    def test_limit(self):
        tasks = [{"id": f"HumanEval/{i}", "prompt": f"task {i}"} for i in range(10)]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(tasks, f)
            f.flush()
            bench = HumanEval(dataset_path=f.name, limit=5)
            loaded = bench.tasks()

        assert len(loaded) == 5
