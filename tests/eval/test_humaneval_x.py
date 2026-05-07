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
