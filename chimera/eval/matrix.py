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

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.eval.error_taxonomy import FailureCategory, classify_failure
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
        status: Aggregate status across ALL of the cell's task attempts
            (see :func:`_derive_cell_status`): ``completed`` |
            ``budget_exhausted`` | ``timeout`` | ``error`` (every attempt
            errored, or the whole cell failed to run) | ``partial_error``
            (a mix — some attempts errored, the passed count is from the
            attempts that ran).
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
    category: FailureCategory = FailureCategory.UNKNOWN
    #: Per-task terminal-status tally, e.g. ``{"completed": 480, "error": 20}``.
    #: On a ``partial_error`` cell this is what separates real failures from
    #: infra errors — without it a full-column pass rate is only a lower bound.
    status_counts: dict[str, int] = field(default_factory=dict)


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

#: Matches any Markdown code fence pair (``` ... ```), the artifact shape
#: ``_extract_code``-style graders look for. When an answer already contains one
#: the harvest is skipped — the agent put the gradeable artifact in its message.
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)

#: Ceiling on harvested source files. Answer-graded tasks expect a single
#: artifact; a handful of files is "a few", but many files means it is not an
#: answer-shaped task (a cloned repo, a scaffold) and guessing would add noise —
#: so the harvest no-ops instead.
_MAX_HARVEST_FILES = 3

#: Filenames the *grader* owns, never harvested even if present. The harness
#: runs the harvest strictly *before* grading against a fresh per-task env, so
#: these are normally absent at harvest time; the guard is defensive.
#: Note ``solution.py`` is deliberately NOT excluded: before grading, any
#: ``solution.py`` on disk was written by the agent and IS the artifact to
#: grade (the grader overwrites it from the answer afterward), so excluding it
#: would defeat the harvest for agents that name their file ``solution.py``.
_GRADER_ARTIFACTS = frozenset({"_stdin.txt"})


def _has_code_fence(text: str) -> bool:
    """Return ``True`` if *text* already contains a Markdown code fence pair."""
    return bool(_CODE_FENCE.search(text or ""))


def _harvest_env_code(env: Environment) -> tuple[str, list[str]]:
    """Read agent-written ``.py`` source from *env* as fenced code blocks.

    File-artifact agents (e.g. a lint-feedback edit loop) write their solution
    to disk and end their run on prose ("I've implemented the function"), so an
    answer-graded benchmark scores a correct solution 0%. This recovers those
    on-disk sources so an ``_extract_code``-style grader can see them.

    Only fires for a small, answer-shaped set of files: workspace ``.py``
    sources, excluding dot-paths (``.chimera/*`` scratch, etc.) and
    :data:`_GRADER_ARTIFACTS`, capped at :data:`_MAX_HARVEST_FILES`. Any listing
    or read error degrades to "harvested nothing" rather than raising.

    Args:
        env: The per-task environment the agent wrote into.

    Returns:
        ``(appendix, names)`` where *appendix* is the harvested sources joined
        as ```` ```python ```` blocks and *names* the files harvested — or
        ``("", [])`` when nothing plausible was found.
    """
    try:
        listed = env.list_files()
    except Exception:  # noqa: BLE001 — harvest is best-effort; never abort a cell
        return "", []

    candidates: list[str] = []
    for raw_path in listed:
        path = raw_path.replace("\\", "/")
        if not path.endswith(".py"):
            continue
        parts = path.split("/")
        if any(part.startswith(".") for part in parts):
            continue  # dot-dir/file scratch (.chimera/*, hidden temp files)
        if parts[-1] in _GRADER_ARTIFACTS:
            continue
        candidates.append(path)

    # Too many sources → not an answer-shaped task; harvesting would add noise.
    if not candidates or len(candidates) > _MAX_HARVEST_FILES:
        return "", []

    blocks: list[str] = []
    names: list[str] = []
    for path in candidates:
        try:
            content = env.read_file(path)
        except Exception:  # noqa: BLE001 — skip an unreadable file, keep the rest
            continue
        if content.strip():
            blocks.append(f"```python\n{content.rstrip()}\n```")
            names.append(path)

    if not blocks:
        return "", []
    return "\n\n".join(blocks), names


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
        harvest_env_artifacts: When ``True`` (default), if a runner returns an
            answer with no fenced code block, recover agent-written ``.py``
            sources from *env* and append them as fenced blocks so file-artifact
            agents (whose final message is prose, not code) become gradeable.
            See :func:`_harvest_env_code`.
    """

    def __init__(
        self,
        runner: AgentRunner,
        budget: BudgetSpec | None = None,
        answer_contract: bool = True,
        harvest_env_artifacts: bool = True,
    ) -> None:
        self.runner = runner
        self.budget = budget
        self.answer_contract = answer_contract
        self.harvest_env_artifacts = harvest_env_artifacts
        self.last_result: AgentRunResult | None = None
        self.last_wall_clock_sec: float = 0.0
        #: Status of EVERY task attempt in this cell, in order. The cell's
        #: aggregate status derives from all of them (see
        #: :func:`_derive_cell_status`) — using only the last attempt mislabeled
        #: a cell "error" when its final task errored after real passes.
        self.statuses: list[str] = []

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
        self.statuses.append(result.status)
        completed = result.status == "completed"

        # File-artifact rescue: when the answer carries no gradeable code fence,
        # pull the agent's on-disk sources into it so answer-graded benchmarks
        # can see work the agent left in the workspace instead of its message.
        answer = result.answer
        if (
            self.harvest_env_artifacts
            and env is not None
            and not _has_code_fence(answer)
        ):
            appendix, names = _harvest_env_code(env)
            if appendix:
                answer = f"{answer}\n\n{appendix}" if answer.strip() else appendix
                result.raw["harvested_files"] = names

        return AgentResult(
            output=answer,
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


def _derive_cell_status(statuses: list[str]) -> str:
    """Reduce per-task statuses to one honest cell status.

    Rules:
        - no attempts / all identical → that status (``completed`` default);
        - a mix that includes ``error`` → ``partial_error`` — the passed count
          is real, but not every task ran cleanly (previously the cell just
          took the LAST task's status, so one trailing error mislabeled a cell
          full of genuine passes as ``error``, and a trailing success masked
          earlier errors as ``completed``);
        - an error-free mix → the most limit-bound status present
          (``timeout`` over ``budget_exhausted``) so budget/deadline pressure
          stays visible in the grid.
    """
    if not statuses:
        return "completed"
    unique = set(statuses)
    if len(unique) == 1:
        return statuses[0]
    if "error" in unique:
        return "partial_error"
    for severe in ("timeout", "budget_exhausted"):
        if severe in unique:
            return severe
    return "completed"


def _run_cell(
    runner: AgentRunner,
    benchmark: Benchmark,
    env_factory: Any,
    budget: BudgetSpec | None,
    graders: list[Any] | None,
    answer_contract: bool = True,
    harvest_env_artifacts: bool = True,
) -> MatrixCell:
    """Run one (agent, benchmark) pair and reduce it to a :class:`MatrixCell`.

    A failure anywhere in the cell (a raising runner, a broken env) is caught
    and returned as a ``status="error"`` cell so one bad pair never aborts the
    grid.
    """
    agent_id = getattr(runner, "id", "?")
    bench_name = benchmark.name()
    try:
        shim = _HarnessAgent(
            runner,
            budget=budget,
            answer_contract=answer_contract,
            harvest_env_artifacts=harvest_env_artifacts,
        )
        harness = Harness(benchmark, shim, env_factory=env_factory, graders=graders)
        result = harness.run()
        last = shim.last_result
        status = _derive_cell_status(shim.statuses)
        from collections import Counter

        status_counts = dict(Counter(shim.statuses))
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
            category=classify_failure(status, note),
            status_counts=status_counts,
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
            category=classify_failure("error", f"{type(exc).__name__}: {exc}"),
        )


def run_matrix(
    runners: list[AgentRunner],
    benchmarks: list[Benchmark],
    env_factory: Any = None,
    budget: BudgetSpec | None = None,
    graders: list[Any] | None = None,
    model: str = "",
    answer_contract: bool = True,
    harvest_env_artifacts: bool = True,
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
        harvest_env_artifacts: When ``True`` (default), an answer with no fenced
            code block is augmented with the agent's on-disk ``.py`` sources so
            file-artifact agents become gradeable on answer-graded benchmarks.
            Applied uniformly across every cell, so it stays a controlled
            variable. See :func:`_harvest_env_code`.

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
                    runner,
                    benchmark,
                    env_factory,
                    budget,
                    graders,
                    answer_contract,
                    harvest_env_artifacts,
                )
            )
    return MatrixReport(cells=cells, model=model)
