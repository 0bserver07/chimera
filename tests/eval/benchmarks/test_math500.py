"""Tests for the MATH-500 benchmark adapter.

Covers the module-level answer utilities (``_extract_boxed``, ``extract_answer``,
``normalize_answer``, ``answers_equivalent``), the ``name`` string, loading
(JSON list / JSONL / ``{"problems": [...]}``) with ``subject`` / ``level`` /
``limit`` filters, the prompt formatting, and the deterministic ``evaluate``
grading path (extract-boxed then normalized-string equivalence).

No network, no LLM: the optional ``datasets`` package IS importable in the
test venv, so the no-dataset test forces its import to fail (via ``sys.modules``)
to exercise the documented ``RuntimeError`` without hitting HuggingFace.
Symbolic (``sympy``) equivalence is out of scope — the grading tests use answers
that match under pure normalization, which needs no optional dependency.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from chimera.eval.benchmarks.math500 import (
    MATH500Benchmark,
    SYSTEM_PROMPT,
    _extract_boxed,
    answers_equivalent,
    extract_answer,
    normalize_answer,
)


# ------------------------------------------------------------------- extract_boxed


def test_extract_boxed_simple() -> None:
    assert _extract_boxed(r"The result is \boxed{42}.") == "42"


def test_extract_boxed_handles_nested_braces() -> None:
    assert _extract_boxed(r"\boxed{\frac{1}{2}}") == r"\frac{1}{2}"


def test_extract_boxed_returns_last() -> None:
    assert _extract_boxed(r"first \boxed{1} then \boxed{2}") == "2"


def test_extract_boxed_none_when_absent() -> None:
    assert _extract_boxed("no box here") is None


# ------------------------------------------------------------------ extract_answer


def test_extract_answer_prefers_boxed() -> None:
    assert extract_answer(r"ANSWER: 7 but really \boxed{9}") == "9"


def test_extract_answer_falls_back_to_marker() -> None:
    assert extract_answer("ANSWER: 7") == "7"


def test_extract_answer_none_when_neither() -> None:
    assert extract_answer("just prose, no answer") is None


# ----------------------------------------------------------------- normalize


def test_normalize_strips_left_right_and_whitespace() -> None:
    assert normalize_answer(r"\left(3\right)") == "(3)"


def test_normalize_strips_dollar_delimiters() -> None:
    assert normalize_answer("$5$") == "5"


def test_normalize_strips_leading_plus_and_spacing_macros() -> None:
    assert normalize_answer(r"+1\,000") == "1000"


# ------------------------------------------------------------- answers_equivalent


def test_answers_equivalent_via_normalization() -> None:
    assert answers_equivalent(" 5 ", "5") is True
    assert answers_equivalent(r"\left(3\right)", "(3)") is True


def test_answers_not_equivalent() -> None:
    assert answers_equivalent("6", "5") is False


def test_answers_equivalent_none_inputs() -> None:
    assert answers_equivalent(None, "5") is False  # type: ignore[arg-type]
    assert answers_equivalent("5", None) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- name


def test_name_is_math500() -> None:
    assert MATH500Benchmark().name() == "math500"


# ------------------------------------------------------------------------- loading


def test_no_path_and_no_datasets_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented behavior: no problems_path + no ``datasets`` -> RuntimeError."""
    monkeypatch.setitem(sys.modules, "datasets", None)
    with pytest.raises(RuntimeError, match="requires either problems_path"):
        MATH500Benchmark().tasks()


def _problem(**over: object) -> dict:
    base: dict = {
        "unique_id": "math500-x",
        "problem": "What is 1 + 1?",
        "answer": "2",
        "subject": "Algebra",
        "level": 1,
    }
    base.update(over)
    return base


def test_load_json_list(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_problem(unique_id="a"), _problem(unique_id="b")]))
    tasks = MATH500Benchmark(problems_path=str(path)).tasks()
    assert len(tasks) == 2
    assert tasks[0]["answer"] == "2"


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"
    path.write_text("\n".join(json.dumps(_problem(unique_id=f"j{i}")) for i in range(3)))
    assert len(MATH500Benchmark(problems_path=str(path)).tasks()) == 3


def test_load_problems_envelope(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"problems": [_problem()]}))
    assert len(MATH500Benchmark(problems_path=str(path)).tasks()) == 1


def test_subject_filter(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([
        _problem(unique_id="a", subject="Algebra"),
        _problem(unique_id="b", subject="Geometry"),
    ]))
    tasks = MATH500Benchmark(problems_path=str(path), subject="Geometry").tasks()
    assert [t["id"] for t in tasks] == ["b"]


def test_level_filter(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([
        _problem(unique_id="a", level=1),
        _problem(unique_id="b", level=5),
    ]))
    tasks = MATH500Benchmark(problems_path=str(path), level=5).tasks()
    assert [t["id"] for t in tasks] == ["b"]


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_problem(unique_id=f"t{i}") for i in range(5)]))
    assert len(MATH500Benchmark(problems_path=str(path), limit=2).tasks()) == 2


def test_prompt_embeds_system_prompt_and_problem(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_problem(problem="Compute 2+2.")]))
    task = MATH500Benchmark(problems_path=str(path)).tasks()[0]
    assert task["prompt"].startswith(SYSTEM_PROMPT)
    assert "Compute 2+2." in task["prompt"]


# ------------------------------------------------------------------------ evaluate


def test_evaluate_correct_boxed_answer_passes() -> None:
    bench = MATH500Benchmark()
    task = {"id": "p", "answer": "42"}
    assert bench.evaluate(task, r"Reasoning... \boxed{42}", env=None) is True


def test_evaluate_wrong_answer_fails() -> None:
    bench = MATH500Benchmark()
    task = {"id": "p", "answer": "42"}
    assert bench.evaluate(task, r"\boxed{7}", env=None) is False


def test_evaluate_no_extractable_answer_is_false() -> None:
    bench = MATH500Benchmark()
    assert bench.evaluate({"id": "p", "answer": "42"}, "no answer at all", env=None) is False


def test_evaluate_missing_ground_truth_is_false() -> None:
    bench = MATH500Benchmark()
    assert bench.evaluate({"id": "p"}, r"\boxed{42}", env=None) is False
