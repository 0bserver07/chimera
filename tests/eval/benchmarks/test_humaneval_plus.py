"""Tests for the HumanEval+ (EvalPlus) benchmark adapter.

Covers ctor ``version`` validation, the ``name`` string, loading (JSON array /
JSONL / envelope) with ``limit``, the JSONL serialization helper, the EvalPlus
CLI output parser, and — critically — the **pure-Python fallback** grading
path exercised when the optional ``evalplus`` package is unavailable.

The ``evalplus`` package is not installed in the test venv; an autouse fixture
additionally forces the import to fail so the fallback path is deterministic
regardless of environment. The official-runner path (``use_evalplus_runner``)
is therefore *not* exercised here — it needs the real package and is
documented as out of scope for a no-network unit test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.humaneval_plus import HumanEvalPlus


@pytest.fixture(autouse=True)
def _force_evalplus_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee ``import evalplus`` fails so the pure-Python path is taken."""
    monkeypatch.setitem(sys.modules, "evalplus", None)


class _ExecEnv:
    """Env that runs the spliced solution via write_file + run_command."""

    def __init__(self, *, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.files: dict[str, str] = {}
        self.commands: list[str] = []

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        return SimpleNamespace(exit_code=self._exit_code)


# --------------------------------------------------------------------------- ctor


def test_bad_version_raises() -> None:
    with pytest.raises(ValueError, match="version must be"):
        HumanEvalPlus(version="deluxe")


def test_name_includes_version() -> None:
    assert HumanEvalPlus(version="plus").name() == "human-eval-plus"
    assert HumanEvalPlus(version="base").name() == "human-eval-base"


# ------------------------------------------------------------------------- loading


def test_no_dataset_and_no_evalplus_yields_empty() -> None:
    assert HumanEvalPlus().tasks() == []


def test_load_json_array(tmp_path: Path) -> None:
    path = tmp_path / "he.json"
    path.write_text(json.dumps([
        {"task_id": "HumanEval/0", "prompt": "p", "test": "assert True"},
        {"task_id": "HumanEval/1", "prompt": "p", "test": "assert True"},
    ]))
    assert len(HumanEvalPlus(dataset_path=str(path)).tasks()) == 2


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "he.jsonl"
    path.write_text("\n".join(
        json.dumps({"task_id": f"HumanEval/{i}", "prompt": "p", "test": "assert True"})
        for i in range(3)
    ))
    assert len(HumanEvalPlus(dataset_path=str(path)).tasks()) == 3


def test_load_envelope(tmp_path: Path) -> None:
    path = tmp_path / "he.json"
    path.write_text(json.dumps({"tasks": [{"task_id": "HumanEval/0", "prompt": "p"}]}))
    assert len(HumanEvalPlus(dataset_path=str(path)).tasks()) == 1


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "he.json"
    path.write_text(json.dumps([
        {"task_id": f"HumanEval/{i}", "prompt": "p"} for i in range(5)
    ]))
    assert len(HumanEvalPlus(dataset_path=str(path), limit=2).tasks()) == 2


# ------------------------------------------------------------- evaluate (fallback)


def _task(**over: object) -> dict:
    base: dict = {
        "task_id": "HumanEval/0",
        "prompt": "def add(a, b):",
        "entry_point": "add",
        "test": "assert add(1, 2) == 3",
        "test_plus": "assert add(1, 2) == 3 and add(-1, 1) == 0",
    }
    base.update(over)
    return base


def test_evaluate_correct_solution_passes_base() -> None:
    bench = HumanEvalPlus(version="base", use_evalplus_runner=False)
    assert bench.evaluate(_task(), "def add(a, b):\n    return a + b", None) is True


def test_evaluate_wrong_solution_fails_base() -> None:
    bench = HumanEvalPlus(version="base", use_evalplus_runner=False)
    assert bench.evaluate(_task(), "def add(a, b):\n    return a - b", None) is False


def test_evaluate_uses_plus_tests_when_version_plus() -> None:
    """The stricter ``test_plus`` suite catches a solution the base tests miss."""
    bench = HumanEvalPlus(version="plus", use_evalplus_runner=False)
    # Passes base (1+2==3) but the plus case add(-1,1)==0 needs real addition.
    sneaky = "def add(a, b):\n    return 3"
    assert bench.evaluate(_task(), sneaky, None) is False
    assert bench.evaluate(_task(), "def add(a, b):\n    return a + b", None) is True


def test_evaluate_no_test_code_is_false() -> None:
    bench = HumanEvalPlus(version="base", use_evalplus_runner=False)
    assert bench.evaluate({"task_id": "x", "prompt": "p"}, "def f(): pass", None) is False


def test_evaluate_routes_to_env_when_provided() -> None:
    bench = HumanEvalPlus(version="base", use_evalplus_runner=False)
    env = _ExecEnv(exit_code=0)
    assert bench.evaluate(_task(), "def add(a, b):\n    return a + b", env) is True
    assert env.commands == ["python solution.py"]
    assert "solution.py" in env.files


def test_evaluate_env_nonzero_exit_is_failure() -> None:
    bench = HumanEvalPlus(version="base", use_evalplus_runner=False)
    assert bench.evaluate(_task(), "def add(a, b):\n    return a + b", _ExecEnv(exit_code=1)) is False


# ---------------------------------------------------------------------- utilities


def test_to_evalplus_jsonl_round_trips(tmp_path: Path) -> None:
    bench = HumanEvalPlus()
    out = bench.to_evalplus_jsonl(
        {"HumanEval/0": "def add(a, b): return a + b"}, tmp_path / "s.jsonl"
    )
    lines = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    assert lines == [{"task_id": "HumanEval/0", "solution": "def add(a, b): return a + b"}]


def test_parse_evalplus_result() -> None:
    parse = HumanEvalPlus._parse_evalplus_result
    assert parse("HumanEval/0 pass", "HumanEval/0") is True
    assert parse("HumanEval/0 fail pass", "HumanEval/0") is False
    assert parse("summary: all tests passed", "HumanEval/9") is True
    assert parse("nothing relevant here", "HumanEval/9") is False
