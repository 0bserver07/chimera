"""run_fidelity over fake replica/real runners × a fake benchmark (no LLM, no net).

Drives the real :func:`~chimera.eval.fidelity.run_fidelity` (which drives the
real :func:`~chimera.eval.matrix.run_matrix` and the real
:class:`~chimera.eval.harness.Harness`) with deterministic fakes, so the delta
maths, divergence proxy, budget-honesty note, and rendering are verified without
a model. The replica passes 1/2 and the real passes 2/2 so every delta is
non-trivial.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.eval.fidelity import (
    FidelityResult,
    fidelity_table,
    render_markdown,
    run_fidelity,
)
from chimera.eval.harness import Benchmark
from chimera.eval.runners.base import AgentRunResult

# Per-task prompts and their golden answers (the FakeBenchmark below).
_PROMPT_1 = "PROMPT_1"
_PROMPT_2 = "PROMPT_2"
_GOLD_1 = "GOLD1"
_GOLD_2 = "GOLD2"


class FakeRunner:
    """An :class:`AgentRunner` with deterministic, no-LLM output.

    ``answer`` is either a fixed string returned for every task (a blunt replica
    that gets some tasks wrong) or a ``prompt -> answer`` mapping (a task-aware
    "real" agent that can get every task right). ``cost``/``tool_calls`` are
    fixed per attempt; ``raw`` lets a test inject the ``budget_honored`` /
    ``budget_note`` flags the matrix layer reads back onto the cell.
    """

    def __init__(
        self,
        id: str,
        answer: str | dict[str, str],
        *,
        cost: float = 0.01,
        tool_calls: int = 3,
        llm_calls: int = 2,
        status: str = "completed",
        raw: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self._answer = answer
        self._cost = cost
        self._tool_calls = tool_calls
        self._llm_calls = llm_calls
        self._status = status
        self._raw = raw or {}

    def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
        # The Harness hands the runner the task *prompt* string, not the dict.
        if isinstance(task, str):
            prompt = task
        elif isinstance(task, dict):
            prompt = str(task.get("prompt", ""))
        else:
            prompt = str(task)
        answer = self._answer[prompt] if isinstance(self._answer, dict) else self._answer
        return AgentRunResult(
            answer=answer,
            cost_usd=self._cost,
            tool_calls=self._tool_calls,
            llm_calls=self._llm_calls,
            status=self._status,
            raw=dict(self._raw),
        )


class FakeBenchmark(Benchmark):
    """Two tasks with distinct golden answers; passes iff output == golden."""

    def __init__(self, name: str = "fidbench") -> None:
        self._name = name

    def name(self) -> str:
        return self._name

    def tasks(self) -> list[dict[str, Any]]:
        return [
            {"id": "t1", "prompt": _PROMPT_1, "golden": _GOLD_1},
            {"id": "t2", "prompt": _PROMPT_2, "golden": _GOLD_2},
        ]

    def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
        return agent_output == task["golden"]


def _replica() -> FakeRunner:
    """Fixed-answer replica: right on task 1, wrong on task 2 -> 1/2."""
    return FakeRunner("aider-replica", _GOLD_1, cost=0.01, tool_calls=3)


def _real(**kwargs: Any) -> FakeRunner:
    """Task-aware real agent: right on both -> 2/2 (costlier, more tools)."""
    return FakeRunner(
        "aider-real",
        {_PROMPT_1: _GOLD_1, _PROMPT_2: _GOLD_2},
        cost=0.03,
        tool_calls=5,
        **kwargs,
    )


def test_run_fidelity_computes_deltas_and_divergence() -> None:
    result = run_fidelity(_replica(), _real(), FakeBenchmark(), answer_contract=False)

    assert isinstance(result, FidelityResult)
    assert result.benchmark == "fidbench"
    assert result.replica_id == "aider-replica"
    assert result.real_id == "aider-real"

    # Pass rates: replica 1/2, real 2/2.
    assert result.replica_pass_rate == 0.5
    assert result.real_pass_rate == 1.0
    # delta_pass_rate is replica - real.
    assert result.delta_pass_rate == pytest.approx(0.5 - 1.0)

    # Cost is summed across the cell's 2 tasks: replica 2x0.01, real 2x0.03.
    assert result.replica_cost == pytest.approx(0.02)
    assert result.real_cost == pytest.approx(0.06)
    # delta_cost is replica - real.
    assert result.delta_cost == pytest.approx(0.02 - 0.06)

    # tool_calls come straight off the cell (last attempt's count).
    assert result.replica_tool_calls == 3.0
    assert result.real_tool_calls == 5.0
    # divergence proxy = |replica - real|.
    assert result.trajectory_divergence == pytest.approx(abs(3.0 - 5.0))

    assert result.replica_status == "completed"
    assert result.real_status == "completed"
    # A clean comparison carries no caveats.
    assert result.notes == ""


def test_summary_and_markdown_include_ids_and_benchmark() -> None:
    result = run_fidelity(_replica(), _real(), FakeBenchmark(), answer_contract=False)

    line = result.summary()
    for token in ("aider-replica", "aider-real", "fidbench"):
        assert token in line

    table = render_markdown([result])
    for token in ("aider-replica", "aider-real", "fidbench"):
        assert token in table
    # Column headers present.
    for column in ("replica", "real", "benchmark", "divergence", "Δcost"):
        assert column in table
    # One data row for the single result.
    assert table.count("| aider-replica |") == 1


def test_partial_budget_note_surfaces() -> None:
    # The real runner reports it could only honor part of the budget; the matrix
    # layer reads raw["budget_honored"]/["budget_note"] onto the cell.
    real = _real(raw={"budget_honored": False, "budget_note": "only wall-clock+cost honored"})

    result = run_fidelity(_replica(), real, FakeBenchmark(), answer_contract=False)

    assert "budget" in result.notes.lower()
    assert "only wall-clock+cost honored" in result.notes
    # The caveat rides along into the one-line summary too.
    assert "budget" in result.summary().lower()
    # Deltas are still computed (a partial-budget cell is not a failed cell).
    assert result.real_status == "completed"
    assert result.delta_pass_rate == pytest.approx(-0.5)


def test_errored_cell_is_noted_not_raised() -> None:
    class BoomRunner:
        id = "boom"

        def run(self, task: Any, env: Any = None, budget: Any = None) -> AgentRunResult:
            raise RuntimeError("kaboom")

    result = run_fidelity(BoomRunner(), _real(), FakeBenchmark(), answer_contract=False)

    # The failing replica cell surfaces as an error status + note, no raise.
    assert result.replica_status == "error"
    assert "kaboom" in result.notes
    assert result.replica_pass_rate == 0.0
    # The healthy real side still measured.
    assert result.real_pass_rate == 1.0
    assert result.real_status == "completed"


def test_fidelity_table_runs_every_pair() -> None:
    # list[Any] to sidestep list invariance (FakeRunner satisfies AgentRunner
    # structurally, but list[tuple[FakeRunner, ...]] is not list[tuple[AgentRunner, ...]]).
    pairs: list[Any] = [
        (_replica(), _real()),
        (FakeRunner("codex-replica", _GOLD_2, tool_calls=7), _real()),
    ]

    results = fidelity_table(pairs, FakeBenchmark(), model="glm-5", answer_contract=False)

    assert len(results) == 2
    assert [r.replica_id for r in results] == ["aider-replica", "codex-replica"]
    # Second replica answers GOLD2 -> right on task 2 only -> also 1/2, but a
    # different divergence (|7 - 5| = 2 here too, but its own row exists).
    assert results[1].replica_tool_calls == 7.0
    assert results[1].trajectory_divergence == pytest.approx(2.0)

    table = render_markdown(results)
    # Header + both rows render.
    assert table.count("| aider-replica |") == 1
    assert table.count("| codex-replica |") == 1


def test_render_markdown_empty_still_has_header() -> None:
    table = render_markdown([])
    assert "| replica | real | benchmark |" in table
    # No data rows.
    assert "| aider-replica |" not in table
