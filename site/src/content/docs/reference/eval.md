---
title: "chimera.eval"
description: "Reference for chimera.eval — Harness, Benchmark ABC, metrics, built-in benchmarks (SWE-bench, HumanEval, AIMO)."
---

`chimera.eval` is the evaluation harness: run an agent against a
benchmark, compute metrics, write reports.

## Top-level exports

```python
from chimera.eval import (
    Harness,
    Benchmark,
    pass_at_k,
    resolve_rate,
    avg_cost,
)
from chimera.eval.benchmarks import (
    SWEBenchBenchmark,
    HumanEvalBenchmark,
    AIMOBenchmark,
    CustomBenchmark,
)
```

| Symbol | Purpose |
|---|---|
| `Harness` | Drives an agent through a benchmark. `Harness(agent_factory, benchmark).run(limit=...)`. |
| `Benchmark` | ABC. Implement `tasks()` and `score(task, result)`. |
| `pass_at_k(results, k)` | Pass@k metric. |
| `resolve_rate(results)` | Fraction of tasks marked resolved. |
| `avg_cost(results)` | Mean USD per task. |
| `SWEBenchBenchmark` | SWE-bench / SWE-bench Lite loader. |
| `HumanEvalBenchmark` | HumanEval (164 problems). |
| `AIMOBenchmark` | AIMO math benchmark. |
| `CustomBenchmark` | Bring-your-own task list. |

Plug a custom benchmark in by subclassing `Benchmark`.

## See also

- [Benchmarking playbook](/playbooks/07-benchmarking/) for end-to-end runs.
- [`chimera.workflows`](/reference/workflows/) for benchmark-adjacent
  workflows (CIFix, ReviewOrchestrator, Researcher, ...).
