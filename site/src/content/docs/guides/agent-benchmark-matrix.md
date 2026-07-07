---
title: "Agent × Benchmark Matrix"
description: "Run any agent against any benchmark under one shared budget, grader, and sandbox — bench-matrix, bench-fidelity, and bench-fetch."
---

Run any agent against any benchmark, with the agent as the only variable. The
matrix crosses N agents against M benchmarks under one shared budget, grader, and
sandbox, so a benchmark column measures the agent and nothing else. Three
subcommands cover the workflow: `bench-matrix` runs the grid, `bench-fidelity`
measures a replica against the real agent it mirrors, and `bench-fetch` stages the
public datasets.

---

## `chimera bench-matrix`

The grid: N agents × M benchmarks, one budget, one grader, one sandbox per task.

```bash
chimera bench-matrix \
    --agents react,coding-agent \
    --benchmarks human-eval \
    --model "glm-5.2[1m]"
```

That runs both the `react` loop and the assembled `coding-agent` on HumanEval
under identical conditions and prints a scoreboard. Widen either axis to fill the
grid:

```bash
chimera bench-matrix \
    --agents react,plan-execute,coding-agent,swebench \
    --benchmarks human-eval,mbpp,livecodebench \
    --model glm-5.2 \
    --max-tool-calls 40 --max-cost 0.50 \
    --format markdown --output matrix.json
```

Key flags:

| Flag | Purpose |
|------|---------|
| `--agents` | Comma-separated agent ids from the runner registry. |
| `--benchmarks` | Comma-separated benchmark names. |
| `--model` | One model shared by every cell (default `glm-5`). |
| `--limit` | Per-benchmark task cap. |
| `--max-tool-calls` / `--max-llm-calls` / `--max-wall-clock` / `--max-cost` | The shared per-task budget. |
| `--env` | Per-task sandbox: `local` (fresh temp dir, default), `none`, or `modal` (fresh cloud sandbox, optionally `--modal-gpu`). |
| `--registry` | JSON registry files merged over the built-ins to add more agents. |
| `--format` | `terminal`, `json`, or `markdown`. |
| `--output` | Also write the full report JSON. |

Agent and benchmark names are validated before any provider is constructed, so a
typo fails fast and offline.

---

## The built-in roster

`chimera bench-matrix` resolves agents from the runner registry, which ships **13
built-in agents** spanning three internal axes (`default_agent_specs` in
`chimera/eval/runners/registry.py`):

- **Four loop postures** — `react`, `plan-execute`, `reflexion`, `tree-of-thought`.
- **Six assembly presets** — `coding-agent` (the `chimera code` flagship),
  `full-tools`, `action-first`, `minimal`, `explore`, and `swebench`, each driven
  through the same adapter the product uses.
- **Three loop styles** — `retry-min`, `lint-loop`, `plan-act`, composed from the
  in-tree agent-style presets.

Extend the roster by passing `--registry` JSON files that add more presets,
codename CLIs, or external agents.

---

## Benchmarks

**26 public benchmark adapters** are registered — HumanEval, HumanEval+,
HumanEval-X, MBPP, MBPP+, LiveCodeBench, MATH-500, SWE-bench (Lite / Verified /
Polybench / Lancer), SWT-bench, Multi-SWE-bench, Harbor, ProgramBench, and more.

**7 run out of the box.** Two carry bundled data (`human-eval`, `math500`); five
more are one `chimera bench-fetch` away. The rest need a manually staged
`--dataset` because of size or licence constraints.

---

## `chimera bench-fetch`

Stage public datasets locally so a wired benchmark becomes runnable with no
`--dataset` flag — a fetched dataset is auto-discovered on the next run.

```bash
chimera bench-fetch --list          # show stageable datasets
chimera bench-fetch livecodebench   # stage one
chimera bench-fetch --all           # stage every fetchable dataset
```

Fetchable out of the box: `humaneval-plus`, `livecodebench`, `mbpp`, `mbpp-plus`,
and `swe-bench`. Combined with the two bundled benches, that is the 7 that run
without manual staging.

---

## `chimera bench-fidelity`

Turn "we replicated agent X" from a claim into a measured number. One internal
replica and the real agent it mirrors run on the **same** benchmarks, model,
budget, and sandbox, and the tool reports the pass-rate delta plus a coarse
trajectory-divergence proxy.

```bash
chimera bench-fidelity \
    --replica codex --real react \
    --benchmarks human-eval,mbpp \
    --model glm-5.2 \
    --max-tool-calls 40 --max-cost 0.50 \
    --format markdown --output fidelity.json
```

`--replica` and `--real` both resolve from the runner registry; every other flag
mirrors `bench-matrix`.

---

## A worked example, end to end

```bash
# 1. Stage a dataset (only needed for the fetchable benches)
chimera bench-fetch human-eval-plus

# 2. Race the react loop against the assembled coding agent
chimera bench-matrix \
    --agents react,coding-agent \
    --benchmarks human-eval \
    --model "glm-5.2[1m]" \
    --format markdown
```

The scoreboard tells you which agent won the column and at what cost — with the
model, budget, grader, and sandbox held equal across both.

---

## Next steps

- [The Coding Agent](/chimera/guides/coding-agent/) — the `coding-agent` roster entry, in depth.
- [Use the REPL](/chimera/guides/use-the-repl/) — drive that same agent interactively.
