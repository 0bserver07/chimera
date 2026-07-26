"""LiveCodeBench adapter — contamination-free competitive programming.

LiveCodeBench (Jain et al., 2024) continuously harvests fresh problems from
LeetCode, AtCoder, and CodeForces. Each problem is timestamped, so callers
can restrict evaluation to problems released *after* a model's training
cutoff — eliminating the data leakage that plagues HumanEval/MBPP.

**Problem rotation** is the load-bearing feature: the dataset grows over
time, and a contamination-free score requires picking a date window the
model has never seen. Two helpers support this:

  * ``LiveCodeBench(start_date=..., end_date=...)`` — explicit window.
  * ``LiveCodeBench.rotated_window(model_cutoff=..., months=3)`` — pick
    a fresh slice relative to a known training cutoff.

Scenarios supported (per upstream): ``codegeneration``, ``selfrepair``,
``codeexecution``, ``testoutput``. Only ``codegeneration`` is wired up
here; the others raise NotImplementedError until the upstream JSON schema
is loaded.

Reference: https://github.com/LiveCodeBench/LiveCodeBench
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from chimera.eval.harness import Benchmark

#: Matches the entry-point method in a LeetCode-style ``starter_code`` stub,
#: e.g. ``def zigzagTraversal(self, grid: List[List[int]]) -> List[int]:``.
_STARTER_METHOD = re.compile(r"^\s*def\s+(\w+)\s*\(\s*self\b", re.MULTILINE)


def _is_functional(test_cases: list[dict[str, Any]]) -> bool:
    """Whether these cases use the call-a-method contract rather than stdin.

    The dataset labels every case, so the contract is read, never inferred from
    the shape of the solution.
    """
    return any(
        isinstance(c, dict) and c.get("testtype") == "functional" for c in test_cases
    )


def _entry_point(starter_code: str) -> str:
    """The method name a functional task must be graded through, or ``""``."""
    match = _STARTER_METHOD.search(starter_code or "")
    return match.group(1) if match else ""


def _stratified_head(tasks: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Take *limit* tasks spread across platforms, not the first *limit* rows.

    The staged file is **platform-blocked**: AtCoder occupies rows 0–111 and
    LeetCode 112–174. A contiguous head slice is therefore single-platform for
    any ``limit <= 112`` — ``--limit 50`` sampled AtCoder exclusively while
    reporting itself as "livecodebench". That is not a small sample of the
    benchmark; it is all of one half and none of the other, and the two halves
    use different grading contracts.

    Round-robin over platform groups (each group keeping dataset order) so a
    small ``--limit`` is a proportional cross-section. Deterministic — no RNG,
    so a run is reproducible from its arguments alone.

    Args:
        tasks: Filtered tasks in dataset order.
        limit: Maximum number to return.

    Returns:
        At most *limit* tasks, drawn round-robin across platforms.
    """
    if limit >= len(tasks):
        return tasks
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        groups.setdefault(str(task.get("platform") or ""), []).append(task)
    if len(groups) < 2:
        return tasks[:limit]
    picked: list[dict[str, Any]] = []
    order = sorted(groups)
    index = 0
    while len(picked) < limit:
        progressed = False
        for key in order:
            group = groups[key]
            if index < len(group):
                picked.append(group[index])
                progressed = True
                if len(picked) == limit:
                    break
        if not progressed:
            break
        index += 1
    return picked


#: Driver for the functional contract. Executed in the sandbox next to the
#: agent's ``solution.py``.
#:
#: Design notes, each of which is a way this could silently over-report:
#: * ``typing`` names are injected **before** the solution executes, not after.
#:   LeetCode stubs annotate with bare ``List``/``Optional``, and those
#:   annotations are evaluated while the class body runs — so importing the
#:   module first and patching names onto it afterwards is too late, and a
#:   *correct* solution dies of ``NameError`` and grades 0. Exactly the kind of
#:   fabricated zero this fix exists to remove, so the solution source is
#:   exec'd into a pre-populated namespace instead of imported.
#: * Arguments are newline-separated JSON values, matching the dataset's own
#:   encoding — a multi-argument problem has one JSON value per line.
#: * The verdict is an explicit printed token. Exit status alone would let a
#:   solution that never ran the comparison pass.
#: * Comparison is ``==`` on decoded values, not string equality, so ``[1, 4]``
#:   and ``[1,4]`` agree — formatting is not correctness.
_FUNCTIONAL_DRIVER = '''\
import json, sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_ns = {{
    "Any": Any, "Callable": Callable, "Dict": Dict, "Iterable": Iterable,
    "List": List, "Optional": Optional, "Sequence": Sequence, "Set": Set,
    "Tuple": Tuple, "__name__": "solution",
}}
with open("solution.py") as _f:
    _src = _f.read()
try:
    exec(compile(_src, "solution.py", "exec"), _ns)
except Exception as _exc:
    print("__LCB_IMPORT_FAILED__", type(_exc).__name__)
    sys.exit(1)

_Solution = _ns.get("Solution")
if _Solution is None:
    print("__LCB_NO_SOLUTION_CLASS__")
    sys.exit(1)

_fn = getattr(_Solution(), "{method}", None)
if _fn is None:
    print("__LCB_NO_METHOD__")
    sys.exit(1)

with open("_cases.json") as _f:
    _cases = json.load(_f)

for _case in _cases:
    _args = [json.loads(l) for l in str(_case.get("input", "")).splitlines() if l.strip()]
    _expected = json.loads(_case.get("output", "null"))
    try:
        _got = _fn(*_args)
    except Exception as _exc:
        print("__LCB_RAISED__", type(_exc).__name__)
        sys.exit(1)
    if _got != _expected:
        print("__LCB_MISMATCH__")
        sys.exit(1)

print("__LCB_PASS__")
'''

_VALID_SCENARIOS = ("codegeneration", "selfrepair", "codeexecution", "testoutput")
_VALID_DIFFICULTIES = ("easy", "medium", "hard")


@dataclass(frozen=True)
class DateWindow:
    """Inclusive [start, end] window for problem-rotation filtering."""

    start: date
    end: date

    def contains(self, when: date) -> bool:
        return self.start <= when <= self.end


class LiveCodeBench(Benchmark):
    """Contamination-free competitive programming benchmark.

    Args:
        scenario: One of ``codegeneration`` (default), ``selfrepair``,
            ``codeexecution``, ``testoutput``.
        start_date: ISO date string (``YYYY-MM-DD``). Problems released
            before this date are skipped. Pair with model training
            cutoff to guarantee zero contamination.
        end_date: ISO date string. Problems after this date are skipped.
        difficulty: Optional filter — ``easy``, ``medium``, or ``hard``.
        release_version: Upstream release tag (e.g. ``release_v6``).
        dataset_path: Local path to a JSON dump of LiveCodeBench problems.
            If unset, ``tasks()`` returns an empty list (callers must
            install the upstream package and provide data).
        limit: Cap on number of tasks returned.
    """

    def __init__(
        self,
        scenario: str = "codegeneration",
        start_date: str | None = None,
        end_date: str | None = None,
        difficulty: str | None = None,
        release_version: str = "release_v6",
        dataset_path: str | None = None,
        limit: int | None = None,
    ) -> None:
        if scenario not in _VALID_SCENARIOS:
            raise ValueError(
                f"scenario must be one of {_VALID_SCENARIOS}, got {scenario!r}"
            )
        if difficulty is not None and difficulty not in _VALID_DIFFICULTIES:
            raise ValueError(
                f"difficulty must be one of {_VALID_DIFFICULTIES}, got {difficulty!r}"
            )
        self._scenario = scenario
        self._window = self._parse_window(start_date, end_date)
        self._difficulty = difficulty
        self._release_version = release_version
        self._dataset_path = dataset_path
        self._limit = limit
        self._tasks: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------ Benchmark API

    def name(self) -> str:
        suffix = f"-{self._scenario}"
        if self._window:
            suffix += f"-{self._window.start.isoformat()}_{self._window.end.isoformat()}"
        return f"livecodebench{suffix}"

    def tasks(self) -> list[dict[str, Any]]:
        if self._tasks is None:
            self._tasks = self._load_and_filter()
        return self._tasks

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        """Run agent_output against the problem's test cases.

        LiveCodeBench mixes **two grading contracts** and the task says which:

        * ``testtype: "stdin"`` (AtCoder here) — a whole program reading stdin
          and writing stdout, compared as trimmed text.
        * ``testtype: "functional"`` (LeetCode here) — a ``Solution`` class whose
          method is *called* with JSON-decoded arguments, compared as values.

        Running everything through ``python solution.py < stdin`` graded the
        functional half against a contract it cannot satisfy: those solutions
        define a class and print nothing, so they scored 0 no matter how correct
        they were. That is 63 of the 175 staged tasks — 36% of the denominator
        unpassable by construction, which is why the published column was
        retracted rather than merely caveated.

        Requires an executable env (Docker/Local); there is no in-process
        fallback because solutions use ``input()`` / ``print()``.
        """
        if self._scenario != "codegeneration":
            raise NotImplementedError(
                f"evaluate() for scenario={self._scenario!r} is not yet wired. "
                "Only 'codegeneration' is implemented."
            )
        if env is None:
            return False
        test_cases = task.get("test_cases") or task.get("public_test_cases") or []
        if not test_cases:
            return False

        # Normalize markdown-fenced answers to bare source (see _code_extract).
        from chimera.eval.benchmarks._code_extract import extract_code

        solution = extract_code(agent_output)
        if not solution.strip():
            # An errored or empty agent run has no program to execute. An empty
            # ``solution.py`` exits 0 and — for a test case whose expected
            # output is empty — its empty stdout would spuriously match, so an
            # empty solution must never grade as a pass (measurement integrity).
            return False

        if _is_functional(test_cases):
            return self._evaluate_functional(task, solution, test_cases, env)
        return self._evaluate_stdin(solution, test_cases, env)

    def _evaluate_stdin(
        self, solution: str, test_cases: list[dict[str, Any]], env: Any
    ) -> bool:
        """Grade a whole-program solution against (stdin, stdout) pairs."""
        env.write_file("solution.py", solution)
        for case in test_cases:
            stdin = case.get("input", "")
            expected = (case.get("output", "") or "").strip()
            # No Environment implementation takes a ``stdin=`` kwarg; every one
            # runs commands through a shell, so feed stdin via a file redirect
            # (portable across Local / Docker / SSH envs).
            env.write_file("_stdin.txt", stdin)
            result = env.run_command("python solution.py < _stdin.txt")
            if result.exit_code != 0:
                return False
            if (result.stdout or "").strip() != expected:
                return False
        return True

    def _evaluate_functional(
        self,
        task: dict[str, Any],
        solution: str,
        test_cases: list[dict[str, Any]],
        env: Any,
    ) -> bool:
        """Grade a ``Solution`` class by CALLING its method, LeetCode-style.

        The method name comes from the task's own ``starter_code``, not from
        guessing: a problem's entry point is part of its contract, and an agent
        that also defines helpers would otherwise be graded on whichever
        function happened to sort first.
        """
        method = _entry_point(task.get("starter_code") or "")
        if not method:
            return False
        env.write_file("solution.py", solution)
        env.write_file("_cases.json", json.dumps(test_cases))
        env.write_file("_driver.py", _FUNCTIONAL_DRIVER.format(method=method))
        result = env.run_command("python _driver.py")
        if result.exit_code != 0:
            return False
        # The driver prints exactly one verdict token. Requiring the token (as
        # opposed to treating exit 0 as success) means a solution that crashes
        # the driver, prints nothing, or floods stdout cannot pass by accident.
        return (result.stdout or "").strip().endswith("__LCB_PASS__")

    # ------------------------------------------------------------------ Rotation helpers

    @classmethod
    def rotated_window(
        cls,
        model_cutoff: str,
        months: int = 3,
        **kwargs: Any,
    ) -> "LiveCodeBench":
        """Build a LiveCodeBench restricted to problems released after a model's cutoff.

        Args:
            model_cutoff: ISO date string (``YYYY-MM-DD``) — typically the model's
                training data cutoff.
            months: Width of the rotation window (default 3 months past the cutoff).
            **kwargs: Forwarded to ``LiveCodeBench.__init__``.

        Returns:
            LiveCodeBench instance covering ``[cutoff, cutoff + months]``.
        """
        cutoff = _parse_iso(model_cutoff)
        end = cutoff + timedelta(days=months * 30)
        return cls(
            start_date=cutoff.isoformat(),
            end_date=end.isoformat(),
            **kwargs,
        )

    # ------------------------------------------------------------------ Internals

    @staticmethod
    def _parse_window(start: str | None, end: str | None) -> DateWindow | None:
        if start is None and end is None:
            return None
        s = _parse_iso(start) if start else date(1970, 1, 1)
        e = _parse_iso(end) if end else date(9999, 12, 31)
        if s > e:
            raise ValueError(f"start_date {s} is after end_date {e}")
        return DateWindow(start=s, end=e)

    def _load_and_filter(self) -> list[dict[str, Any]]:
        raw = self._load_raw()
        out: list[dict[str, Any]] = []
        for task in raw:
            if self._difficulty and task.get("difficulty") != self._difficulty:
                continue
            if self._window:
                released = task.get("contest_date") or task.get("release_date")
                if released is None:
                    continue
                try:
                    released_d = _parse_iso(released[:10])
                except ValueError:
                    continue
                if not self._window.contains(released_d):
                    continue
            out.append(task)
        if self._limit is not None:
            out = _stratified_head(out, self._limit)
        return out

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._dataset_path:
            return []
        import json
        from pathlib import Path

        data = json.loads(Path(self._dataset_path).read_text())
        return data if isinstance(data, list) else data.get("problems", [])


def _parse_iso(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()
