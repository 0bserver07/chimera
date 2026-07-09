"""Tests for dataset staging (``chimera bench-fetch``) — no real network."""

from __future__ import annotations

import io
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

import chimera.eval.datasets as ds


class _FakeResponse(io.BytesIO):
    """Minimal context-manager response like urlopen's."""

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


@pytest.fixture()
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_DATASETS_DIR", str(tmp_path))
    return tmp_path


def test_staged_path_none_when_absent(staging: Path) -> None:
    assert ds.staged_path("mbpp") is None
    assert ds.staged_path("no-such-bench") is None


def test_fetch_url_writes_file(staging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps([{"task_id": 1, "prompt": "p", "test_list": []}]).encode()
    monkeypatch.setattr(ds, "_urlopen", lambda url, timeout=0: _FakeResponse(payload))

    path = ds.fetch("mbpp")

    assert path.exists()
    assert json.loads(path.read_text())[0]["task_id"] == 1
    # Cached on second call (fetcher not re-invoked): poison the network.
    monkeypatch.setattr(ds, "_urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert ds.fetch("mbpp") == path
    # staged_path now resolves, including via alias-free canonical name.
    assert ds.staged_path("mbpp") == path


def test_fetch_hf_rows_paginates(staging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(url: str, timeout: int = 0) -> _FakeResponse:
        offset = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["offset"][0])
        if offset == 0:
            rows = [{"row": {"instance_id": f"i{n}"}} for n in range(100)]
        else:
            rows = [{"row": {"instance_id": f"i{100 + n}"}} for n in range(3)]
        return _FakeResponse(json.dumps({"rows": rows}).encode())

    monkeypatch.setattr(ds, "_urlopen", fake_urlopen)

    path = ds.fetch("swe-bench")

    lines = path.read_text().splitlines()
    assert len(lines) == 103
    assert json.loads(lines[0])["instance_id"] == "i0"
    assert json.loads(lines[-1])["instance_id"] == "i102"


def test_aliases_resolve_to_same_spec(staging: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ds, "_urlopen", lambda url, timeout=0: _FakeResponse(json.dumps({"rows": []}).encode())
    )
    path = ds.fetch("swebench")  # alias
    assert ds.staged_path("swe-bench-lite") == path  # another alias, same spec


def test_unknown_name_raises_with_available_list(staging: Path) -> None:
    with pytest.raises(ValueError, match="Fetchable:"):
        ds.fetch("definitely-not-a-bench")


def test_load_benchmark_autodiscovers_staged_dataset(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A staged file makes `chimera bench mbpp` work with no --dataset flag."""
    tasks = [
        {"task_id": 7, "prompt": "write add", "code": "def add(a,b): return a+b", "test_list": []}
    ]
    payload = json.dumps(tasks).encode()
    monkeypatch.setattr(ds, "_urlopen", lambda url, timeout=0: _FakeResponse(payload))
    ds.fetch("mbpp")

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("mbpp")
    assert len(bench.tasks()) == 1
    assert bench.tasks()[0]["task_id"] == 7


def test_fetch_lcb_transform_streams_and_reduces(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LCB rows are transformed: public tests decoded, private payload dropped."""
    rows = [
        {  # stdin/stdout problem — kept
            "question_id": "q1",
            "question_title": "Sum",
            "question_content": "Add two numbers.",
            "public_test_cases": json.dumps(
                [{"input": "1 2\n", "output": "3", "testtype": "stdin"}]
            ),
            "private_test_cases": "HUGE-PICKLED-BLOB" * 100,
            "difficulty": "easy",
            "contest_date": "2025-01-01T00:00:00",
            "platform": "codeforces",
            "starter_code": "",
        },
        {  # no decodable public tests — dropped
            "question_id": "q2",
            "question_title": "Bad",
            "public_test_cases": "",
        },
    ]
    payload = "\n".join(json.dumps(r) for r in rows).encode()
    monkeypatch.setattr(ds, "_urlopen", lambda url, timeout=0: _FakeResponse(payload))

    path = ds.fetch("livecodebench")

    tasks = json.loads(path.read_text())
    assert len(tasks) == 1
    task = tasks[0]
    assert task["id"] == "q1"
    assert task["test_cases"] == [{"input": "1 2\n", "output": "3", "testtype": "stdin"}]
    assert "stdin" in task["prompt"] and "Add two numbers." in task["prompt"]
    assert "private" not in json.dumps(task)  # pickled payload never staged
    # alias resolves to the same staged file
    assert ds.staged_path("lcb") == path


def test_lcb_staged_file_loads_through_adapter(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {
        "question_id": "q9",
        "question_title": "Echo",
        "question_content": "Echo input.",
        "public_test_cases": json.dumps([{"input": "hi\n", "output": "hi", "testtype": "stdin"}]),
        "difficulty": "easy",
        "contest_date": "2025-02-02T00:00:00",
        "platform": "atcoder",
        "starter_code": "",
    }
    monkeypatch.setattr(
        ds, "_urlopen", lambda url, timeout=0: _FakeResponse(json.dumps(row).encode())
    )
    ds.fetch("livecodebench")

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("livecodebench", limit=1)
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["test_cases"][0]["output"] == "hi"


# MBPP+ rows carry the base ``test_list`` (what the MBPP adapter grades) plus
# EvalPlus's expanded ``test`` harness (preserved for a future adapter).
_MBPP_PLUS_ROW = {
    "task_id": 2,
    "code": "def similar_elements(a, b):\n    return tuple(set(a) & set(b))",
    "prompt": "Write a function to find the shared elements from the given two lists.",
    "source_file": "Benchmark Questions Verification V2.ipynb",
    "test_imports": [],
    "test_list": [
        "assert set(similar_elements((3, 4, 5, 6), (5, 7, 4, 10))) == set((4, 5))",
        "assert set(similar_elements((1, 2, 3, 4), (5, 4, 3, 7))) == set((3, 4))",
        "assert set(similar_elements((11, 12, 14, 13), (17, 15, 14, 13))) == set((13, 14))",
    ],
    # The plus harness (real rows are up to ~790 KB); a stand-in here.
    "test": "import numpy as np\n# ... EvalPlus expanded assertions ...\n",
}


def test_fetch_mbpp_plus_stages_rows_preserving_plus_test(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MBPP+ stages as JSONL; the plus ``test`` field survives verbatim."""

    def fake_urlopen(url: str, timeout: int = 0) -> _FakeResponse:
        # Single page: fewer than _HF_PAGE rows terminates pagination.
        payload = {"rows": [{"row": _MBPP_PLUS_ROW}]}
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(ds, "_urlopen", fake_urlopen)

    path = ds.fetch("mbpp-plus")

    assert path.name == "test.jsonl" and path.parent.name == "mbpp-plus"
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    # Plus-value preserved in-row for a future plus-strength adapter.
    assert "test" in row and "EvalPlus" in row["test"]
    assert row["test_list"] and row["task_id"] == 2
    # Hyphenless alias resolves to the same staged file.
    assert ds.staged_path("mbppplus") == path


def test_mbpp_plus_staged_file_grades_through_mbpp_adapter(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged file is directly loadable by ``MBPP(dataset_path=...)`` and
    grades canonical ``code`` via ``test_list`` (base-strength)."""
    monkeypatch.setattr(
        ds, "_urlopen", lambda url, timeout=0: _FakeResponse(
            json.dumps({"rows": [{"row": _MBPP_PLUS_ROW}]}).encode()
        ),
    )
    staged = ds.fetch("mbpp-plus")

    from chimera.eval.benchmarks.mbpp import MBPP

    bench = MBPP(dataset_path=str(staged))
    tasks = bench.tasks()
    assert len(tasks) == 1
    task = tasks[0]
    # The plus harness rides along on the loaded task (unused by grading).
    assert "EvalPlus" in task["test"]

    # Canonical solution passes all base assertions; a wrong one fails.
    assert bench.evaluate(task, _MBPP_PLUS_ROW["code"], env=None) is True
    assert bench.evaluate(task, "def similar_elements(a, b):\n    return ()", env=None) is False


def _single_page(row: dict[str, Any]) -> Any:
    """A one-row datasets-server page (terminates hf-rows pagination)."""
    return lambda url, timeout=0: _FakeResponse(
        json.dumps({"rows": [{"row": row}]}).encode()
    )


def test_fetch_human_eval_base_stages_and_grades(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HumanEval base stages as JSONL and grades the canonical solution.

    The base bench previously loaded 0 tasks (its adapter only read a JSON
    list); the staged HuggingFace dump is JSON-lines, so this proves the
    JSONL load path end to end.
    """
    row = {
        "task_id": "HumanEval/0",
        "prompt": "def add(a, b):\n",
        "canonical_solution": "    return a + b\n",
        "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        "entry_point": "add",
    }
    monkeypatch.setattr(ds, "_urlopen", _single_page(row))

    staged = ds.fetch("human-eval")
    assert staged.name == "test.jsonl" and staged.parent.name == "human-eval"

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("human-eval")
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["entry_point"] == "add"
    # The staged `test` only DEFINES check(); grading appends check(entry_point).
    assert bench.evaluate(tasks[0], "def add(a, b):\n    return a + b\n", env=None) is True
    assert bench.evaluate(tasks[0], "def add(a, b):\n    return a - b\n", env=None) is False
    # Hyphenless alias resolves to the same staged file.
    assert ds.staged_path("humaneval") == staged


def test_fetch_bigcodebench_stages_and_grades(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BigCodeBench stages via hf-rows; the instruct prompt drives the agent."""
    row = {
        "task_id": "BigCodeBench/0",
        "complete_prompt": "def task_func():\n    # complete me\n",
        "instruct_prompt": "Write task_func that returns 3.",
        "code_prompt": "def task_func():\n",
        "test": (
            "import unittest\n"
            "class TestCases(unittest.TestCase):\n"
            "    def test_x(self):\n"
            "        self.assertEqual(task_func(), 3)\n"
        ),
        "entry_point": "task_func",
        "libs": "[]",
    }
    monkeypatch.setattr(ds, "_urlopen", _single_page(row))

    staged = ds.fetch("bigcodebench")
    assert staged.name == "v0.1.4.jsonl" and staged.parent.name == "bigcodebench"

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("bigcodebench")
    tasks = bench.tasks()
    assert len(tasks) == 1
    # Default split is instruct -> the instruct prompt is the agent prompt.
    assert tasks[0]["prompt"] == "Write task_func that returns 3."
    assert bench.evaluate(tasks[0], "def task_func():\n    return 3\n", env=None) is True
    assert bench.evaluate(tasks[0], "def task_func():\n    return 4\n", env=None) is False


def test_fetch_humaneval_x_stages_python_split(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HumanEval-X stages the raw repo JSONL (url kind); rows default to python.

    The upstream rows carry no ``language`` field (language is implied by the
    file path), so the adapter must default them to python for the wired
    execution path.
    """
    rows = [
        {
            "task_id": "Python/0",
            "prompt": "def add(a, b):\n",
            "declaration": "def add(a, b):\n",
            "canonical_solution": "    return a + b\n",
            "test": "def check(add):\n    assert add(1, 2) == 3\ncheck(add)\n",
        }
    ]
    payload = "\n".join(json.dumps(r) for r in rows).encode()
    monkeypatch.setattr(ds, "_urlopen", lambda url, timeout=0: _FakeResponse(payload))

    staged = ds.fetch("humaneval-x")
    assert staged.name == "python.jsonl" and staged.parent.name == "humaneval-x"

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("humaneval-x")
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "Python/0"
    assert tasks[0]["language"] == "python"
    # Hyphenless alias resolves to the same staged file.
    assert ds.staged_path("humanevalx") == staged


def test_fetch_aimo_stages_coerces_answer_and_grades(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AIMO stages via hf-rows; string/float answers are coerced to ints.

    Public rows store the answer as ``"116"`` or ``"142.0"``; the grader
    extracts ``abs(int(...))`` from agent output, so an un-coerced string
    ground truth would fail every task.
    """
    rows = [
        {"id": "0", "problem": "AIME-style problem", "answer": "116"},
        {"id": "1", "problem": "AMC-style problem", "answer": "142.0"},
    ]
    monkeypatch.setattr(
        ds,
        "_urlopen",
        lambda url, timeout=0: _FakeResponse(
            json.dumps({"rows": [{"row": r} for r in rows]}).encode()
        ),
    )

    staged = ds.fetch("aimo")
    assert staged.name == "aime.jsonl" and staged.parent.name == "aimo"

    from chimera.cli.main import _load_benchmark

    bench = _load_benchmark("aimo")
    tasks = bench.tasks()
    assert len(tasks) == 2
    assert tasks[0]["answer"] == 116 and isinstance(tasks[0]["answer"], int)
    assert tasks[1]["answer"] == 142 and isinstance(tasks[1]["answer"], int)
    # The coerced ground truth grades a matching agent answer, rejects a miss.
    assert bench.evaluate(tasks[0], "... so ANSWER: 116", env=None) is True
    assert bench.evaluate(tasks[0], "... so ANSWER: 999", env=None) is False
