"""Cell status derives from ALL task attempts, not just the last one.

The Modal grid exposed the mislabel: a cell whose final task errored after
real passes read status="error" (and a trailing success masked earlier
errors as "completed"). Cells must aggregate honestly.
"""

from __future__ import annotations

from typing import Any

from chimera.eval.matrix import _derive_cell_status, run_matrix
from chimera.eval.runners.base import AgentRunResult


class _ScriptedRunner:
    """AgentRunner returning a scripted status per task, in order."""

    def __init__(self, id: str, statuses: list[str]) -> None:
        self.id = id
        self._statuses = list(statuses)
        self._i = 0

    def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
        status = self._statuses[self._i % len(self._statuses)]
        self._i += 1
        return AgentRunResult(
            answer="def f():\n    return 1" if status == "completed" else "",
            cost_usd=0.001,
            tool_calls=1,
            llm_calls=1,
            status=status,
        )


class _FakeBench:
    """N trivial tasks; grading passes iff the answer is non-empty."""

    def __init__(self, n: int) -> None:
        self._n = n

    def name(self) -> str:
        return "fake-bench"

    def tasks(self) -> list[dict[str, Any]]:
        return [{"prompt": f"task {i}"} for i in range(self._n)]

    def evaluate(self, task: dict[str, Any], output: str, env: Any = None) -> bool:
        return bool(output.strip())


def _cell(statuses: list[str]):
    runner = _ScriptedRunner("scripted", statuses)
    report = run_matrix([runner], [_FakeBench(len(statuses))])
    return report.cells[0]


def test_trailing_error_after_passes_is_partial_not_error() -> None:
    cell = _cell(["completed", "completed", "error"])
    assert cell.status == "partial_error"  # was: "error" (last-attempt bug)
    assert cell.passed == 2  # the real passes survive


def test_trailing_success_does_not_mask_earlier_errors() -> None:
    cell = _cell(["error", "error", "completed"])
    assert cell.status == "partial_error"  # was: "completed"


def test_uniform_statuses_unchanged() -> None:
    assert _cell(["completed"] * 3).status == "completed"
    assert _cell(["error"] * 3).status == "error"
    assert _cell(["budget_exhausted"] * 2).status == "budget_exhausted"


def test_error_free_mix_reports_limit_pressure() -> None:
    cell = _cell(["completed", "budget_exhausted"])
    assert cell.status == "budget_exhausted"


def test_derive_helper_edge_cases() -> None:
    assert _derive_cell_status([]) == "completed"
    assert _derive_cell_status(["timeout", "budget_exhausted"]) == "timeout"
    assert _derive_cell_status(["completed", "error", "budget_exhausted"]) == "partial_error"
