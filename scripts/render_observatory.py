#!/usr/bin/env python3
"""Render THE OBSERVATORY — the public agent × benchmark results page.

Every number on the page is generated from a JSON receipt committed under
``data/`` — nothing is hand-typed. The generator enforces the measurement-
integrity discipline from ``scripts/verify_status.py`` at build time. Two
patterns ABORT generation rather than render:

* a cell whose status is ``error`` yet claims passes — an errored run cannot
  have produced a graded pass;
* a *uniform zero* — a cell of 5+ tasks that all reached ``completed`` and
  none of which passed. Historically that is always a broken grading
  contract, not a measured 0% (see :func:`_uniform_zero_note`).

Usage::

    uv run python scripts/render_observatory.py            # regenerate page + site copy
    uv run python scripts/render_observatory.py --check    # exit 1 if the committed
                                                           # page is stale vs data/

Outputs (byte-identical pair):

* ``docs/benchmarks/observatory.md``
* ``site/src/content/docs/benchmarks/observatory.md``

Exit codes: ``0`` fresh/written · ``1`` ``--check`` found drift · ``2`` a data
receipt failed integrity validation.

Determinism: input files are discovered by sorted glob, every table is sorted,
and the page's date comes from the newest input file's mtime — never the wall
clock — so regenerating over unchanged inputs is byte-identical. Because git
does not preserve mtimes, ``--check`` compares everything EXCEPT the single
mtime-derived date line, keeping the gate meaningful in a fresh clone/CI.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Default receipt set, resolved as globs against ``--data-dir`` (sorted).
#: fullscore2/fullscore3 carry the clean EXACT columns; fullscore1 is kept for
#: the one number only it holds (the livecodebench lower bound) — its noisy
#: columns are automatically superseded by the quality ranking below.
DEFAULT_PATTERNS: tuple[str, ...] = (
    "matrix-full-glm52.json",
    "modal-grid-fullscore1-*.json",
    "modal-grid-fullscore2-*.json",
    "modal-grid-fullscore3-*.json",
    "modal-grid-observatory*.json",
)

#: Raw benchmark ids as recorded by the harness -> display names (the CLI
#: registry aliases, so every displayed name is also a runnable ``--benchmarks``
#: value).
_DISPLAY: dict[str, str] = {
    "mbpp-sanitized": "mbpp",
    "livecodebench-codegeneration": "livecodebench",
    "human-eval-plus": "humaneval-plus",
    "human-eval": "humaneval",
    "tau-bench:airline": "tau-bench",
}

#: Preferred column order for tables (extras append alphabetically).
_BENCH_ORDER: tuple[str, ...] = (
    "humaneval",
    "humaneval-plus",
    "mbpp",
    "mbpp-plus",
    "math500",
    "livecodebench",
    "tau-bench",
)

#: Preferred row order for tables (extras append alphabetically).
_AGENT_ORDER: tuple[str, ...] = (
    "coding-agent",
    "react",
    "plan-execute",
    "reflexion",
    "tree-of-thought",
    "full-tools",
    "action-first",
    "minimal",
    "explore",
    "swebench",
    "retry-min",
    "lint-loop",
    "plan-act",
)

_SHORT: dict[str, str] = {
    "humaneval": "HE",
    "humaneval-plus": "HE+",
    "mbpp": "mbpp",
    "mbpp-plus": "mbpp+",
    "math500": "math",
    "livecodebench": "lcb",
    "tau-bench": "tau",
}

#: The depth run currently managed on Modal (rendered as "in flight" until its
#: receipt lands in data/).
_OBSERVATORY1_AGENTS = "coding-agent,react,plan-execute,reflexion,tree-of-thought"
_OBSERVATORY1_BENCHES = "mbpp,humaneval-plus,mbpp-plus,math500"
_OBSERVATORY1_LIMIT = 50
_MODEL = "glm-5.2[1m]"


class IntegrityError(Exception):
    """A data receipt violates the measurement-integrity invariants."""


#: Smallest cell size at which "every task completed cleanly and nothing
#: passed" stops being sampling noise and becomes the harness-gap signature
#: (``docs/playbooks/13-live-bench-runs.md``). Below this a 0/n cell is
#: unremarkable; at or above it, a real agent that never once satisfies the
#: grader means the grader — not the agent — is broken.
_UNIFORM_ZERO_MIN_TASKS = 5


@dataclass(frozen=True)
class Cell:
    """One (agent, benchmark) measurement read from a receipt file.

    Attributes:
        agent: The runner's id (``agent_id`` in the receipt).
        bench: Display benchmark name (normalized via ``_DISPLAY``).
        bench_raw: Benchmark id exactly as recorded in the receipt.
        total: Tasks graded in the cell.
        passed: Tasks that passed.
        pass_rate: ``passed / total`` as recorded.
        cost_usd: Dollar cost of the cell.
        status: Aggregate cell status (``completed`` | ``partial_error`` | ...).
        status_counts: Per-task terminal-status tally when the run recorded one.
        source: Receipt filename (``data/<source>``) — the provenance footnote.
    """

    agent: str
    bench: str
    bench_raw: str
    total: int
    passed: int
    pass_rate: float
    cost_usd: float
    status: str
    status_counts: dict[str, int] | None
    category: str
    source: str


@dataclass
class Inputs:
    """Everything loaded from data/, already validated.

    Attributes:
        files: The receipt paths that fed the page, sorted by name.
        flagship: Full-dataset flagship cells (the ``fullscore*`` runs).
        depth: Depth-matrix cells (the ``observatory*`` runs), possibly empty.
        breadth: The n=1 breadth-grid cells (``matrix-full-*``).
        breadth_model: The model recorded by the breadth receipt, if any.
    """

    files: list[Path] = field(default_factory=list)
    flagship: list[Cell] = field(default_factory=list)
    depth: list[Cell] = field(default_factory=list)
    breadth: list[Cell] = field(default_factory=list)
    breadth_model: str = ""


def _display_bench(raw: str) -> str:
    """Return the display name for a raw benchmark id.

    Args:
        raw: Benchmark id as recorded by the harness.

    Returns:
        The CLI-alias display name (falls back to ``raw`` unchanged).
    """
    return _DISPLAY.get(raw, raw)


def _uniform_zero_note(
    status: str, passed: int, total: int, counts: dict | None
) -> str:
    """Describe a clean-status uniform-zero cell, or return ``""``.

    A cell where every task reached a terminal ``completed`` status — no
    errors, no budget exhaustion, no timeouts — and *nothing* passed is the
    harness-gap signature this page exists to catch: the agent produced an
    answer for every task and the grader accepted none of them. Historically
    this has always been a broken grading contract (a checker defined but
    never invoked, a prompt/grader answer-shape mismatch), never a measured
    score, so it must abort generation rather than render as ``0.0%``.

    Cells that ran fewer than :data:`_UNIFORM_ZERO_MIN_TASKS` tasks are
    exempt: a 0/1 or 0/2 result is ordinary sampling noise. Cells whose tally
    contains any non-``completed`` status are exempt too — those already
    render as a lower bound, which is honest.

    Args:
        status: The cell's aggregate status.
        passed: Tasks that passed.
        total: Tasks graded.
        counts: The per-task ``status_counts`` tally, if the run recorded one.

    Returns:
        A diagnostic sentence when the cell is a clean-status uniform zero,
        otherwise the empty string.
    """
    if passed != 0 or total < _UNIFORM_ZERO_MIN_TASKS:
        return ""
    clean = set(counts) == {"completed"} if counts else status == "completed"
    if not clean:
        return ""
    tally = f"status_counts={{{_split(counts)}}}" if counts else f"status={status}"
    return (
        f"0/{total} with {tally} — every task ran to completion and none "
        "passed, the harness-gap signature (see "
        "docs/playbooks/13-live-bench-runs.md). Diagnose the adapter with a "
        "known-correct solution before publishing; a fake zero is not a score."
    )


def _validate_cell(raw: dict, source: str) -> None:
    """Enforce the verify_status.py integrity invariants on one raw cell.

    Args:
        raw: The cell dict as read from the receipt JSON.
        source: Receipt filename, for error messages.

    Raises:
        IntegrityError: If the cell claims passes despite an ``error`` status,
            reports more passes than tasks, carries a ``status_counts`` tally
            that does not sum to ``total``, a ``pass_rate`` inconsistent with
            ``passed / total``, or is a clean-status uniform zero (see
            :func:`_uniform_zero_note`).
    """
    status = raw.get("status", "")
    passed = int(raw.get("passed", 0))
    total = int(raw.get("total", 0))
    where = f"{source}: {raw.get('agent_id')} x {raw.get('benchmark')}"
    if status == "error" and passed > 0:
        raise IntegrityError(
            f"{where}: status=error with passed={passed} — an errored run "
            "cannot have produced graded passes (grader-invariant violation)"
        )
    if passed > total:
        raise IntegrityError(f"{where}: passed={passed} > total={total}")
    counts = raw.get("status_counts")
    if counts:
        tally = sum(int(v) for v in counts.values())
        if tally != total:
            raise IntegrityError(
                f"{where}: status_counts sums to {tally} but total={total}"
            )
    uniform_zero = _uniform_zero_note(status, passed, total, counts)
    if uniform_zero:
        raise IntegrityError(f"{where}: {uniform_zero}")
    rate = float(raw.get("pass_rate", 0.0))
    expect = (passed / total) if total else 0.0
    if not math.isclose(rate, expect, rel_tol=1e-6, abs_tol=1e-9):
        raise IntegrityError(
            f"{where}: pass_rate={rate} inconsistent with {passed}/{total}"
        )


def _load_file(path: Path) -> tuple[str, list[Cell], dict]:
    """Load one receipt file into validated cells.

    Args:
        path: The receipt JSON path.

    Returns:
        ``(role, cells, doc)`` where role is ``"breadth"`` | ``"depth"`` |
        ``"flagship"`` and doc is the parsed top-level object.

    Raises:
        IntegrityError: Propagated from per-cell validation.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    run_id = str(doc.get("run_id") or "")
    name = path.name
    if name.startswith("matrix-full"):
        role = "breadth"
    elif run_id.startswith("observatory") or name.startswith("modal-grid-observatory"):
        role = "depth"
    else:
        role = "flagship"
    cells: list[Cell] = []
    for raw in doc.get("cells", []):
        _validate_cell(raw, name)
        counts = raw.get("status_counts")
        cells.append(
            Cell(
                agent=str(raw.get("agent_id", "")),
                bench=_display_bench(str(raw.get("benchmark", ""))),
                bench_raw=str(raw.get("benchmark", "")),
                total=int(raw.get("total", 0)),
                passed=int(raw.get("passed", 0)),
                pass_rate=float(raw.get("pass_rate", 0.0)),
                cost_usd=float(raw.get("cost_usd", 0.0)),
                status=str(raw.get("status", "")),
                status_counts={str(k): int(v) for k, v in counts.items()} if counts else None,
                category=str(raw.get("category", "")),
                source=name,
            )
        )
    return role, cells, doc


def load_inputs(data_dir: Path, patterns: tuple[str, ...] = DEFAULT_PATTERNS) -> Inputs:
    """Discover, load, and validate every receipt feeding the page.

    Args:
        data_dir: Directory holding the ``data/*.json`` receipts.
        patterns: Glob patterns resolved (sorted) against ``data_dir``.

    Returns:
        The validated :class:`Inputs` bundle.

    Raises:
        IntegrityError: If any receipt fails validation, or no receipt matched.
    """
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(data_dir.glob(pattern)))
    files = sorted(dict.fromkeys(files), key=lambda p: p.name)
    if not files:
        raise IntegrityError(f"no data receipts matched {patterns} under {data_dir}")
    inputs = Inputs(files=files)
    for path in files:
        role, cells, doc = _load_file(path)
        if role == "breadth":
            inputs.breadth.extend(cells)
            inputs.breadth_model = str(doc.get("model") or inputs.breadth_model)
        elif role == "depth":
            inputs.depth.extend(cells)
        else:
            inputs.flagship.extend(cells)
    return inputs


def _quality(cell: Cell) -> tuple[int, str, str]:
    """Classify how citable a cell is, from its status tally.

    Args:
        cell: The measurement to classify.

    Returns:
        ``(rank, marker, basis)`` — rank 0 is best (EXACT), higher is weaker;
        marker is the short status label; basis is the provenance sentence.
    """
    counts = cell.status_counts
    if counts and set(counts) == {"completed"}:
        return 0, "✅ EXACT", f"EXACT — 0 errors (`{{completed: {counts['completed']}}}`)"
    if counts and not ({"error", "timeout"} & set(counts)):
        clean = counts.get("completed", 0)
        margin = 100.0 * (cell.total - clean) / cell.total if cell.total else 0.0
        return 1, "✅ ~EXACT", f"~EXACT — `{{{_split(counts)}}}` (≤{margin:.1f}% margin)"
    if counts is None and cell.status == "completed":
        return 2, "✅ clean", "clean status (run predates per-task `status_counts`)"
    return 3, "⚠️ lower bound", (
        f"lower bound — status `{cell.status}`, errored tasks count as misses"
        + ("" if counts else " (run predates `status_counts`)")
    )


def _pct(cell: Cell) -> str:
    """Format a cell's pass rate as a one-decimal percentage string."""
    return f"{100.0 * cell.pass_rate:.1f}%"


def _split(counts: dict[str, int]) -> str:
    """Format a status tally with ``completed`` first, then alphabetical."""
    ordered = sorted(counts.items(), key=lambda kv: (kv[0] != "completed", kv[0]))
    return ", ".join(f"{k}: {v}" for k, v in ordered)


def _order(values: set[str], preferred: tuple[str, ...]) -> list[str]:
    """Order names by a preferred list, unknown names appended alphabetically."""
    known = [v for v in preferred if v in values]
    extras = sorted(values - set(preferred))
    return known + extras


def _pick_flagship(cells: list[Cell]) -> list[Cell]:
    """Select the most citable flagship cell per benchmark.

    Args:
        cells: All flagship-run cells (may hold several runs per benchmark).

    Returns:
        One cell per benchmark — best quality rank first, newest receipt as the
        tiebreak — sorted for display (lower bounds last, then rate descending).
    """
    best: dict[str, Cell] = {}
    for cell in cells:
        if cell.agent != "coding-agent":
            continue
        cur = best.get(cell.bench)
        if cur is None:
            best[cell.bench] = cell
            continue
        q_new, q_cur = _quality(cell)[0], _quality(cur)[0]
        if q_new < q_cur or (q_new == q_cur and cell.source > cur.source):
            best[cell.bench] = cell
    rows = list(best.values())
    rows.sort(key=lambda c: (_quality(c)[0] >= 3, -c.pass_rate, c.bench))
    return rows


def _reproduce_block(lines: list[str]) -> list[str]:
    """Wrap reproduce commands in a fenced bash block under a standard heading."""
    return ["**Reproduce:**", "", "```bash", *lines, "```", ""]


def _render_flagship(rows: list[Cell]) -> list[str]:
    """Render section 1 — the flagship full-dataset scorecard."""
    out = [
        "## 1. Flagship full-dataset scorecard — `coding-agent`",
        "",
        "The assembled `chimera code` stack (`coding-agent`) over each benchmark's"
        f" **whole dataset**, hardened grader, model `{_MODEL}`, executed on Modal"
        " cloud. One row per benchmark; the Basis column is derived from the"
        " receipt's per-task status tally, and the Source column names the exact"
        " receipt.",
        "",
        "| Benchmark | n | Score | Basis | Source |",
        "|---|---:|---|---|---|",
    ]
    lcb_lower = False
    total_cost = 0.0
    for cell in rows:
        rank, _marker, basis = _quality(cell)
        total_cost += cell.cost_usd
        if rank >= 3:
            score = f"≥ {_pct(cell)} ({cell.passed}/{cell.total})"
        else:
            score = f"**{_pct(cell)}** ({cell.passed}/{cell.total})"
        if cell.bench == "livecodebench" and rank >= 3:
            lcb_lower = True
            basis += "*"
        out.append(f"| {cell.bench} | {cell.total} | {score} | {basis} | `data/{cell.source}` |")
    out.append("")
    if lcb_lower:
        out.extend(
            [
                "*livecodebench is a floor, not a score: its 175 contest-codegen"
                " tasks need ~14.5 h sequentially and the Modal cell timeout is"
                " 12 h, so the clean full-column re-run cannot finish in one"
                " container — errored tasks count as misses. A smaller-n run"
                " (e.g. n=50, ~4 h) would give an exact number; expected low"
                " either way (hard contest codegen).",
                "",
            ]
        )
    out.append(
        f"Receipts: {len(rows)} cells, **${total_cost:.2f}** total model spend"
        " (sum of the source cells' `cost_usd`)."
    )
    out.append("")
    out.extend(
        _reproduce_block(
            [
                "# small-n smoke of the same cells (any machine; model creds required;",
                "# drop --limit to run each full dataset):",
                "uvx --from chimera-run chimera bench-matrix --agents coding-agent \\",
                "  --benchmarks mbpp,humaneval-plus,mbpp-plus,math500,livecodebench \\",
                f'  --limit 5 --model "{_MODEL}"',
                "",
                "# how this data was actually produced — detached Modal grid",
                "# (survives disconnects; cells persist to a Volume):",
                "modal run --detach scripts/modal_bench_app.py::grid_detached \\",
                "  --run-id myscore --agents coding-agent \\",
                "  --benches mbpp,humaneval-plus,mbpp-plus,math500,livecodebench --limit 500",
                "modal run scripts/modal_bench_app.py::collect --run-id myscore",
            ]
        )
    )
    return out


def _render_depth(cells: list[Cell]) -> list[str]:
    """Render section 2 — the multi-agent depth matrix (or its in-flight note)."""
    out: list[str] = []
    if not cells:
        out.extend(
            [
                f"## 2. Depth matrix — 5 agents × 4 benchmarks, n={_OBSERVATORY1_LIMIT}"
                " (`observatory1`)",
                "",
            ]
        )
        out.extend(
            [
                "**Run `observatory1` is in flight — no number appears here until"
                " its receipt lands.** The depth grid — agents"
                f" `{_OBSERVATORY1_AGENTS}` × benchmarks `{_OBSERVATORY1_BENCHES}`"
                f" at n={_OBSERVATORY1_LIMIT}, model `{_MODEL}` — is running"
                " detached on Modal; cells persist to the `chimera-bench-results`"
                " Volume as they finish. When `data/modal-grid-observatory1-<ts>.json`"
                " exists, this section regenerates from it:",
                "",
                "```bash",
                "modal run scripts/modal_bench_app.py::collect --run-id observatory1",
                "uv run python scripts/render_observatory.py",
                "```",
                "",
            ]
        )
    else:
        latest: dict[tuple[str, str], Cell] = {}
        for cell in cells:
            key = (cell.agent, cell.bench)
            cur = latest.get(key)
            if cur is None or cell.source > cur.source:
                latest[key] = cell
        agents = _order({c.agent for c in latest.values()}, _AGENT_ORDER)
        benches = _order({c.bench for c in latest.values()}, _BENCH_ORDER)
        sources = sorted({c.source for c in latest.values()})
        refs = {s: i + 1 for i, s in enumerate(sources)}
        multi = len(sources) > 1
        out.extend(
            [
                f"## 2. Depth matrix — {len(agents)} agents ×"
                f" {len(benches)} benchmarks (`observatory` runs)",
                "",
                "Every architecture below raced the same benchmarks under the same"
                f" budget, model `{_MODEL}`, on Modal. `≥` marks lower-bound cells"
                " (errors counted as misses); clean cells are exact at their n"
                " (shown per cell as passed/n).",
                "",
                "| Agent | " + " | ".join(benches) + " |",
                "|---|" + "---|" * len(benches),
            ]
        )
        total_cost = 0.0
        dirty: list[str] = []
        for agent in agents:
            label = f"**{agent}** ★" if agent == "coding-agent" else agent
            row = [label]
            for bench in benches:
                cell = latest.get((agent, bench))
                if cell is None:
                    row.append("—")
                    continue
                total_cost += cell.cost_usd
                rank, _marker, _basis = _quality(cell)
                if cell.total <= 0:
                    # A cell that ran no task has no score. Rendering "0/0
                    # (0.0%)" would read as a measured zero — the exact
                    # false-precision this page exists to prevent.
                    row.append("error")
                    counts = cell.status_counts
                    split = _split(counts) if counts else f"status {cell.status}"
                    why = f", {cell.category}" if cell.category else ""
                    dirty.append(f"`{agent} × {bench}` — no result ({split}{why})")
                    continue
                text = f"{cell.passed}/{cell.total} ({_pct(cell)})"
                if rank >= 3:
                    text = "≥ " + text
                if multi:
                    text += f" `[{refs[cell.source]}]`"
                if rank in (1, 3):
                    counts = cell.status_counts
                    split = _split(counts) if counts else f"status {cell.status}"
                    dirty.append(f"`{agent} × {bench}` — {split}")
                row.append(text)
            out.append("| " + " | ".join(row) + " |")
        out.append("")
        if dirty:
            out.append("Cells not fully clean: " + "; ".join(sorted(dirty)) + ".")
            out.append("")
        # Requested-but-absent work is reported, never silently dropped: a row
        # or column that vanishes because its cells failed to land would make
        # the grid look complete when it is not.
        requested_agents = [a.strip() for a in _OBSERVATORY1_AGENTS.split(",") if a.strip()]
        missing_agents = [a for a in requested_agents if a not in agents]
        missing_cells = [
            f"`{a} × {b}`"
            for a in agents
            for b in benches
            if (a, b) not in latest
        ]
        if missing_agents or missing_cells:
            notes = []
            if missing_agents:
                notes.append(
                    "requested but absent entirely: "
                    + ", ".join(f"`{a}`" for a in missing_agents)
                )
            if missing_cells:
                notes.append("missing cells: " + ", ".join(sorted(missing_cells)))
            out.append(
                "**Incomplete run.** "
                + "; ".join(notes)
                + ". Those cells produced no receipt (failure or timeout) and are"
                " excluded from the table rather than scored — the grid above is"
                " what landed, not what was requested."
            )
            out.append("")
        if multi:
            src_list = " · ".join(f"`[{refs[s]}]` = `data/{s}`" for s in sources)
        else:
            src_list = " · ".join(f"`data/{s}`" for s in sources)
        out.append(
            f"Receipts: {len(latest)} cells, **${total_cost:.2f}** total model"
            f" spend. Source{'s' if multi else ''}: {src_list}."
        )
        out.append("")
    out.extend(
        _reproduce_block(
            [
                "uvx --from chimera-run chimera bench-matrix \\",
                f"  --agents {_OBSERVATORY1_AGENTS} \\",
                f"  --benchmarks {_OBSERVATORY1_BENCHES} \\",
                f'  --limit {_OBSERVATORY1_LIMIT} --model "{_MODEL}"',
                "",
                "# or detached on Modal (how observatory1 runs):",
                "modal run --detach scripts/modal_bench_app.py::grid_detached \\",
                f"  --run-id observatory1 --agents {_OBSERVATORY1_AGENTS} \\",
                f"  --benches {_OBSERVATORY1_BENCHES} --limit {_OBSERVATORY1_LIMIT}",
                "modal run scripts/modal_bench_app.py::collect --run-id observatory1",
            ]
        )
    )
    return out


def _render_breadth(cells: list[Cell], model: str) -> list[str]:
    """Render section 3 — the 13 × 7 breadth grid at n=1."""
    latest: dict[tuple[str, str], Cell] = {}
    for cell in cells:
        latest[(cell.agent, cell.bench)] = cell
    agents = _order({c.agent for c in latest.values()}, _AGENT_ORDER)
    benches = _order({c.bench for c in latest.values()}, _BENCH_ORDER)
    sources = sorted({c.source for c in latest.values()})
    solved_all = 0
    total_cost = 0.0
    out = [
        f"## 3. Breadth grid — {len(agents)} agents × {len(benches)} benchmarks, n=1",
        "",
        "The instrument demonstration: **every** replicated architecture against"
        f" **every** staged benchmark, one task each, model `{model}`, identical"
        " budget. n=1 proves the harness runs the full roster — it ranks nothing;"
        " depth (sections 1–2) is where ranking happens. `✓` = solved, `·` ="
        " failed.",
        "",
        "| Agent | " + " | ".join(_SHORT.get(b, b) for b in benches) + " |",
        "|---|" + ":-:|" * len(benches),
    ]
    wins_by_agent: dict[str, int] = {}
    for agent in agents:
        label = f"**{agent}** ★" if agent == "coding-agent" else agent
        row = [label]
        wins = 0
        for bench in benches:
            cell = latest.get((agent, bench))
            if cell is None:
                row.append("—")
                continue
            total_cost += cell.cost_usd
            mark = "✓" if cell.passed >= 1 else "·"
            wins += 1 if cell.passed >= 1 else 0
            row.append(mark)
        wins_by_agent[agent] = wins
        solved_all += 1 if wins == len(benches) else 0
        out.append("| " + " | ".join(row) + " |")
    legend = " · ".join(f"{_SHORT.get(b, b)}={b}" for b in benches if _SHORT.get(b, b) != b)
    summary = (
        f"{solved_all}/{len(agents)} agents solve {len(benches)}/{len(benches)} at n=1."
    )
    if wins_by_agent.get("lint-loop") == 0:
        summary += (
            " `lint-loop`'s zero row is honest — it writes no solution file on"
            " from-scratch codegen (a known agent-behavior gap, not a grading"
            " bug)."
        )
    out.extend(
        [
            "",
            f"Legend: {legend}.",
            "",
            summary,
            "",
            f"Receipts: {len(latest)} cells, **${total_cost:.2f}** total model"
            f" spend. Source: " + " · ".join(f"`data/{s}`" for s in sources) + ".",
            "",
        ]
    )
    agent_arg = ",".join(agents)
    bench_arg = ",".join(benches)
    out.extend(
        _reproduce_block(
            [
                "uvx --from chimera-run chimera bench-matrix \\",
                f"  --agents {agent_arg} \\",
                f"  --benchmarks {bench_arg} \\",
                f'  --limit 1 --model "{model}"',
            ]
        )
    )
    return out


def render(inputs: Inputs) -> str:
    """Render the whole observatory page from validated inputs.

    Args:
        inputs: The loaded receipt bundle.

    Returns:
        The complete Markdown document (frontmatter included), newline-terminated.
    """
    newest = max(p.stat().st_mtime for p in inputs.files)
    data_date = datetime.fromtimestamp(newest, tz=timezone.utc).date()
    flagship_rows = _pick_flagship(inputs.flagship)
    lines: list[str] = [
        "---",
        'title: "The Observatory — verified agent × benchmark results"',
        'description: "Chimera\'s public scoreboard: every number generated from a'
        " committed data receipt, labeled EXACT or lower-bound from per-task status"
        ' tallies, each with the exact command that reproduces it."',
        "---",
        "",
        "<!--",
        "  GENERATED FILE — do not edit by hand; edits are overwritten.",
        "  Source of truth: data/*.json receipts + scripts/render_observatory.py.",
        "",
        "  Regenerate (rewrites this file AND the byte-identical site copy):",
        "      uv run python scripts/render_observatory.py",
        "",
        "  Freshness gate (exit 1 when this page is stale vs data/):",
        "      uv run python scripts/render_observatory.py --check",
        "-->",
        "",
        "# The Observatory",
        "",
        "Chimera's results page. Every number below was generated from a JSON"
        " receipt committed in `data/` by `scripts/render_observatory.py` —"
        " nothing is hand-typed. Each cell names its source file, carries an"
        " EXACT or lower-bound label derived from per-task status tallies, and"
        " sits next to the command that reproduces it. **Don't trust us — run"
        " the command.**",
        "",
        "Companions in the repo: `docs/benchmarks/modal-cloud-benches.md` (how"
        " the runs execute) · `docs/progress/benchmark-matrix.md` (operations"
        " guide: how to re-run or extend any column) ·"
        " `scripts/verify_status.py` (the live integrity audit).",
        "",
        "## How to read these numbers",
        "",
        "Every grid cell records a per-task terminal-status tally"
        " (`status_counts`, e.g. `{completed: 427}` or `{completed: 496,"
        " budget_exhausted: 4}`). That tally is what separates a score from a"
        " floor: a cell is labeled **EXACT** only when it shows every task"
        " completing cleanly — zero infrastructure errors hiding in the"
        " denominator.",
        "",
        "When a cell mixes clean runs with infrastructure errors, its status is"
        " `partial_error` and every errored task counts as a **miss, never a"
        " pass**. The resulting number is a **lower bound** (written `≥`): the"
        " agent's true rate is at least that high, and the cell is not citable"
        " as a score. Runs that predate `status_counts` can only ever be lower"
        " bounds, however clean they look.",
        "",
        "The graders are hardened so failure cannot masquerade as success:"
        " empty output grades False, wrong output grades False, and the checker"
        " is actually invoked — an earlier grader bug silently passed *any*"
        " HumanEval+ output, and every number measured under it was invalidated"
        " and re-measured rather than kept. The same discipline gates this very"
        " page: a receipt containing an `error`-status cell that claims passes"
        " **aborts generation**. So does a *uniform zero* — a cell of 5+ tasks"
        " that all reached `completed` yet passed none. That pattern has always"
        " been a broken grading contract rather than a measured 0%, so it must"
        " be diagnosed against a known-correct solution before anything ships.",
        "",
    ]
    lines.extend(_render_flagship(flagship_rows))
    lines.extend(_render_depth(inputs.depth))
    lines.extend(_render_breadth(inputs.breadth, inputs.breadth_model or _MODEL))
    src_list = " · ".join(f"`data/{p.name}`" for p in inputs.files)
    lines.extend(
        [
            "---",
            "",
            "*Generated by `scripts/render_observatory.py` from"
            f" {len(inputs.files)} data receipts: {src_list}.*",
            "",
            f"*Data date: {data_date.isoformat()} (newest receipt's mtime — the"
            " generator never reads the wall clock, so regenerating over"
            " unchanged inputs is byte-identical).*",
            "",
        ]
    )
    return "\n".join(lines)


_DATE_LINE_PREFIX = "*Data date: "


def _masked(text: str) -> str:
    """Neutralize the mtime-derived date line for staleness comparison.

    Git does not preserve file mtimes, so a fresh clone renders a different
    date line than the committed page while every number stays identical. The
    ``--check`` gate therefore compares everything except that one line.

    Args:
        text: A rendered or committed page.

    Returns:
        The page with the data-date line replaced by a fixed placeholder.
    """
    return "\n".join(
        "*Data date: <masked>*" if line.startswith(_DATE_LINE_PREFIX) else line
        for line in text.splitlines()
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` on success, ``1`` when ``--check`` detects drift, ``2`` on a data
        integrity failure.
    """
    parser = argparse.ArgumentParser(
        description="Render THE OBSERVATORY results page from data/ receipts.",
        epilog=(
            "exit codes: 0 fresh/written · 1 --check drift · 2 receipt failed "
            "integrity validation"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "verify the committed page matches the receipts (ignoring only the "
            "mtime-derived date line); write nothing"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO / "data",
        help="directory holding the *.json receipts (default: data/)",
    )
    parser.add_argument(
        "--inputs",
        default=None,
        help=(
            "comma-separated glob patterns (relative to --data-dir) overriding "
            "the default receipt set: " + ", ".join(DEFAULT_PATTERNS)
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "docs" / "benchmarks" / "observatory.md",
        help="canonical page path (default: docs/benchmarks/observatory.md)",
    )
    parser.add_argument(
        "--site-out",
        type=Path,
        default=REPO / "site" / "src" / "content" / "docs" / "benchmarks" / "observatory.md",
        help="byte-identical site copy (default: site/src/content/docs/benchmarks/observatory.md)",
    )
    args = parser.parse_args(argv)

    patterns = (
        tuple(p.strip() for p in args.inputs.split(",") if p.strip())
        if args.inputs
        else DEFAULT_PATTERNS
    )
    try:
        inputs = load_inputs(args.data_dir, patterns)
        page = render(inputs)
    except IntegrityError as exc:
        print(f"INTEGRITY FAILURE — page NOT generated: {exc}", file=sys.stderr)
        return 2

    targets: list[Path] = [args.out, args.site_out]
    if args.check:
        want = _masked(page)
        stale = [
            str(t)
            for t in targets
            if not t.exists() or _masked(t.read_text(encoding="utf-8")) != want
        ]
        if stale:
            print("observatory: STALE vs data/ receipts:", file=sys.stderr)
            for t in stale:
                print(f"  {t}", file=sys.stderr)
            print(
                "run `uv run python scripts/render_observatory.py` to refresh.",
                file=sys.stderr,
            )
            return 1
        print(f"observatory: fresh ({len(inputs.files)} receipts, {len(targets)} copies)")
        return 0

    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(page, encoding="utf-8")
        print(f"wrote {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
