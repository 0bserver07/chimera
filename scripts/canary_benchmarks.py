#!/usr/bin/env python3
"""Feed every benchmark adapter its own known-correct answer and check it scores.

A benchmark adapter can be completely broken while its unit tests are green,
because those tests assert that the adapter *runs*, never that a correct answer
*scores*. That gap has already produced three fabricated results in this repo:

* ``humaneval-x`` scored a live ``0/50`` with ``status_counts {completed: 50}``
  — every task ran cleanly and none passed — because the grader executed
  ``prompt + raw_reply + test`` and an instructed agent's Markdown prose died of
  ``SyntaxError`` before any assertion ran. The real score was ``50/50``.
* ``livecodebench`` grades 63 of 175 tasks against a contract they cannot
  satisfy, so no number it produces is a LiveCodeBench score.
* ``list_files`` used ``fnmatch`` (whose ``*`` crosses ``/``), so the same
  benchmark saw different files depending on the sandbox backend.

The canary is the cheap, general defence: take a task's **own canonical
solution**, hand it to ``evaluate()`` the way an agent would, and require a
pass. If a dataset's own reference answer cannot score, nothing an agent writes
will either, and every number from that adapter is fiction.

The inverse matters just as much. A grader that returns ``True`` unconditionally
would sail through the positive check, so each adapter is also fed wrong, empty
and prose-only answers and must reject all three. A canary that cannot fail is
not a canary.

Answers are submitted in the shapes agents actually emit — the dataset-native
shape, a fenced code block, and a fenced block wrapped in prose (what
``FINAL_ANSWER_CONTRACT`` asks matrix agents for). The humaneval-x zero hid for
a whole release precisely because the tests only ever fed the first shape.

Adapters are built through ``chimera.cli.main._load_benchmark`` — the same call
``chimera bench-matrix`` makes — so the canary exercises the configuration that
actually runs, not a hand-built stand-in.

Usage::

    python scripts/canary_benchmarks.py                 # every staged adapter
    python scripts/canary_benchmarks.py --bench mbpp    # just one
    python scripts/canary_benchmarks.py --limit 20      # deeper sample
    python scripts/canary_benchmarks.py --json          # machine-readable

Exit code is ``1`` if any adapter is BROKEN, else ``0``. NOT-STAGED and EXEMPT
never fail the run — they are reported, never silent, so an unaudited adapter is
visible rather than mistaken for a passing one.
"""
from __future__ import annotations

import argparse
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

# --- Verdicts ---------------------------------------------------------------
PASS = "PASS"
BROKEN = "BROKEN"
NOT_STAGED = "NOT-STAGED"
EXEMPT = "EXEMPT"
ERROR = "ERROR"
#: The canonical answer failed, but the task's own test file imports a module
#: this interpreter lacks — so the failure is the environment, not the grader.
#: Kept distinct from BROKEN because a false BROKEN sends someone to "fix" a
#: working adapter, and distinct from PASS because the adapter is unverified.
ENV_MISSING = "ENV-MISSING"


@dataclass
class Recipe:
    """How to build a known-correct answer for one benchmark.

    Attributes:
        answer: ``(task) -> str`` returning the dataset's own correct answer in
            its native shape, or ``None`` when the task carries no reference.
        wrong: ``(task) -> str`` returning an answer that must be REJECTED.
            Defaults to a plainly incorrect Python body.
        code_like: Whether fenced-code shapes are meaningful for this adapter.
            False for natural-language answers (a maths result), where wrapping
            the answer in ``python`` fences is not a shape any agent sends.
        test_fields: The task fields whose source the grader actually EXECUTES.
            Scanning the wrong field invents dependencies that are never
            imported: MBPP carries both a ``test`` blob and a ``test_list`` of
            assertions, and grades with ``test_list`` only — scanning ``test``
            reported a numpy requirement for tasks that never touch numpy.
        exempt: Why this adapter cannot be canaried offline, if it cannot.
    """

    answer: Callable[[dict[str, Any]], str | None] | None = None
    wrong: Callable[[dict[str, Any]], str] | None = None
    code_like: bool = True
    test_fields: tuple[str, ...] = ("test",)
    exempt: str = ""


#: Fields scanned for import requirements. Broader than the stub field a recipe
#: actually joins, because any of them may carry the imports the graded program
#: needs.
_STUB_FIELDS = ("code_prompt", "complete_prompt", "declaration", "prompt")


def _joined(stub_field: str) -> Callable[[dict[str, Any]], str | None]:
    """Build a getter for "code stub + canonical body" — the completion contract.

    The stub field is named EXPLICITLY per benchmark rather than guessed from a
    priority list, because guessing produced false BROKENs twice:

    * BigCodeBench's ``prompt`` is its natural-language ``instruct_prompt``, so
      joining it to a code body yields un-runnable source (the code stub is
      ``code_prompt``).
    * HumanEval-X carries a ``declaration`` field that looked like the obvious
      stub, but the staged data has it malformed for at least ``Python/142``
      (``'def sum_squares(lst):\n    "\n'`` — a stray unterminated quote). The
      adapter never reads ``declaration``; only the canary did, and it reported
      a perfectly good grader as broken.

    A canary that cries wolf gets someone to "fix" working code, so the mapping
    is data, not inference.

    Args:
        stub_field: The task key holding the code stub the body continues.

    Returns:
        A getter returning stub + canonical solution, or ``None``.
    """

    def get(task: dict[str, Any]) -> str | None:
        sol = task.get("canonical_solution")
        stub = task.get(stub_field)
        if not sol or not stub:
            return None
        return f"{stub}{sol}"

    return get


def _missing_imports(source: str) -> list[str]:
    """Top-level modules *source* imports that this interpreter cannot import.

    A grader that executes a task's test file inherits that file's dependencies.
    HumanEval+ tests ``import numpy``; without it every task fails and a naive
    canary reports the adapter BROKEN when the only broken thing is the
    environment. Distinguishing the two is the difference between a useful
    signal and a false alarm.

    Args:
        source: Python source to scan (a task's ``test`` field).

    Returns:
        Sorted distinct top-level module names that fail to import.
    """
    import ast
    import importlib.util

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    missing = []
    for root in roots:
        try:
            if importlib.util.find_spec(root) is None:
                missing.append(root)
        except (ImportError, ValueError, ModuleNotFoundError):
            missing.append(root)
    return sorted(missing)


def _field(name: str) -> Callable[[dict[str, Any]], str | None]:
    """Return a getter for a task field holding a standalone correct answer."""

    def get(task: dict[str, Any]) -> str | None:
        value = task.get(name)
        return str(value) if value else None

    return get


#: Per-benchmark recipes, keyed by the canonical CLI name. An adapter absent
#: from this table is reported ERROR (unclassified) rather than skipped — a new
#: benchmark must be given a recipe or an explicit exemption, never neither.
RECIPES: dict[str, Recipe] = {
    # --- self-contained code graders: canariable offline -------------------
    "human-eval": Recipe(answer=_joined("prompt")),
    "humaneval-plus": Recipe(answer=_joined("prompt")),
    # NOT "declaration" — the staged copy has it malformed for Python/142, and
    # the adapter does not read it.
    "humaneval-x": Recipe(answer=_joined("prompt")),
    # NOT "prompt" — that is BigCodeBench's natural-language instruct prompt.
    "bigcodebench": Recipe(answer=_joined("code_prompt")),
    "mbpp": Recipe(answer=_field("code"), test_fields=("test_list",)),
    "mbpp-plus": Recipe(answer=_field("code"), test_fields=("test_list",)),
    # --- natural-language answer graders -----------------------------------
    "aimo": Recipe(answer=_field("answer"), wrong=lambda t: "-99999", code_like=False),
    "math-500": Recipe(
        answer=_field("answer"), wrong=lambda t: "-99999", code_like=False
    ),
    # --- exempt: grading needs a checked-out repo and a real test runner ----
    "swe-bench": Recipe(exempt="gold patch needs a checked-out repo + test runner"),
    "swe-bench-verified": Recipe(
        exempt="gold patch needs a checked-out repo + test runner"
    ),
    "swe-polybench": Recipe(exempt="gold patch needs a checked-out repo + test runner"),
    "swt-bench": Recipe(exempt="gold patch needs a checked-out repo + test runner"),
    "multi-swe-bench": Recipe(
        exempt="gold patch needs a checked-out repo + test runner"
    ),
    "senior-swe-bench": Recipe(
        exempt="gold patch needs a checked-out repo + test runner"
    ),
    "swe-lancer": Recipe(exempt="gold patch needs a checked-out repo + test runner"),
    # --- exempt: no reference answer exists in the dataset ------------------
    "livecodebench": Recipe(
        exempt="dataset stages no canonical solution; adapter is RETRACTED "
        "(see scripts/render_observatory.py RETRACTED)"
    ),
    "tau-bench": Recipe(exempt="agentic — grading replays actions against a sim env"),
    "webarena": Recipe(exempt="agentic — grading needs a live browser environment"),
    "nocha": Recipe(exempt="long-context QA — claims are graded, no reference answer"),
    "harbor": Recipe(exempt="delegating adapter — grading is the upstream harness's"),
    "programbench": Recipe(exempt="submission-contract bench — grading fetches deps"),
    "dpai-arena": Recipe(exempt="agentic arena — no per-task reference answer"),
    "context-bench": Recipe(exempt="agentic — retrieval behaviour, no reference answer"),
    "cline-bench": Recipe(exempt="repo-task bench — needs a checked-out workspace"),
    "feature-bench": Recipe(exempt="repo-task bench — needs a checked-out workspace"),
    "aider-polyglot": Recipe(exempt="repo-task bench — needs a checked-out workspace"),
    "custom": Recipe(exempt="user-supplied tasks — no fixed dataset to canary"),
}


def _shapes(answer: str, code_like: bool) -> list[tuple[str, str]]:
    """The submission shapes an agent actually produces, named for reporting."""
    shapes = [("native", answer)]
    if code_like:
        shapes.append(("fenced", f"```python\n{answer}\n```"))
        shapes.append(
            (
                "fenced+prose",
                "Here's the implementation:\n\n"
                f"```python\n{answer}\n```\n\n"
                "It handles the described cases.",
            )
        )
    return shapes


#: Tasks confirmed **unpassable by construction** — the dataset's own reference
#: answer cannot satisfy the dataset's own test, for a reason inside the staged
#: data rather than in Chimera's grader. Listing one here is a disclosure, not a
#: dismissal: it caps the benchmark's achievable score, and that cap must travel
#: with any number published from it.
#:
#: Each entry needs evidence, because "known bad" is exactly the label a real
#: bug would love to hide behind. Verified by reading the staged bytes, not
#: inferred from a failure.
KNOWN_UNPASSABLE: dict[str, dict[str, str]] = {
    "humaneval-plus": {
        "HumanEval/32": (
            "upstream EvalPlus data: the final assertion is "
            "`_poly(*candidate(*inp), inp)`, which splats the FLOAT that "
            "find_zero returns and dies of TypeError before comparing "
            "anything. Present verbatim in the raw HF rows "
            "(evalplus/humanevalplus), so it is not a staging artifact. "
            "Caps humaneval-plus at 163/164 = 99.4%."
        ),
    },
}


@dataclass
class Result:
    """One adapter's canary outcome."""

    bench: str
    verdict: str
    checked: int = 0
    detail: str = ""
    failures: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)


def _task_blockers(task: dict, answer: str | None, recipe: "Recipe") -> list[str]:
    """Modules the WHOLE executed program needs but this interpreter lacks.

    The grader runs stub + solution + test, so all three are scanned.
    ``BigCodeBench/3`` imports numpy in its *solution stub* while its test
    imports only ``unittest`` — checking the test alone would have reported that
    task as a grader bug when the environment was the only thing missing.
    """
    missing: set[str] = set()
    sources = [str(answer or "")]
    sources.extend(str(task.get(f) or "") for f in _STUB_FIELDS)
    for name in recipe.test_fields:
        value = task.get(name)
        # A test field may be a blob of source or a list of assert strings.
        sources.extend(str(v) for v in value) if isinstance(value, list) else (
            sources.append(str(value or ""))
        )
    for src in sources:
        missing.update(_missing_imports(src))
    # ``libs`` is a hint, not source, and is not always a real list — BigCodeBench
    # stores it as the STRING "['random', 'itertools']", so iterating it yields
    # single characters and invents modules named "a", "d", "e"...
    libs = task.get("libs")
    if isinstance(libs, str):
        try:
            import ast as _ast

            libs = _ast.literal_eval(libs)
        except (ValueError, SyntaxError):
            libs = []
    for lib in libs or []:
        if isinstance(lib, str) and lib.isidentifier():
            missing.update(_missing_imports(f"import {lib}"))
    return sorted(missing)


def _canary_one(name: str, recipe: Recipe, limit: int) -> Result:
    """Run the canary for a single benchmark."""
    from chimera.cli.main import _load_benchmark

    if recipe.exempt:
        return Result(name, EXEMPT, detail=recipe.exempt)

    try:
        bench = _load_benchmark(name, limit=limit)
        tasks = bench.tasks()
    except Exception as exc:
        return Result(name, NOT_STAGED, detail=f"{type(exc).__name__}: {exc}"[:120])
    if not tasks:
        return Result(name, NOT_STAGED, detail="0 tasks staged")

    assert recipe.answer is not None  # non-exempt recipes always have one
    wrong_of = recipe.wrong or (lambda t: "    return None  # deliberately wrong\n")

    failures: list[str] = []
    blocked: dict[str, list[str]] = {}
    excluded: list[str] = []
    known_bad = KNOWN_UNPASSABLE.get(name, {})
    checked = 0
    for task in tasks[:limit]:
        tid = str(task.get("id") or task.get("task_id") or "?")
        answer = recipe.answer(task)
        if answer is None:
            failures.append(f"{tid}: no reference answer in task")
            continue

        # Skip tasks this interpreter cannot run at all. Judged PER TASK, not
        # per adapter: one numpy-dependent task must not invalidate the verdict
        # on the others, and must not be counted as verified either.
        needs = _task_blockers(task, answer, recipe)
        if needs:
            blocked[tid] = needs
            continue
        if tid in known_bad:
            excluded.append(tid)
            continue
        checked += 1

        # Positive: the dataset's own answer must pass, in every shape.
        for shape, submission in _shapes(answer, recipe.code_like):
            try:
                ok = bench.evaluate(task, submission, None)
            except Exception as exc:
                failures.append(
                    f"{tid} [{shape}]: raised {type(exc).__name__}: {exc}"[:150]
                )
                continue
            if not ok:
                failures.append(f"{tid} [{shape}]: CORRECT answer graded as FAIL")

        # Negative: a grader that always passes is itself the bug.
        for label, bad in (
            ("wrong", wrong_of(task)),
            ("empty", ""),
            ("prose-only", "I think the answer is left as an exercise."),
        ):
            try:
                ok = bench.evaluate(task, bad, None)
            except Exception:
                continue  # raising on garbage is fine; silently passing is not
            if ok:
                failures.append(f"{tid} [{label}]: WRONG answer graded as PASS")

    skipped = ""
    if blocked:
        mods = sorted({m for v in blocked.values() for m in v})
        skipped = (
            f"{len(blocked)}/{len(tasks[:limit])} task(s) skipped — need "
            f"{', '.join(mods)}"
        )

    if failures:
        return Result(
            name, BROKEN, checked=checked, failures=failures, detail=skipped,
            excluded=excluded,
        )
    if checked == 0:
        if blocked:
            return Result(
                name, ENV_MISSING, detail=skipped or "no runnable task",
                excluded=excluded,
            )
        if excluded:
            # Every sampled task is a disclosed exclusion. Nothing was verified,
            # so this is not a PASS — but the disclosure must survive, or the
            # adapter looks merely unstaged.
            return Result(
                name, NOT_STAGED,
                detail=f"all {len(excluded)} sampled task(s) unpassable by construction",
                excluded=excluded,
            )
        return Result(name, NOT_STAGED, detail="no task carried a reference answer")
    return Result(name, PASS, checked=checked, detail=skipped, excluded=excluded)


def canary(names: list[str], limit: int) -> list[Result]:
    """Run the canary across *names*, in order."""
    results: list[Result] = []
    for name in names:
        recipe = RECIPES.get(name)
        if recipe is None:
            results.append(
                Result(
                    name,
                    ERROR,
                    detail="no canary recipe — add one to RECIPES or mark it exempt",
                )
            )
            continue
        try:
            results.append(_canary_one(name, recipe, limit))
        except Exception:
            results.append(
                Result(name, ERROR, detail=traceback.format_exc(limit=2)[-160:])
            )
    return results


def _canonical_names() -> list[str]:
    """Every registered benchmark once, de-aliased, in stable order."""
    from chimera.cli.main import _BENCHMARKS

    seen: dict[str, str] = {}
    for name in sorted(_BENCHMARKS):
        target = _BENCHMARKS[name]
        # Prefer the spelling that has a recipe, else the first (shortest) alias.
        if target not in seen or name in RECIPES:
            if target not in seen or seen[target] not in RECIPES:
                seen[target] = name
    return sorted(seen.values())


def format_text(results: list[Result]) -> str:
    """Render a human-readable canary report."""
    order = {BROKEN: 0, ERROR: 1, ENV_MISSING: 2, PASS: 3, NOT_STAGED: 4, EXEMPT: 5}
    rows = sorted(results, key=lambda r: (order.get(r.verdict, 9), r.bench))
    width = max((len(r.bench) for r in rows), default=10) + 1
    tally = {v: sum(1 for r in results if r.verdict == v) for v in order}

    lines = ["Known-correct-answer canary — every adapter graded against its own reference", ""]
    for r in rows:
        mark = {PASS: "✅", BROKEN: "❌", ERROR: "⚠️ ", ENV_MISSING: "🔧",
                NOT_STAGED: "· ", EXEMPT: "— "}[r.verdict]
        extra = f" ({r.checked} tasks)" if r.checked else ""
        note = f"  {r.detail}" if r.detail else ""
        lines.append(f"  {mark} {r.bench:<{width}} {r.verdict:<11}{extra}{note}")
        for tid in r.excluded:
            why = KNOWN_UNPASSABLE.get(r.bench, {}).get(tid, "")
            lines.append(f"        ⊘ {tid} excluded — unpassable by construction: {why}")
        for f in r.failures[:6]:
            lines.append(f"        ↳ {f}")
        if len(r.failures) > 6:
            lines.append(f"        ↳ … and {len(r.failures) - 6} more")
    lines.extend(
        [
            "",
            f"{tally[PASS]} pass · {tally[BROKEN]} BROKEN · {tally[ERROR]} unclassified · "
            f"{tally[ENV_MISSING]} env-missing · {tally[NOT_STAGED]} not staged · "
            f"{tally[EXEMPT]} exempt",
        ]
    )
    if tally[BROKEN]:
        lines.extend(
            [
                "",
                "A BROKEN adapter cannot produce a real score. Any number already",
                "published from one must be retracted, not re-run — see the RETRACTED",
                "registry in scripts/render_observatory.py.",
            ]
        )
    if tally[ENV_MISSING]:
        lines.extend(
            [
                "",
                "ENV-MISSING is not a pass either: those graders are UNVERIFIED here.",
                "Install the named modules and re-run — the canary deliberately refuses",
                "to call an adapter broken when the environment is what is missing.",
            ]
        )
    if tally[NOT_STAGED]:
        lines.extend(
            [
                "",
                "NOT-STAGED is not a pass. Stage the dataset (`chimera bench-fetch",
                "<name>`) and re-run before trusting any column from those adapters.",
            ]
        )
    return "\n".join(lines)


def format_json(results: list[Result]) -> str:
    """Render the canary report as a stable JSON object."""
    return json.dumps(
        {
            "summary": {
                v: sum(1 for r in results if r.verdict == v)
                for v in (PASS, BROKEN, ERROR, ENV_MISSING, NOT_STAGED, EXEMPT)
            },
            "results": [
                {
                    "bench": r.bench,
                    "verdict": r.verdict,
                    "checked": r.checked,
                    "detail": r.detail,
                    "failures": r.failures,
                    "excluded": r.excluded,
                }
                for r in sorted(results, key=lambda r: r.bench)
            ],
        },
        indent=2,
        sort_keys=True,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns ``1`` when any adapter is BROKEN."""
    parser = argparse.ArgumentParser(
        description="Grade every benchmark adapter against its own canonical answer.",
    )
    parser.add_argument("--bench", action="append", help="only these benchmarks")
    parser.add_argument(
        "--limit", type=int, default=5, help="tasks per adapter (default 5)"
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    names = args.bench or _canonical_names()
    results = canary(names, args.limit)

    print(format_json(results) if args.json else format_text(results))
    return 1 if any(r.verdict in (BROKEN, ERROR) for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
