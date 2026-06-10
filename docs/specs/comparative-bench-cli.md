# `chimera bench compare` — Controlled Comparative CLI

**Date:** 2026-05-28
**Status:** Proposal
**Layer:** 8 (CLI) over 5 (Evaluation)
**Team roles:** `planner` (budget-enforcement audit), `executor` (CLI + plumbing), `reviewer` (matrix correctness), `researcher` (cross-loop budget semantics)
**Depends on:** [harbor-task-adapter](harbor-task-adapter.md) for DeepSWE matrices; existing `ComparativeEval` in `chimera/eval/comparative.py`
**Unblocks:** the headline mission deliverable

## Problem

Chimera's mission is the controlled comparative matrix for coding agents — same model, same loop type, same step/latency budget, different agent architectures. `ComparativeEval` exists as a library primitive but only programmatically; there is no CLI that produces the matrix in one command. Worse: it is unclear whether step/latency budgets are enforced uniformly across replicated agents whose loops differ in their natural unit of "step" (ReAct counts thought-action pairs; Plan-and-Execute counts plan steps; Reflexion counts attempt iterations). Without uniform budget enforcement, the "controlled" claim is hollow and any matrix Chimera produces is rejectable.

## What This Enables

- One-command comparative runs: `chimera bench compare --agents A,B,C,D --model M --task T --max-tool-calls N`.
- Publishable matrices: `pass_rate × cost × wall_clock × tool_call_count × N agents` against the same task pool.
- Reproducible: same seed, same model, same budget → same matrix.
- The kick-ass deliverable the field cannot otherwise produce.

## Design Sketch

### BudgetSpec

The universal step unit is **tool calls**, because every Chimera replica routes through `chimera/core/tool_executor.py`. Wall-clock and dollar caps act as orthogonal guards.

```python
@dataclass(frozen=True)
class BudgetSpec:
    max_tool_calls: int | None = None        # primary normalized unit
    max_llm_calls: int | None = None         # API turn count
    max_wall_clock_sec: float | None = None  # latency budget
    max_cost_usd: float | None = None        # dollar cap
    early_stop_on_first_hit: bool = True

    def is_exhausted(self, tally: BudgetTally) -> tuple[bool, str | None]: ...
```

### Plumbing

Add `budget` to `LoopConfig`:

```python
@dataclass
class LoopConfig:
    ...
    budget: BudgetSpec | None = None
```

Each loop in `chimera/core/loops/` checks budget after every tool call. On hit, the loop emits a `BudgetExhausted` event and returns final state with status `budget_exhausted` (distinct from `task_failed`).

### CompareReport

Extends existing `ComparisonReport`:

```python
@dataclass
class CompareReport(ComparisonReport):
    budget: BudgetSpec
    model: str
    task_pool: str           # benchmark identifier, e.g. "harbor:deep-swe-10"
    seed: int
    trajectory_paths: dict[str, list[Path]]
    budget_hits: dict[str, int]
```

### CLI Surface

```bash
chimera bench compare \
    --agents aider,swe-agent,opencode,cline \
    --model glm-5 \
    --task harbor:/path/to/deep-swe/tasks?n=10 \
    --max-tool-calls 30 \
    --max-wall-clock 600 \
    --max-cost 5.00 \
    --seed 0 \
    --output report.html
```

### Output Formats

- Terminal: ANSI matrix (pass_rate, mean_cost, mean_wall_clock, budget_hits per agent).
- JSON: machine-readable for plotting / further analysis.
- HTML: standalone report with sortable matrix, per-task drill-down.
- Markdown: paste-into-issue format.

## File Layout

- `chimera/cli/bench_compare.py` — CLI subcommand.
- `chimera/eval/budget.py` — `BudgetSpec`, `BudgetTally`, `BudgetEnforcer` mixin.
- `chimera/eval/comparative.py` — extend with `CompareReport`, budget plumbing.
- `chimera/core/loop_config.py` — add `budget: BudgetSpec | None`.
- `chimera/core/loops/*.py` — wire budget checks into each loop type (ReAct, PlanAndExecute, Reflexion, TreeOfThought).
- `tests/eval/test_budget_enforcement.py` — verify each loop type honors budget at tool-call granularity.
- `tests/cli/test_bench_compare.py` — CLI smoke tests against fixture benchmarks.
- `tests/cli/test_bench_compare_live.py` — gated 4-agent × 10-task live test.

## Acceptance Criteria

- [ ] `BudgetSpec` honored by every loop type in `chimera/core/loops/`.
- [ ] Budget enforcement verified with a deterministic test where the budget hit is forced (mock provider returns N tool calls).
- [ ] CLI runs 4 agents against `harbor:deepswe?n=10` with the same budget, produces an HTML matrix.
- [ ] Same seed reproduces an identical matrix on rerun.
- [ ] Budget hits flagged distinctly from task failures in the report (separate column, not conflated).
- [ ] Per-agent trajectory paths recorded for offline inspection.

## Test Strategy

- **Unit:** `BudgetSpec.is_exhausted` edge cases (None fields, exact-bound, off-by-one).
- **Loop integration:** for each loop type, a test that injects a mock provider scripted to call N tools and verifies the loop stops at N=budget.
- **CLI smoke:** 2-agent comparative against HumanEval-5 with `max_tool_calls=10`, GLM-5.
- **Live matrix:** 4-agent DeepSWE-10 matrix against GLM-5.

## Open Questions

- Granularity: what happens if an agent exceeds budget mid-LLM-call (the LLM proposed a tool call that would be the (N+1)th)? Initial choice: allow the call to start; check budget after completion; report it as the call that tipped the budget.
- Retry policy on budget hit: treat as failure (no retry) and surface in the `budget_hits` column. Retries would confound the controlled comparison.
- How to handle agents that ignore `LoopConfig.budget` (third-party replicas not wired). Initial choice: hard error at CLI startup with a list of non-compliant agents.

## Out of Scope

- New benchmark adapters ([harbor-task-adapter](harbor-task-adapter.md)).
- ATIF trajectory format ([atif-trajectory-emission](atif-trajectory-emission.md)).
- A web UI for matrices (HTML report is sufficient initially).

## References

- Mission: see `README.md` and `docs/philosophy.md` — control the variables (same model, same harness, same task) so agent comparisons are apples-to-apples.
- Existing primitive: `chimera/eval/comparative.py`.
- Loop types: `chimera/core/loops/`.
