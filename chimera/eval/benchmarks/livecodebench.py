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

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from chimera.eval.harness import Benchmark

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
        """Run agent_output against the problem's stdin/stdout test cases.

        Competitive programming format: each test case is a (stdin, expected_stdout)
        pair. The solution reads from stdin and writes to stdout. We require an
        executable env (Docker/Local) — there is no in-process fallback because
        the solutions use ``input()`` / ``print()``.
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
            out = out[: self._limit]
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
