---
title: The Full Grid — 13 Agents × 7 Benchmarks, One Command Surface (glm-5.2[1m])
description: Every built-in agent architecture crossed against every runnable benchmark under one budget, grader, and sandbox — 91 live cells for $0.78, including the grader bug the grid caught before it judged any agent.
---

# The Full Grid — 13 agents × 7 benchmarks on glm-5.2[1m]

The milestone artifact of the agent × benchmark matrix: **every** built-in
agent architecture (4 loops, 5 assembly presets, 3 loop styles, plus the
`chimera code` flagship) crossed against **every** runnable benchmark, under
one per-task budget (15 tool calls / 15 LLM calls / $0.15), one grader per
column, fresh env per task, answer-contract + artifact-harvest on. 91 live
cells, **zero errors, $0.78 total**.

**What this is:** proof that the whole surface runs, grades, and budgets
end-to-end — the instrument demonstration. **What this is not:** a
leaderboard. Each cell is n=1 (the benchmark's first task), so uniform 100s
mean "the pipeline is sound," not "all agents are equal." Depth runs come
next; the instrument now makes them one command.

## Results (pass %, `*` = budget_exhausted)

| Agent | he | he+ | mbpp | mbpp+ | m500 | lcb | tau | row $ |
|---|---|---|---|---|---|---|---|---|
| react | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.047 |
| plan-execute | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.062 |
| reflexion | 100 | 100 | 100 | 100 | 100 | 100 | 100 | **0.023** |
| tree-of-thought | 100 | 100 | 100 | 100 | 100 | 100 | 100* | 0.090 |
| coding-agent | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.077 |
| full-tools | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.049 |
| action-first | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.039 |
| minimal | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.071 |
| explore | 100 | 100 | 100 | 100 | 100 | 100 | 100 | **0.023** |
| swebench | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.058 |
| retry-min | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.043 |
| lint-loop | 0* | 0* | 0* | 0* | 0* | 0* | 0* | 0.113 |
| plan-act | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 0.084 |

Columns: HumanEval · HumanEval+ · MBPP · MBPP+ · MATH-500 · LiveCodeBench
(newest slice, public-test grading) · τ-bench airline. Data:
`data/matrix-full-glm52.json` (per-cell status/cost/tool-calls/budget flags).

## The headline: the grid's first catch was its own harness

The first pass produced four **uniform-zero columns** (he+/mbpp/mbpp+/lcb: 0%
for all 13 agents). A uniform-zero column is a harness-gap signature, not an
agent signal — and refusing to publish it paid off: those four graders were
executing raw markdown as Python, so *correct but fenced* answers failed
(fenced canonical solutions graded `False`). One shared fence-extractor later
(commit `2c41ad1`, regression-tested), the same columns re-ran to the numbers
above. **The measurement instrument caught its own grader bug before it judged
any agent — exactly what it exists to do.**

## What the grid actually differentiates (even at n=1)

- **Cost spread is real: ~4×** between the cheapest full-solvers (reflexion,
  explore — $0.023/row) and the dearest (tree-of-thought $0.090, plan-act
  $0.084). Same tasks, same model, same budget — architecture is the only
  variable.
- **Budgets bite visibly**: 8 cells hit `budget_exhausted`, including
  tree-of-thought × τ-bench *after solving it* — enforcement working as
  designed, not decoration.
- **lint-loop 0/7 is kept, not hidden**: traced to agent behavior (it writes
  no files on codegen tasks and fixates on lint output — zero `write_file`
  calls), the known backlog item. An honest instrument shows its one honest
  failure.

## Provenance

- Model `glm-5.2[1m]`; per-task `BudgetSpec(max_tool_calls=15, max_llm_calls=15,
  max_cost_usd=0.15)`; fresh temp-dir `LocalEnvironment` per task;
  `FINAL_ANSWER_CONTRACT` + `harvest_env_artifacts` on (defaults).
- The four fence-fixed columns were re-run post-fix (13 agents each); the
  other three columns are from the original run — same code paths otherwise,
  identical budget. Recorded in the data file's `note`.
- swe-bench is excluded: repo-fix tasks are meaningless without per-task repo
  containers; that column joins when the docker env path is wired.

## Next

Depth over breadth: n=25+ per cell on the contrast-rich columns, the external
agent rows (`*-cli` registry entries) once their tools are installed, and the
replica-vs-real fidelity pairs — all the same one-command surface.
