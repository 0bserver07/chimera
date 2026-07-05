"""run_matrix — cross N agents × M benchmarks under one controlled harness.

Generalises the 1-D ``bench-compare``
(:class:`~chimera.eval.comparative.ComparativeEval` — one benchmark × N agent
loops) to the 2-D agent × benchmark grid the mission calls its headline
deliverable. Every cell reuses the *existing*
:class:`~chimera.eval.harness.Harness`: same benchmark evaluator, same env
factory, same graders, same budget object — so the only free variable across a
row is the agent and across a column is the benchmark. See
``docs/specs/agent-benchmark-matrix.md``.

The bridge is :class:`_HarnessAgent`, a thin shim exposing any
:class:`~chimera.eval.runners.base.AgentRunner` as the ``run(prompt, env) ->
AgentResult`` object the Harness drives, so the ~28 benchmark adapters measure
external agents with zero benchmark changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.eval.harness import Harness
from chimera.types import AgentResult

if TYPE_CHECKING:
    from chimera.core.budget import BudgetSpec
    from chimera.env.base import Environment
    from chimera.eval.harness import Benchmark
    from chimera.eval.runners.base import AgentRunner, AgentRunResult


@dataclass
class MatrixCell:
    """One (agent, benchmark) result in the matrix.

    Attributes:
        agent_id: The runner's row label.
        benchmark: The benchmark's column label (``Benchmark.name()``).
        total: Tasks graded in this cell.
        passed: Tasks that passed.
        pass_rate: ``passed / total`` (``0.0`` when ``total`` is 0).
        cost_usd: Aggregated dollar cost across the cell's tasks
            (from the harness ``EvalResult``).
        tool_calls: Tool-call count from the cell's last attempt (the
            normalized budget unit for agents routed through
            ``tool_executor``).
        wall_clock_sec: Wall-clock seconds of the cell's last attempt.
        status: Terminal status of the last attempt (``completed`` |
            ``budget_exhausted`` | ``error`` | ``timeout``), or ``error`` when
            the whole cell failed to run.
        budget_honored: ``False`` when the runner could only honor part of the
            budget (e.g. wall-clock/cost but not tool-calls) — keeps the
            "controlled" claim honest per the spec.
        budget_note: Human-readable detail when ``budget_honored`` is ``False``
            (or an error message for a failed cell).
    """

    agent_id: str
    benchmark: str
    total: int
    passed: int
    pass_rate: float
    cost_usd: float
    tool_calls: int
    wall_clock_sec: float
    status: str
    budget_honored: bool = True
    budget_note: str = ""


@dataclass
class MatrixReport:
    """The full agent × benchmark grid.

    Attributes:
        cells: Every :class:`MatrixCell`, in ``agent × benchmark`` order.
        model: The model identifier shared by every cell (recorded for
            provenance; empty when unset).
    """

    cells: list[MatrixCell] = field(default_factory=list)
    model: str = ""

    def by_agent(self) -> dict[str, dict[str, MatrixCell]]:
        """Pivot the grid by agent.

        Returns:
            ``agent_id -> {benchmark -> MatrixCell}``, preserving first-seen
            order of both axes.
        """
        pivot: dict[str, dict[str, MatrixCell]] = {}
        for cell in self.cells:
            pivot.setdefault(cell.agent_id, {})[cell.benchmark] = cell
        return pivot

    def best_per_benchmark(self) -> dict[str, str]:
        """Return the top-scoring agent per benchmark column.

        Ties keep the first agent seen for that benchmark. Cells that errored
        still participate (their ``pass_rate`` is ``0.0``), so a column where
        every agent errored resolves to whichever ran first.

        Returns:
            ``benchmark -> agent_id`` of the highest ``pass_rate``.
        """
        best: dict[str, tuple[float, str]] = {}
        for cell in self.cells:
            current = best.get(cell.benchmark)
            if current is None or cell.pass_rate > current[0]:
                best[cell.benchmark] = (cell.pass_rate, cell.agent_id)
        return {benchmark: agent for benchmark, (_, agent) in best.items()}

    def summary(self) -> str:
        """Render a markdown grid of pass rates (rows=agent, cols=benchmark).

        Returns:
            A markdown string: a header row of benchmark names and one row per
            agent whose cells show the pass rate as a percentage (``err`` for a
            failed cell, ``—`` for a missing one).
        """
        agents: list[str] = []
        benchmarks: list[str] = []
        for cell in self.cells:
            if cell.agent_id not in agents:
                agents.append(cell.agent_id)
            if cell.benchmark not in benchmarks:
                benchmarks.append(cell.benchmark)
        lookup = {(cell.agent_id, cell.benchmark): cell for cell in self.cells}

        title = "Agent × Benchmark matrix — pass rate"
        if self.model:
            title += f" (model `{self.model}`)"
        header = "| Agent | " + " | ".join(benchmarks) + " |"
        divider = "|" + "---|" * (len(benchmarks) + 1)
        lines = [title, "", header, divider]
        for agent in agents:
            values: list[str] = []
            for benchmark in benchmarks:
                found = lookup.get((agent, benchmark))
                if found is None:
                    values.append("—")
                elif found.status == "error":
                    values.append("err")
                else:
                    values.append(f"{found.pass_rate:.0%}")
            lines.append(f"| {agent} | " + " | ".join(values) + " |")
        return "\n".join(lines)


#: Uniform final-answer contract appended to every task prompt (all agents in a
#: run get the identical suffix, so it stays a controlled variable). Multi-step
#: loops (plan-execute, lint-feedback, reflexion) tend to end on a summary
#: ("I've implemented the function...") rather than the artifact itself, and
#: answer-graded benchmarks then score a correct solution as 0%. The contract
#: makes the gradeable artifact the final message. Disable with
#: ``run_matrix(..., answer_contract=False)`` to measure raw prompt behavior.
FINAL_ANSWER_CONTRACT = (
    "\n\nIMPORTANT: Your final message is what gets graded. End with the "
    "complete final solution itself — the full code in one fenced code block, "
    "or the bare final answer — not a summary of what you did."
)


class _HarnessAgent:
    """Adapt an :class:`AgentRunner` to the Harness ``run(prompt, env)`` contract.

    The Harness drives ``agent.run(prompt, env) -> AgentResult``; an
    :class:`~chimera.eval.runners.base.AgentRunner` speaks
    ``run(task, env, budget) -> AgentRunResult``. This shim bridges the two and
    stashes the last :class:`~chimera.eval.runners.base.AgentRunResult` so
    :func:`run_matrix` can read the runner-native wall-clock / status /
    tool-call counters the harness ``AgentResult`` does not carry.

    Args:
        runner: The agent under test.
        budget: Optional :class:`~chimera.core.budget.BudgetSpec` forwarded to
            the runner on every task.
        answer_contract: When ``True`` (default), append
            :data:`FINAL_ANSWER_CONTRACT` to every prompt so multi-step agents
            end on the gradeable artifact rather than a summary.
    """

    def __init__(
        self,
        runner: AgentRunner,
        budget: BudgetSpec | None = None,
        answer_contract: bool = True,
    ) -> None:
        self.runner = runner
        self.budget = budget
        self.answer_contract = answer_contract
        self.last_result: AgentRunResult | None = None
        self.last_wall_clock_sec: float = 0.0

    def run(self, prompt: str, env: Environment | None = None) -> AgentResult:
        """Run the wrapped runner and map its result onto an ``AgentResult``.

        Args:
            prompt: The task prompt handed over by the Harness.
            env: Optional per-task environment.

        Returns:
            An :class:`~chimera.types.AgentResult` the Harness can grade.
        """
        if self.answer_contract:
            prompt = prompt + FINAL_ANSWER_CONTRACT
        start = time.monotonic()
        result = self.runner.run(prompt, env, self.budget)
        self.last_wall_clock_sec = time.monotonic() - start
        self.last_result = result
        completed = result.status == "completed"
        return AgentResult(
            output=result.answer,
            steps=result.llm_calls,
            tool_calls_total=result.tool_calls,
            cost=result.cost_usd,
            success=completed,
            error=None if completed else result.status,
        )


def _budget_flags(result: AgentRunResult | None) -> tuple[bool, str]:
    """Read per-cell budget-honesty flags from a runner result.

    In-process runners honor the tool-call budget exactly, so they leave the
    defaults (``True`` / ``""``). External runners that can only honor a subset
    of the budget signal it via ``raw["budget_honored"]`` /
    ``raw["budget_note"]``, which surface on the cell.

    Args:
        result: The last :class:`~chimera.eval.runners.base.AgentRunResult`, or
            ``None`` when the cell produced no result.

    Returns:
        ``(budget_honored, budget_note)``.
    """
    raw = getattr(result, "raw", None)
    if isinstance(raw, dict):
        return bool(raw.get("budget_honored", True)), str(raw.get("budget_note", ""))
    return True, ""


def _run_cell(
    runner: AgentRunner,
    benchmark: Benchmark,
    env_factory: Any,
    budget: BudgetSpec | None,
    graders: list[Any] | None,
    answer_contract: bool = True,
) -> MatrixCell:
    """Run one (agent, benchmark) pair and reduce it to a :class:`MatrixCell`.

    A failure anywhere in the cell (a raising runner, a broken env) is caught
    and returned as a ``status="error"`` cell so one bad pair never aborts the
    grid.
    """
    agent_id = getattr(runner, "id", "?")
    bench_name = benchmark.name()
    try:
        shim = _HarnessAgent(runner, budget=budget, answer_contract=answer_contract)
        harness = Harness(benchmark, shim, env_factory=env_factory, graders=graders)
        result = harness.run()
        last = shim.last_result
        status = last.status if last is not None else "completed"
        tool_calls = last.tool_calls if last is not None else 0
        wall_clock = 0.0
        if last is not None:
            wall_clock = last.wall_clock_sec or shim.last_wall_clock_sec
        honored, note = _budget_flags(last)
        return MatrixCell(
            agent_id=agent_id,
            benchmark=bench_name,
            total=result.total,
            passed=result.passed,
            pass_rate=result.pass_rate,
            cost_usd=result.total_cost,
            tool_calls=tool_calls,
            wall_clock_sec=wall_clock,
            status=status,
            budget_honored=honored,
            budget_note=note,
        )
    except Exception as exc:  # noqa: BLE001 — one failing cell must not abort the grid
        return MatrixCell(
            agent_id=agent_id,
            benchmark=bench_name,
            total=0,
            passed=0,
            pass_rate=0.0,
            cost_usd=0.0,
            tool_calls=0,
            wall_clock_sec=0.0,
            status="error",
            budget_honored=False,
            budget_note=f"{type(exc).__name__}: {exc}",
        )


def run_matrix(
    runners: list[AgentRunner],
    benchmarks: list[Benchmark],
    env_factory: Any = None,
    budget: BudgetSpec | None = None,
    graders: list[Any] | None = None,
    model: str = "",
    answer_contract: bool = True,
) -> MatrixReport:
    """Run every agent against every benchmark and collect the grid.

    Each ``(runner, benchmark)`` pair is driven through the existing
    :class:`~chimera.eval.harness.Harness` via :class:`_HarnessAgent`, so the
    grader, environment, and budget are identical across every cell of a
    benchmark column — the controlled-variable guarantee the matrix rests on.

    Args:
        runners: Agents under test (one matrix row each).
        benchmarks: Benchmarks to evaluate against (one column each).
        env_factory: Optional zero-argument callable producing a fresh
            environment per task, shared across all cells.
        budget: Optional :class:`~chimera.core.budget.BudgetSpec` applied to
            every attempt; ``None`` runs unbudgeted.
        graders: Optional post-hoc graders passed through to each Harness.
        model: Model identifier shared by every cell, recorded on the report.
        answer_contract: When ``True`` (default), every prompt carries the
            uniform :data:`FINAL_ANSWER_CONTRACT` suffix so multi-step agents
            end on the gradeable artifact instead of a summary. Identical
            across all agents in the run, so it remains a controlled variable.

    Returns:
        A :class:`MatrixReport` with one :class:`MatrixCell` per pair. A cell
        that fails to run is recorded with ``status="error"`` rather than
        aborting the sweep.
    """
    cells: list[MatrixCell] = []
    for runner in runners:
        for benchmark in benchmarks:
            cells.append(
                _run_cell(
                    runner, benchmark, env_factory, budget, graders, answer_contract
                )
            )
    return MatrixReport(cells=cells, model=model)
