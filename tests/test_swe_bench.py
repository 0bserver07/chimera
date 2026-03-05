"""Tests for SWE-bench benchmark implementation."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from chimera.eval.benchmarks.swe_bench import SWEBench, SWEBenchInstance


@pytest.fixture
def sample_dataset():
    """Create a temporary SWE-bench dataset file."""
    instances = [
        {
            "instance_id": "test__test-repo__1",
            "repo": "test/test-repo",
            "base_commit": "abc123",
            "problem_statement": "Fix the bug in utils.py",
            "hints_text": "Look at the parse function",
            "test_patch": "diff --git a/test_fix.py b/test_fix.py\n",
            "patch": "diff --git a/utils.py b/utils.py\n",
        },
        {
            "instance_id": "test__test-repo__2",
            "repo": "test/test-repo",
            "base_commit": "def456",
            "problem_statement": "Add error handling to API",
            "hints_text": "",
            "test_patch": "",
            "patch": "",
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(instances, f)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def jsonl_dataset():
    """Create a JSONL format dataset."""
    instances = [
        {"instance_id": "jsonl_1", "repo": "a/b", "base_commit": "111", "problem_statement": "Fix X"},
        {"instance_id": "jsonl_2", "repo": "c/d", "base_commit": "222", "problem_statement": "Fix Y"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")
        path = f.name
    yield path
    os.unlink(path)


class TestSWEBench:
    def test_load_json_array(self, sample_dataset):
        bench = SWEBench(dataset_path=sample_dataset)
        assert len(bench.tasks()) == 2

    def test_load_jsonl(self, jsonl_dataset):
        bench = SWEBench(dataset_path=jsonl_dataset)
        assert len(bench.tasks()) == 2

    def test_limit(self, sample_dataset):
        bench = SWEBench(dataset_path=sample_dataset, limit=1)
        assert len(bench.tasks()) == 1

    def test_name(self):
        bench = SWEBench(split="lite")
        assert bench.name() == "swe-bench"

    def test_task_structure(self, sample_dataset):
        bench = SWEBench(dataset_path=sample_dataset)
        task = bench.tasks()[0]
        assert task["id"] == "test__test-repo__1"
        assert "Fix the bug" in task["description"]
        assert task["repo"] == "test/test-repo"
        assert task["base_commit"] == "abc123"

    def test_instances_property(self, sample_dataset):
        bench = SWEBench(dataset_path=sample_dataset)
        assert len(bench.instances) == 2
        assert isinstance(bench.instances[0], SWEBenchInstance)

    def test_add_instance(self):
        bench = SWEBench()
        inst = SWEBenchInstance(
            instance_id="manual_1",
            repo="x/y",
            base_commit="aaa",
            problem_statement="Do something",
        )
        bench.add_instance(inst)
        assert len(bench.tasks()) == 1

    def test_evaluate_without_env(self):
        bench = SWEBench()
        task = {"test_patch": ""}
        # Without env, always returns False
        assert bench.evaluate(task, "This is a real patch with content") is False

    def test_missing_dataset(self):
        with pytest.raises(FileNotFoundError):
            SWEBench(dataset_path="/nonexistent/path.json")

    def test_empty_constructor(self):
        bench = SWEBench()
        assert bench.tasks() == []
        assert bench.name() == "swe-bench"

    def test_instance_to_task(self):
        inst = SWEBenchInstance(
            instance_id="t1",
            repo="r/r",
            base_commit="c1",
            problem_statement="desc",
            hints_text="hint",
            test_patch="patch",
        )
        task = inst.to_task()
        assert task["id"] == "t1"
        assert task["hints"] == "hint"
        assert task["test_patch"] == "patch"
