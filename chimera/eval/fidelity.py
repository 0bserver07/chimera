"""Replica-vs-real fidelity — measure how faithfully a Chimera replica tracks a real agent.

This is the payoff of the "replicate agents" pillar of the mission: for agents
Chimera mirrors internally (its ``swe_agent`` / ``codex`` / ``aider`` / ``cline``
styles), Chimera holds **both** a code-backed internal replica *and* the ability
to drive the real external CLI. Running the pair ``(replica, real)`` on the
*same* benchmark, *same* model, *same* budget, and *same* sandbox turns "we
replicated agent X" from a claim into a measured number:

- ``|pass_rate(replica) - pass_rate(real)|`` — outcome fidelity, and
- a coarse trajectory-divergence proxy (the tool-call count difference).

The harness is a thin, honest reduction over the existing matrix layer: it calls
:func:`~chimera.eval.matrix.run_matrix` with the two runners against one
benchmark — so every controlled-variable guarantee the matrix already provides
(same grader, same env factory, same budget object per cell) holds here for free
— then pairs the two resulting :class:`~chimera.eval.matrix.MatrixCell` objects
into a :class:`FidelityResult`. It never re-drives an agent and never fabricates
a number. See the "Signature experiment: replica vs. real" section of
``docs/specs/agent-benchmark-matrix.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chimera.eval.matrix import run_matrix

if TYPE_CHECKING:
    from chimera.core.budget import BudgetSpec
    from chimera.eval.harness import Benchmark
    from chimera.eval.matrix import MatrixCell
    from chimera.eval.runners.base import AgentRunner


@dataclass
class FidelityResult:
    """One replica-vs-real comparison on a single benchmark.

    Deltas are always ``replica - real`` so a positive number means the replica
    scored/spent *more* than the real agent.

    Attributes:
        benchmark: Benchmark column both runners were measured on
            (``Benchmark.name()``).
        replica_id: Row label of the internal replica runner.
        real_id: Row label of the runner driving the real external agent.
        replica_pass_rate: Replica cell ``pass_rate`` (``passed / total``).
        real_pass_rate: Real cell ``pass_rate``.
        delta_pass_rate: ``replica_pass_rate - real_pass_rate`` — the headline
            outcome-fidelity number.
        replica_cost: Replica cell ``cost_usd`` (summed across the cell's tasks).
        real_cost: Real cell ``cost_usd``.
        delta_cost: ``replica_cost - real_cost``.
        replica_tool_calls: Replica cell ``tool_calls``. This is the tool-call
            count of the cell's **last graded task attempt** (not a sum or mean
            across tasks) — that is exactly what
            :class:`~chimera.eval.matrix.MatrixCell` records, and it is
            surfaced verbatim so the divergence proxy below is interpreted
            correctly. Typed as ``float`` for uniform arithmetic/rendering.
        real_tool_calls: Real cell ``tool_calls``, same semantics.
        trajectory_divergence: ``abs(replica_tool_calls - real_tool_calls)`` — a
            deliberately coarse proxy for trajectory-shape divergence. Richer
            divergence (tool mix, edit locality, step alignment) is future work
            per the spec; this captures only the gross step-count gap.
        replica_status: Replica cell terminal ``status`` (``completed`` |
            ``budget_exhausted`` | ``error`` | ``timeout``), or ``missing`` when
            no replica cell was produced.
        real_status: Real cell terminal ``status``, or ``missing``.
        notes: Human-readable caveats — a missing/errored cell, or a cell whose
            budget was only partially honored. Empty when the comparison is
            clean. Never silently dropped: a partial-budget comparison always
            says so here.
    """

    benchmark: str
    replica_id: str
    real_id: str
    replica_pass_rate: float
    real_pass_rate: float
    delta_pass_rate: float
    replica_cost: float
    real_cost: float
    delta_cost: float
    replica_tool_calls: float
    real_tool_calls: float
    trajectory_divergence: float
    replica_status: str
    real_status: str
    notes: str = ""

    def summary(self) -> str:
        """Render the comparison as one human-readable line.

        Returns:
            A single line naming both runner ids and the benchmark, the two
            pass rates and their delta, the cost delta, and the divergence
            proxy — with any :attr:`notes` appended in brackets.
        """
        line = (
            f"{self.replica_id} vs {self.real_id} on {self.benchmark}: "
            f"replica {self.replica_pass_rate:.0%} vs real {self.real_pass_rate:.0%} "
            f"(Δpass {self.delta_pass_rate:+.0%}), "
            f"Δcost ${self.delta_cost:+.4f}, "
            f"divergence {self.trajectory_divergence:.0f} tool calls"
        )
        if self.notes:
            line += f" [{self.notes}]"
        return line


def _cell_fields(cell: MatrixCell | None) -> tuple[float, float, float, str]:
    """Extract ``(pass_rate, cost, tool_calls, status)`` from a cell, safely.

    Args:
        cell: A matrix cell, or ``None`` when the cell was absent from the run.

    Returns:
        The four values, with ``(0.0, 0.0, 0.0, "missing")`` when *cell* is
        ``None``.
    """
    if cell is None:
        return 0.0, 0.0, 0.0, "missing"
    return cell.pass_rate, cell.cost_usd, float(cell.tool_calls), cell.status


def _pick_cells(
    cells: list[MatrixCell],
    replica_id: str,
    real_id: str,
    benchmark: str,
) -> tuple[MatrixCell | None, MatrixCell | None]:
    """Select the replica and real cells for *benchmark* from a matrix run.

    Cells are matched by ``agent_id`` + ``benchmark``. When both runners share
    an id (an easy misconfiguration for a self-comparison), they cannot be told
    apart by id, so the matrix's runner order is used instead — the report lists
    cells in ``agent × benchmark`` order, so the replica cell comes first.

    Args:
        cells: All cells from the fidelity matrix run.
        replica_id: The replica runner's id.
        real_id: The real runner's id.
        benchmark: The benchmark column name.

    Returns:
        ``(replica_cell, real_cell)``; either element is ``None`` when no
        matching cell was produced.
    """
    column = [c for c in cells if c.benchmark == benchmark]
    if replica_id == real_id:
        first = column[0] if len(column) >= 1 else None
        second = column[1] if len(column) >= 2 else None
        return first, second
    replica_cell = next((c for c in column if c.agent_id == replica_id), None)
    real_cell = next((c for c in column if c.agent_id == real_id), None)
    return replica_cell, real_cell


def _build_notes(
    replica_id: str,
    real_id: str,
    replica_cell: MatrixCell | None,
    real_cell: MatrixCell | None,
) -> str:
    """Assemble the honest-caveats note for a comparison.

    Surfaces, in order: a missing cell, an errored cell (with its message), a
    partially honored budget (with its detail), and an id collision. A clean
    comparison yields an empty string.

    Args:
        replica_id: The replica runner's id.
        real_id: The real runner's id.
        replica_cell: The replica cell, or ``None``.
        real_cell: The real cell, or ``None``.

    Returns:
        A ``"; "``-joined note string, or ``""`` when nothing is worth flagging.
    """
    parts: list[str] = []
    for role, agent_id, cell in (
        ("replica", replica_id, replica_cell),
        ("real", real_id, real_cell),
    ):
        if cell is None:
            parts.append(f"{role} cell '{agent_id}' missing from matrix run")
            continue
        if cell.status == "error":
            detail = cell.budget_note or "unknown error"
            parts.append(f"{role} '{agent_id}' errored: {detail}")
            continue
        if not cell.budget_honored:
            detail = cell.budget_note or "partial"
            parts.append(f"{role} '{agent_id}' budget only partially honored: {detail}")
    if replica_id == real_id:
        parts.append("replica and real share the same id; cells matched by matrix order")
    return "; ".join(parts)


def run_fidelity(
    replica: AgentRunner,
    real: AgentRunner,
    benchmark: Benchmark,
    env_factory: Any = None,
    budget: BudgetSpec | None = None,
    model: str = "",
) -> FidelityResult:
    """Measure one replica against its real counterpart on one benchmark.

    Runs both agents through :func:`~chimera.eval.matrix.run_matrix` — a single
    2-row × 1-column grid — so both cells share the benchmark's grader, the
    *env_factory*, and the *budget* object. The two cells are then reduced to a
    :class:`FidelityResult` of deltas (all ``replica - real``) plus a
    tool-call divergence proxy.

    A missing or errored cell does not raise: its fields default to zero with
    ``status="missing"`` (absent cell) or the cell's own ``"error"`` status, and
    the reason is recorded in :attr:`FidelityResult.notes`. Likewise, if either
    cell honored only part of the budget, the note says so — the comparison is
    never silently presented as clean when a control was partial.

    Args:
        replica: The internal replica runner (matrix row one).
        real: The runner driving the real external agent (matrix row two).
        benchmark: The benchmark both are measured on.
        env_factory: Optional zero-argument callable producing a fresh
            environment per task, shared by both cells.
        budget: Optional :class:`~chimera.core.budget.BudgetSpec` applied to
            every attempt in both cells; ``None`` runs unbudgeted.
        model: Model identifier shared by both cells, recorded on the underlying
            matrix report for provenance.

    Returns:
        A :class:`FidelityResult` for the ``(replica, real, benchmark)`` triple.
    """
    report = run_matrix(
        [replica, real],
        [benchmark],
        env_factory=env_factory,
        budget=budget,
        model=model,
    )
    bench_name = benchmark.name()
    replica_cell, real_cell = _pick_cells(
        report.cells, replica.id, real.id, bench_name
    )

    replica_pass, replica_cost, replica_tools, replica_status = _cell_fields(replica_cell)
    real_pass, real_cost, real_tools, real_status = _cell_fields(real_cell)

    return FidelityResult(
        benchmark=bench_name,
        replica_id=replica.id,
        real_id=real.id,
        replica_pass_rate=replica_pass,
        real_pass_rate=real_pass,
        delta_pass_rate=replica_pass - real_pass,
        replica_cost=replica_cost,
        real_cost=real_cost,
        delta_cost=replica_cost - real_cost,
        replica_tool_calls=replica_tools,
        real_tool_calls=real_tools,
        trajectory_divergence=abs(replica_tools - real_tools),
        replica_status=replica_status,
        real_status=real_status,
        notes=_build_notes(replica.id, real.id, replica_cell, real_cell),
    )


def fidelity_table(
    pairs: list[tuple[AgentRunner, AgentRunner]],
    benchmark: Benchmark,
    **kwargs: Any,
) -> list[FidelityResult]:
    """Run :func:`run_fidelity` for every ``(replica, real)`` pair on one benchmark.

    Args:
        pairs: ``(replica, real)`` runner pairs — one row of the fidelity table
            each.
        benchmark: The benchmark every pair is measured on.
        **kwargs: Forwarded verbatim to :func:`run_fidelity` (``env_factory``,
            ``budget``, ``model``), so every pair runs under identical controls.

    Returns:
        One :class:`FidelityResult` per pair, in input order.
    """
    return [run_fidelity(replica, real, benchmark, **kwargs) for replica, real in pairs]


def _esc(text: str) -> str:
    """Escape a value for a single markdown table cell.

    Args:
        text: Raw cell text (an id or a free-text note may contain pipes or
            newlines that would otherwise break the table).

    Returns:
        The text with ``|`` escaped and newlines flattened to spaces.
    """
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(results: list[FidelityResult]) -> str:
    """Render a list of fidelity results as a markdown table.

    Columns: replica, real, benchmark, replica%, real%, Δ, Δcost, divergence,
    notes. An empty *results* list still renders the header so the table shape
    is stable.

    Args:
        results: The rows to render.

    Returns:
        A markdown string: a title, a blank line, and the table.
    """
    header = (
        "| replica | real | benchmark | replica% | real% | Δ | Δcost | "
        "divergence | notes |"
    )
    divider = "|---|---|---|---|---|---|---|---|---|"
    lines = ["## Replica-vs-real fidelity", "", header, divider]
    for r in results:
        lines.append(
            f"| {_esc(r.replica_id)} | {_esc(r.real_id)} | {_esc(r.benchmark)} | "
            f"{r.replica_pass_rate:.0%} | {r.real_pass_rate:.0%} | "
            f"{r.delta_pass_rate:+.0%} | ${r.delta_cost:+.4f} | "
            f"{r.trajectory_divergence:.0f} | {_esc(r.notes) or '—'} |"
        )
    return "\n".join(lines)
