---
title: "SWE-Lancer"
---

# SWE-Lancer (freelance bug fixes)

`SWE-Lancer` (OpenAI, 2025) evaluates coding agents on 1,400+ real-world
freelance Upwork tickets totaling over $1M in payouts. Two task categories:

- `ic_swe` — write a fix end-to-end against a Playwright test harness.
- `swe_manager` — pick the best of N proposed fixes (multiple choice).

The headline metric is **dollar-weighted resolve rate**:
`sum(payout for passed tasks) / sum(all payouts)`.

References:

- Paper: arXiv:2502.12115
- GitHub: <https://github.com/openai/SWELancer-Benchmark>

## Status: SCAFFOLD

| Surface | State |
| --- | --- |
| `SWELancerTask` dataclass (id, payout, category, choices, ...) | DONE |
| Loader (JSON / JSONL, category + min_payout filters, limit) | DONE |
| `grade_manager_choice(task, idx)` for `swe_manager` | DONE |
| `dollar_weighted_pass_rate(results)` headline metric | DONE |
| Live grading for `ic_swe` (Docker + Playwright harness) | NotImplementedError — follow-up |
| Discoverable via `chimera eval --benchmark swe-lancer` | DONE |

`evaluate(...)` raises `NotImplementedError` for the live path so misuse
is loud. For `swe_manager` tasks, call `grade_manager_choice` directly —
no environment required.

## Quick start

```python
from chimera.eval.benchmarks import SWELancer

bench = SWELancer(
    dataset_path="swe-lancer.jsonl",
    category="ic_swe",
    min_payout=100.0,   # focus on $100+ tickets
)
print(bench.name())                # "swe-lancer-ic_swe"

# Pre-graded results → headline metric
rate = bench.dollar_weighted_pass_rate([("sl_1", True), ("sl_2", False)])
```

## Live integration plan

1. Reuse `chimera.env.docker.DockerEnvironment` to provision the upstream
   harness image per task.
2. Write a `SWELancerRunner` analogous to `MultiSWEBench` runners that
   knows how to invoke the Playwright entrypoint and parse the JUnit-XML
   result.
3. Wire `evaluate` to call the runner (replace the
   `NotImplementedError`).
