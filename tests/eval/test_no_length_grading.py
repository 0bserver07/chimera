"""No benchmark may grade correctness from the LENGTH of the agent's answer.

Five adapters (``swe_bench``, ``swt_bench``, ``swe_polybench``,
``feature_bench``, ``dpai_arena`` — six sites) fell back to
``len(agent_output.strip()) > 10`` when they could not run the benchmark's own
tests. A single sentence of prose therefore graded as a *solved* SWE-bench
instance, and the adapters disagreed about which configuration triggered it:
``swe_bench`` was safe with ``env=None`` and unsafe with a runner-less env,
``swt_bench`` was the exact mirror image.

This is the same defect class as a cloud sandbox silently degrading to local —
the result becomes indistinguishable from a real one. It also propagated by
imitation: ``dpai_arena._evaluate_rubric``'s docstring said its placeholder was
"matching the SWE-bench fallback behaviour". Hence two layers here: the
behavioural check below, and a static scan so a *new* adapter cannot copy the
pattern back in.

The honest failure mode is a zero, not a pass. A uniform-zero column is already
the harness-gap signature that ``scripts/render_observatory.py`` refuses to
publish as a score, so an ungradeable benchmark now surfaces as a visible gap
instead of an invented number.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from chimera.eval.benchmarks.dpai_arena import DPAIArena
from chimera.eval.benchmarks.feature_bench import FeatureBench
from chimera.eval.benchmarks.swe_bench import SWEBench
from chimera.eval.benchmarks.swe_polybench import SWEPolyBench
from chimera.eval.benchmarks.swt_bench import SWTBench

#: Answers that must never grade as a pass. Each is "substantive" by the old
#: heuristic (comfortably over 20 characters) and worthless as a solution.
PROSE_ANSWERS = [
    "I have analyzed the issue and implemented a comprehensive fix.",
    "The bug is in the date parsing logic. Fixed it.",
    "Done! Let me know if you need anything else.",
    "x" * 5000,
]


class _NoRunner:
    """An env object that exists but cannot execute anything.

    Reachable in practice via a stub/duck-typed env, and the configuration that
    made four of the five adapters grade prose as a pass.
    """

    def write_file(self, *args: object, **kwargs: object) -> None:
        return None


def _adapters() -> list[tuple[str, object, dict[str, object]]]:
    base = {
        "instance_id": "astropy__astropy-12907",
        "id": "astropy__astropy-12907",
        "test_patch": "",
        "fail_to_pass": [],
        "pass_to_pass": [],
    }
    return [
        ("swe-bench", SWEBench(), dict(base)),
        ("swt-bench", SWTBench(), dict(base)),
        ("swe-polybench", SWEPolyBench(), dict(base)),
        ("feature-bench", FeatureBench(), dict(base)),
        # Every dispatched track, plus a track with no grader at all.
        ("dpai-arena/pr-review", DPAIArena(), {**base, "track": "pr-review"}),
        ("dpai-arena/issue-to-patch", DPAIArena(), {**base, "track": "issue-to-patch"}),
        ("dpai-arena/unknown", DPAIArena(), {**base, "track": "no-such-track"}),
    ]


@pytest.mark.parametrize("answer", PROSE_ANSWERS, ids=lambda a: f"len{len(a)}")
def test_prose_never_grades_as_solved(answer: str) -> None:
    """Prose must not resolve an instance under ANY env shape.

    Parametrised over both configurations because the adapters disagreed about
    which one was unsafe — checking only one would have passed on four of the
    five while the fifth stayed broken.
    """
    failures = []
    for label, bench, task in _adapters():
        for env_label, env in (("env=None", None), ("env=no-runner", _NoRunner())):
            verdict = bench.evaluate(dict(task), answer, env=env)  # type: ignore[attr-defined]
            if verdict:
                failures.append(f"{label} [{env_label}] graded prose as RESOLVED")
    assert not failures, "length-as-correctness grading: " + "; ".join(failures)


def test_empty_answer_never_grades_as_solved() -> None:
    """The floor case, kept separate so a regression names itself clearly."""
    for label, bench, task in _adapters():
        for env in (None, _NoRunner()):
            assert not bench.evaluate(dict(task), "", env=env), label  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Static gate: the pattern must not come back by imitation.
# --------------------------------------------------------------------------

_BENCH_DIR = Path(__file__).resolve().parents[2] / "chimera" / "eval" / "benchmarks"

#: Parameters whose LENGTH must never decide a verdict. These name the agent's
#: answer; ``len()`` of one is a proxy for effort, never for correctness.
_ANSWER_NAMES = frozenset({"agent_output", "output", "answer", "response", "completion"})


class _LenOfAnswerVisitor(ast.NodeVisitor):
    """Collect ``len(<answer>)`` calls, however the answer is massaged first."""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def _names_in(self, node: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id == "len" and node.args:
            # Walks the argument so `len(agent_output.strip())`,
            # `len(agent_output.split())` and `len(x := agent_output)` all count.
            if self._names_in(node.args[0]) & _ANSWER_NAMES:
                self.hits.append(node.lineno)
        self.generic_visit(node)


def _length_grading_sites() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(_BENCH_DIR.glob("*.py")):
        visitor = _LenOfAnswerVisitor()
        visitor.visit(ast.parse(path.read_text()))
        if visitor.hits:
            found[path.name] = visitor.hits
    return found


def test_no_adapter_grades_on_answer_length() -> None:
    """AST-scanned, not grepped — the fix's own comments quote the old code.

    A text search would match those comments and force either a weakened
    pattern or the deletion of the explanation. The scan reads code only.
    """
    sites = _length_grading_sites()
    assert not sites, (
        "benchmark adapters must not derive a verdict from answer length: "
        + "; ".join(f"{f}:{lines}" for f, lines in sites.items())
    )


def test_the_static_gate_catches_a_real_violation() -> None:
    """Falsification: a gate that cannot fail is not a gate.

    Exercises the visitor directly against each spelling of the pattern,
    including the ``.strip()`` form the adapters actually used.
    """
    for src in (
        "def evaluate(self, task, agent_output, env=None):\n"
        "    return bool(agent_output and len(agent_output.strip()) > 10)\n",
        "def evaluate(self, task, output, env=None):\n"
        "    if len(output) > 20:\n        return True\n    return False\n",
        "def evaluate(self, task, agent_output, env=None):\n"
        "    return len(agent_output.split()) >= 3\n",
    ):
        visitor = _LenOfAnswerVisitor()
        visitor.visit(ast.parse(src))
        assert visitor.hits, f"gate missed a violation:\n{src}"

    # And it must not fire on legitimate len() use — otherwise the only way to
    # keep the suite green is to stop scanning.
    for benign in (
        "def evaluate(self, task, agent_output, env=None):\n"
        "    return len(task['fail_to_pass']) > 0\n",
        "def tasks(self):\n    return len(self._instances)\n",
    ):
        visitor = _LenOfAnswerVisitor()
        visitor.visit(ast.parse(benign))
        assert not visitor.hits, f"gate false-positived on:\n{benign}"
