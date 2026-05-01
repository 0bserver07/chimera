"""Tests for the BigCodeBench adapter.

Covers:

* dataset auto-detection / absent-skip path
* both ``complete`` and ``instruct`` splits parse a synthetic 2-task dump
* split-specific prompt selection from BCB record fields
* test execution against agent output for both ``check(<entry_point>)``
  and ``unittest.TestCase`` shapes, including markdown-fence stripping
* in-process exec fallback and env-mediated execution
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from chimera.eval.benchmarks.bigcodebench import (
    ENV_DATASET_PATH,
    VALID_SPLITS,
    BigCodeBench,
    _strip_code_fences,
    dataset_available,
    default_dataset_path,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def synthetic_tasks() -> list[dict]:
    """Two BCB-shaped tasks: one ``check`` style, one ``unittest`` style."""
    return [
        {
            "task_id": "BigCodeBench/0",
            "complete_prompt": (
                "import math\n\n"
                "def task_func(x):\n"
                "    \"\"\"Return floor(sqrt(x)).\"\"\"\n"
                "    # write your solution here\n"
            ),
            "instruct_prompt": (
                "Write a function task_func(x) that returns the integer "
                "floor of the square root of x using math.isqrt."
            ),
            "code_prompt": "import math\n\ndef task_func(x):\n",
            "test": (
                "def check(candidate):\n"
                "    assert candidate(0) == 0\n"
                "    assert candidate(4) == 2\n"
                "    assert candidate(10) == 3\n"
            ),
            "entry_point": "task_func",
            "libs": ["math"],
        },
        {
            "task_id": "BigCodeBench/1",
            "complete_prompt": (
                "def task_func(items):\n"
                "    \"\"\"Return the sum of items.\"\"\"\n"
                "    # write your solution here\n"
            ),
            "instruct_prompt": (
                "Write a function task_func(items) that returns sum(items)."
            ),
            "code_prompt": "def task_func(items):\n",
            "test": (
                "import unittest\n\n"
                "class TestCases(unittest.TestCase):\n"
                "    def test_basic(self):\n"
                "        self.assertEqual(task_func([1, 2, 3]), 6)\n"
                "    def test_empty(self):\n"
                "        self.assertEqual(task_func([]), 0)\n"
            ),
            "entry_point": "task_func",
            "libs": [],
        },
    ]


@pytest.fixture
def dataset_dir(tmp_path, synthetic_tasks):
    path = tmp_path / "bigcodebench"
    path.mkdir()
    (path / "tasks.json").write_text(json.dumps(synthetic_tasks))
    return path


# ----------------------------------------------------------------------
# Dataset resolution
# ----------------------------------------------------------------------


class TestDatasetResolution:
    def test_default_path_uses_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_DATASET_PATH, str(tmp_path))
        assert default_dataset_path() == tmp_path

    def test_default_path_fallback(self, monkeypatch):
        monkeypatch.delenv(ENV_DATASET_PATH, raising=False)
        path = default_dataset_path()
        assert "~" not in str(path)
        assert path.name == "bigcodebench"

    def test_dataset_available_missing(self, tmp_path):
        assert dataset_available(tmp_path / "nope") is False

    def test_dataset_available_empty_dir(self, tmp_path):
        assert dataset_available(tmp_path) is False

    def test_dataset_available_with_json(self, tmp_path):
        (tmp_path / "x.json").write_text("[]")
        assert dataset_available(tmp_path) is True

    def test_dataset_available_with_jsonl(self, tmp_path):
        (tmp_path / "x.jsonl").write_text("")
        assert dataset_available(tmp_path) is True

    def test_dataset_available_single_file(self, tmp_path):
        f = tmp_path / "x.json"
        f.write_text("[]")
        assert dataset_available(f) is True

    def test_dataset_absent_returns_empty_tasks(self, tmp_path):
        bench = BigCodeBench(dataset_path=str(tmp_path / "missing"))
        assert bench.tasks() == []


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


class TestConstruction:
    def test_default_split_is_instruct(self):
        assert BigCodeBench().split == "instruct"

    def test_invalid_split_raises(self):
        with pytest.raises(ValueError, match="split must be one of"):
            BigCodeBench(split="weird")  # type: ignore[arg-type]

    def test_valid_splits_constant(self):
        assert set(VALID_SPLITS) == {"complete", "instruct"}

    def test_name_includes_split(self):
        assert BigCodeBench(split="instruct").name() == "bigcodebench-instruct"
        assert BigCodeBench(split="complete").name() == "bigcodebench-complete"


# ----------------------------------------------------------------------
# Task loading + normalisation
# ----------------------------------------------------------------------


class TestTaskLoading:
    def test_instruct_split_uses_instruct_prompt(self, dataset_dir):
        bench = BigCodeBench(split="instruct", dataset_path=str(dataset_dir))
        tasks = bench.tasks()
        assert len(tasks) == 2
        assert tasks[0]["id"] == "BigCodeBench/0"
        assert "math.isqrt" in tasks[0]["prompt"]
        # original BCB fields preserved
        assert tasks[0]["entry_point"] == "task_func"
        assert tasks[0]["libs"] == ["math"]

    def test_complete_split_uses_complete_prompt(self, dataset_dir):
        bench = BigCodeBench(split="complete", dataset_path=str(dataset_dir))
        tasks = bench.tasks()
        assert "def task_func(x):" in tasks[0]["prompt"]
        assert "write your solution here" in tasks[0]["prompt"]

    def test_loads_from_single_file(self, dataset_dir):
        f = dataset_dir / "tasks.json"
        bench = BigCodeBench(dataset_path=str(f))
        assert len(bench.tasks()) == 2

    def test_loads_from_jsonl(self, tmp_path, synthetic_tasks):
        f = tmp_path / "tasks.jsonl"
        f.write_text("\n".join(json.dumps(t) for t in synthetic_tasks))
        bench = BigCodeBench(dataset_path=str(f))
        tasks = bench.tasks()
        assert len(tasks) == 2
        assert tasks[1]["id"] == "BigCodeBench/1"

    def test_limit_applied(self, dataset_dir):
        bench = BigCodeBench(dataset_path=str(dataset_dir), limit=1)
        assert len(bench.tasks()) == 1

    def test_handles_wrapped_dict_with_tasks(self, tmp_path, synthetic_tasks):
        (tmp_path / "wrapped.json").write_text(
            json.dumps({"tasks": synthetic_tasks})
        )
        bench = BigCodeBench(dataset_path=str(tmp_path))
        assert len(bench.tasks()) == 2

    def test_handles_hf_style_mapping(self, tmp_path, synthetic_tasks):
        mapping = {t["task_id"]: t for t in synthetic_tasks}
        (tmp_path / "hf.json").write_text(json.dumps(mapping))
        bench = BigCodeBench(dataset_path=str(tmp_path))
        ids = sorted(t["id"] for t in bench.tasks())
        assert ids == ["BigCodeBench/0", "BigCodeBench/1"]

    def test_prompt_falls_back_when_split_field_missing(self, tmp_path):
        # instruct_prompt missing -> falls back to complete_prompt
        record = {
            "task_id": "BCB/x",
            "complete_prompt": "from-complete",
            "test": "def check(c): pass",
            "entry_point": "f",
        }
        (tmp_path / "x.json").write_text(json.dumps([record]))
        bench = BigCodeBench(split="instruct", dataset_path=str(tmp_path))
        assert bench.tasks()[0]["prompt"] == "from-complete"


# ----------------------------------------------------------------------
# Fence stripper
# ----------------------------------------------------------------------


class TestStripCodeFences:
    def test_no_fences_returns_unchanged(self):
        src = "def f(): return 1"
        assert _strip_code_fences(src) == src

    def test_strips_python_fence(self):
        src = "Here you go:\n```python\ndef f(): return 1\n```"
        out = _strip_code_fences(src)
        assert "```" not in out
        assert "def f(): return 1" in out

    def test_concatenates_multiple_blocks(self):
        src = "```\nimport math\n```\nthen\n```python\ndef f():\n    pass\n```"
        out = _strip_code_fences(src)
        assert "import math" in out
        assert "def f():" in out
        assert "then" not in out


# ----------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------


@dataclass
class FakeCommandResult:
    exit_code: int


class FakeEnv:
    def __init__(self, exit_code: int = 0):
        self._exit_code = exit_code
        self.files: dict[str, str] = {}
        self.last_cmd = ""

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def run_command(self, cmd: str) -> FakeCommandResult:
        self.last_cmd = cmd
        return FakeCommandResult(exit_code=self._exit_code)


class TestEvaluate:
    def test_check_style_passes_in_process(self, synthetic_tasks):
        bench = BigCodeBench()
        task = synthetic_tasks[0]
        solution = "import math\ndef task_func(x):\n    return math.isqrt(x)\n"
        assert bench.evaluate(task, solution, None) is True

    def test_check_style_fails_in_process(self, synthetic_tasks):
        bench = BigCodeBench()
        task = synthetic_tasks[0]
        solution = "def task_func(x):\n    return x\n"  # wrong
        assert bench.evaluate(task, solution, None) is False

    def test_unittest_style_passes_in_process(self, synthetic_tasks):
        bench = BigCodeBench()
        task = synthetic_tasks[1]
        solution = "def task_func(items):\n    return sum(items)\n"
        assert bench.evaluate(task, solution, None) is True

    def test_unittest_style_fails_in_process(self, synthetic_tasks):
        bench = BigCodeBench()
        task = synthetic_tasks[1]
        solution = "def task_func(items):\n    return 0\n"
        assert bench.evaluate(task, solution, None) is False

    def test_strips_fences_before_exec(self, synthetic_tasks):
        bench = BigCodeBench()
        task = synthetic_tasks[0]
        fenced = (
            "Sure, here is the solution:\n"
            "```python\n"
            "import math\n"
            "def task_func(x):\n"
            "    return math.isqrt(x)\n"
            "```\n"
        )
        assert bench.evaluate(task, fenced, None) is True

    def test_env_path_writes_solution_and_runs(self, synthetic_tasks):
        bench = BigCodeBench()
        task = synthetic_tasks[0]
        env = FakeEnv(exit_code=0)
        ok = bench.evaluate(task, "def task_func(x): return 0", env)
        assert ok is True
        assert "solution.py" in env.files
        assert "check(task_func)" in env.files["solution.py"]
        assert env.last_cmd == "python solution.py"

    def test_env_nonzero_exit_fails(self, synthetic_tasks):
        bench = BigCodeBench()
        env = FakeEnv(exit_code=1)
        assert bench.evaluate(synthetic_tasks[0], "def task_func(x): return 0", env) is False

    def test_no_test_returns_false(self):
        bench = BigCodeBench()
        assert bench.evaluate({"entry_point": "f"}, "def f(): pass", None) is False

    def test_does_not_double_inject_check(self, synthetic_tasks):
        # When the test source already calls check(...), we must not append.
        bench = BigCodeBench()
        task = dict(synthetic_tasks[0])
        task["test"] = task["test"] + "\ncheck(task_func)\n"
        env = FakeEnv(exit_code=0)
        bench.evaluate(task, "def task_func(x): return 0", env)
        # The composed source should contain exactly one check(...) call.
        assert env.files["solution.py"].count("check(task_func)") == 1
