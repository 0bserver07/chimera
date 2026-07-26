"""Tests for ``scripts/canary_benchmarks.py`` — the known-correct-answer canary.

The canary grades every benchmark adapter against its own reference answer, so
these tests guard the guard. Two failure modes matter equally:

* a **missed** break — the canary passes an adapter that cannot score;
* a **false** break — the canary calls a working adapter broken, which sends
  someone to "fix" correct code. Both of the canary's own early bugs were this
  kind (a stub field guessed wrong, a dependency scan reading a field the
  grader never executes), so the false-positive paths are pinned explicitly.

Everything here is hermetic: the pure helpers take strings, and the end-to-end
paths drive a fake benchmark. Nothing reads ``~/.chimera/datasets`` or the
network, so the tests run in CI where no dataset is staged.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "canary_benchmarks.py"
_spec = importlib.util.spec_from_file_location("canary_benchmarks", _SCRIPT)
assert _spec is not None and _spec.loader is not None
cb = importlib.util.module_from_spec(_spec)
sys.modules["canary_benchmarks"] = cb  # dataclasses resolve via sys.modules
_spec.loader.exec_module(cb)


# --------------------------------------------------------------- answer shapes
class TestSubmissionShapes:
    def test_code_like_covers_the_shapes_agents_actually_send(self) -> None:
        names = [n for n, _ in cb._shapes("BODY", code_like=True)]
        assert names == ["native", "fenced", "fenced+prose"]

    def test_fenced_and_prose_shapes_carry_the_answer(self) -> None:
        for _, text in cb._shapes("BODY", code_like=True):
            assert "BODY" in text

    def test_prose_answers_skip_the_code_fences(self) -> None:
        # Wrapping "42" in ```python fences is not a shape any agent sends for
        # a maths answer; testing it would assert a contract nobody has.
        assert [n for n, _ in cb._shapes("42", code_like=False)] == ["native"]


# ------------------------------------------------------------ stub resolution
class TestJoinedStub:
    def test_joins_the_named_stub_field(self) -> None:
        get = cb._joined("code_prompt")
        task = {"code_prompt": "def f():\n", "canonical_solution": "    return 1\n"}
        assert get(task) == "def f():\n    return 1\n"

    def test_returns_none_without_a_reference_answer(self) -> None:
        assert cb._joined("prompt")({"prompt": "def f():\n"}) is None

    def test_returns_none_when_the_named_stub_is_absent(self) -> None:
        # Silently falling back to another field is what produced a false
        # BROKEN on humaneval-x (its `declaration` is malformed in the staged
        # data and the adapter never reads it). No guessing.
        get = cb._joined("code_prompt")
        assert get({"prompt": "def f():\n", "canonical_solution": "  return 1\n"}) is None


# ----------------------------------------------------- dependency attribution
class TestMissingImports:
    def test_reports_an_absent_module(self) -> None:
        assert "definitely_not_a_real_module_xyz" in cb._missing_imports(
            "import definitely_not_a_real_module_xyz"
        )

    def test_ignores_importable_modules(self) -> None:
        assert cb._missing_imports("import json\nfrom pathlib import Path") == []

    def test_handles_from_imports_and_dotted_names(self) -> None:
        out = cb._missing_imports("from nope_pkg_xyz.sub import thing")
        assert out == ["nope_pkg_xyz"]

    def test_relative_imports_are_not_module_requirements(self) -> None:
        assert cb._missing_imports("from . import sibling") == []

    def test_unparseable_source_is_not_a_dependency_claim(self) -> None:
        assert cb._missing_imports("this is prose, not python (((") == []


class TestTaskBlockers:
    def test_scans_the_executed_test_field_only(self) -> None:
        # MBPP carries BOTH a `test` blob and a `test_list`, and grades with
        # `test_list`. Scanning `test` invented a numpy requirement for tasks
        # that never touch numpy — a false ENV-MISSING on a working adapter.
        recipe = cb.Recipe(answer=cb._field("code"), test_fields=("test_list",))
        task = {
            "code": "def f(): return 1",
            "test": "import numpy_absent_xyz",       # NOT executed by the grader
            "test_list": ["assert f() == 1"],         # what actually runs
        }
        assert cb._task_blockers(task, task["code"], recipe) == []

    def test_flags_a_dependency_of_the_executed_test(self) -> None:
        recipe = cb.Recipe(answer=cb._field("code"), test_fields=("test_list",))
        task = {"code": "x=1", "test_list": ["import numpy_absent_xyz"]}
        assert cb._task_blockers(task, task["code"], recipe) == ["numpy_absent_xyz"]

    def test_flags_a_dependency_imported_only_by_the_solution_stub(self) -> None:
        # BigCodeBench/3 imports numpy in its *stub*; its test imports only
        # unittest. Scanning the test alone would have called that a grader bug.
        recipe = cb.Recipe(answer=cb._joined("code_prompt"))
        task = {
            "code_prompt": "import numpy_absent_xyz\ndef task_func():\n",
            "canonical_solution": "    return 1\n",
            "test": "import unittest",
        }
        answer = recipe.answer(task)  # type: ignore[misc]
        assert "numpy_absent_xyz" in cb._task_blockers(task, answer, recipe)

    def test_stringified_libs_do_not_become_single_letter_modules(self) -> None:
        # BigCodeBench stores libs as the STRING "['random', 'itertools']".
        # Iterating it yields characters, inventing modules "a", "d", "e"…
        recipe = cb.Recipe(answer=cb._field("code"), test_fields=("test",))
        task = {"code": "x=1", "test": "assert True", "libs": "['random', 'itertools']"}
        blockers = cb._task_blockers(task, "x=1", recipe)
        assert blockers == [], f"single-character modules leaked: {blockers}"

    def test_real_list_libs_are_still_honoured(self) -> None:
        recipe = cb.Recipe(answer=cb._field("code"), test_fields=("test",))
        task = {"code": "x=1", "test": "assert True", "libs": ["numpy_absent_xyz"]}
        assert cb._task_blockers(task, "x=1", recipe) == ["numpy_absent_xyz"]


# --------------------------------------------------------------- end-to-end
class _FakeBench:
    """A benchmark whose grader behaviour is dictated by the test."""

    def __init__(self, tasks: list[dict], grade) -> None:
        self._tasks, self._grade = tasks, grade

    def tasks(self) -> list[dict]:
        return self._tasks

    def evaluate(self, task: dict, output: str, env=None) -> bool:
        return self._grade(task, output)


def _run(monkeypatch, name, recipe, tasks, grade, limit=5):
    """Drive ``_canary_one`` against a fake benchmark."""
    import chimera.cli.main as main

    monkeypatch.setattr(main, "_load_benchmark", lambda *a, **k: _FakeBench(tasks, grade))
    monkeypatch.setitem(cb.RECIPES, name, recipe)
    return cb._canary_one(name, recipe, limit)


_TASK = {"id": "T1", "prompt": "def f():\n", "canonical_solution": "    return 1\n",
         "test": "assert True"}


class TestVerdicts:
    def test_correct_answer_accepted_and_wrong_rejected_is_a_pass(self, monkeypatch) -> None:
        good = _TASK["prompt"] + _TASK["canonical_solution"]
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [_TASK],
                 lambda t, out: good in out)
        assert r.verdict == cb.PASS and r.checked == 1

    def test_grader_rejecting_the_reference_answer_is_BROKEN(self, monkeypatch) -> None:
        # The humaneval-x zero: every task ran clean, nothing passed.
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [_TASK],
                 lambda t, out: False)
        assert r.verdict == cb.BROKEN
        assert any("CORRECT answer graded as FAIL" in f for f in r.failures)

    def test_always_true_grader_is_BROKEN_not_PASS(self, monkeypatch) -> None:
        # The inverse check: a grader that passes everything would sail through
        # a positive-only canary. A canary that cannot fail is not a canary.
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [_TASK],
                 lambda t, out: True)
        assert r.verdict == cb.BROKEN
        assert any("WRONG answer graded as PASS" in f for f in r.failures)
        assert any("empty" in f for f in r.failures)

    def test_grader_that_raises_is_BROKEN(self, monkeypatch) -> None:
        def boom(task, out):
            raise RuntimeError("grader exploded")

        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [_TASK], boom)
        assert r.verdict == cb.BROKEN
        assert any("raised RuntimeError" in f for f in r.failures)

    def test_missing_dependency_is_ENV_MISSING_not_BROKEN(self, monkeypatch) -> None:
        task = dict(_TASK, test="import numpy_absent_xyz")
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [task],
                 lambda t, out: False)
        assert r.verdict == cb.ENV_MISSING
        assert "numpy_absent_xyz" in r.detail
        assert r.checked == 0  # unverified, and not counted as verified

    def test_env_blocked_task_does_not_invalidate_its_neighbours(self, monkeypatch) -> None:
        # One numpy-dependent task must not condemn the whole adapter.
        blocked = dict(_TASK, id="T2", test="import numpy_absent_xyz")
        good = _TASK["prompt"] + _TASK["canonical_solution"]
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")),
                 [_TASK, blocked], lambda t, out: good in out)
        assert r.verdict == cb.PASS
        assert r.checked == 1
        assert "1/2 task(s) skipped" in r.detail

    def test_empty_dataset_is_NOT_STAGED_never_PASS(self, monkeypatch) -> None:
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [],
                 lambda t, out: True)
        assert r.verdict == cb.NOT_STAGED

    def test_exempt_adapters_report_their_reason(self) -> None:
        r = cb._canary_one("x", cb.Recipe(exempt="needs a live browser"), 5)
        assert r.verdict == cb.EXEMPT and "live browser" in r.detail

    def test_known_unpassable_task_is_excluded_and_disclosed(self, monkeypatch) -> None:
        monkeypatch.setitem(cb.KNOWN_UNPASSABLE, "x", {"T1": "upstream test is malformed"})
        r = _run(monkeypatch, "x", cb.Recipe(answer=cb._joined("prompt")), [_TASK],
                 lambda t, out: False)
        # Excluded rather than BROKEN — but never silently.
        assert r.verdict != cb.BROKEN
        assert r.excluded == ["T1"]


# ------------------------------------------------------------------ coverage
class TestEveryBenchmarkIsClassified:
    def test_every_registered_benchmark_has_a_recipe_or_an_exemption(self) -> None:
        """A new adapter must be canaried or explicitly excused — never neither.

        This is the guard that keeps the audit honest over time: without it, a
        benchmark added next month is silently unaudited while the canary still
        reports all-green.
        """
        from chimera.cli.main import _BENCHMARKS

        covered = {
            name
            for name in _BENCHMARKS
            if name in cb.RECIPES
        }
        by_target: dict[str, list[str]] = {}
        for name, target in _BENCHMARKS.items():
            by_target.setdefault(target, []).append(name)
        missing = [
            sorted(aliases)[0]
            for target, aliases in by_target.items()
            if not any(a in covered for a in aliases)
        ]
        assert not missing, (
            "benchmarks with no canary recipe and no exemption: "
            f"{sorted(missing)} — add them to RECIPES in scripts/canary_benchmarks.py"
        )

    def test_exemptions_state_a_reason(self) -> None:
        for name, recipe in cb.RECIPES.items():
            if recipe.exempt:
                assert len(recipe.exempt) > 15, f"{name}: exemption reason too thin"
            else:
                assert recipe.answer is not None, f"{name}: no answer builder"

    def test_known_unpassable_entries_carry_evidence(self) -> None:
        # "known bad" is the perfect hiding place for a real bug, so an entry
        # must explain itself well enough to be re-checked.
        for bench, tasks in cb.KNOWN_UNPASSABLE.items():
            for tid, why in tasks.items():
                assert len(why) > 80, f"{bench}/{tid}: reason too thin to verify"


# ------------------------------------------------------------------ reporting
class TestReporting:
    def test_text_report_names_broken_adapters_and_the_consequence(self) -> None:
        results = [cb.Result("b1", cb.BROKEN, failures=["T1: CORRECT answer graded as FAIL"])]
        text = cb.format_text(results)
        assert "b1" in text and "BROKEN" in text
        assert "retracted" in text.lower()

    def test_not_staged_is_never_reported_as_a_pass(self) -> None:
        text = cb.format_text([cb.Result("b1", cb.NOT_STAGED, detail="0 tasks")])
        assert "NOT-STAGED is not a pass" in text

    def test_env_missing_is_never_reported_as_a_pass(self) -> None:
        text = cb.format_text([cb.Result("b1", cb.ENV_MISSING, detail="needs numpy")])
        assert "not a pass either" in text

    def test_json_report_is_valid_and_shaped(self) -> None:
        payload = json.loads(
            cb.format_json([cb.Result("b1", cb.PASS, checked=3)])
        )
        assert payload["summary"][cb.PASS] == 1
        assert payload["results"][0]["bench"] == "b1"
        assert payload["results"][0]["checked"] == 3
