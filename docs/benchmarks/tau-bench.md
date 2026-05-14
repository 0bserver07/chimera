---
title: "tau-bench"
description: "Multi-turn tool-use evaluation across business domains (airline, retail, telecom, banking). The headline 'can an agent actually use tools?' benchmark."
---

# tau-bench (tool use + business tasks)

[`tau-bench`](https://github.com/sierra-research/tau-bench) (Sierra Research, 2024) tests whether an agent can carry out **multi-turn business tasks** by calling tools against simulated APIs. Each task is a conversation with a simulated user; grading compares the database state at end-of-conversation to a target state.

References:

- GitHub: <https://github.com/sierra-research/tau-bench>
- Paper: arXiv:2406.12045
- Successor `tau2-bench`: <https://github.com/sierra-research/tau2-bench>

## Status: TODO (adapter wired; not benchmarked yet)

| Run | Score |
| --- | --- |
| Chimera | NOT RUN — depends on upstream `tau-bench` install for grading. |

## Domains

| Domain | Tasks | What it tests |
| --- | --- | --- |
| `airline` | ~50 | Bookings, cancellations, rebooking. |
| `retail` | ~115 | Orders, returns, exchanges. |
| `telecom` | ~50 | Plan changes, troubleshooting flows. |
| `banking` | ~70 | Transfers, disputes, statements. |
| `mock` | ~5 | Minimal test domain; no upstream install needed. |

## How to run

```bash
# Install upstream (for the simulated user + DB)
pip install tau-bench
```

```python
from chimera.eval.benchmarks import TauBench
from chimera.eval.harness import Harness

bench = TauBench(domain="retail", dataset_path="tau-bench/retail")
print(bench.name())          # "tau-bench:retail"
print(len(bench.tasks()))    # ~115

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
```

For a sanity check without the upstream install, use `domain="mock"` — the adapter ships a self-contained 5-task mock domain.

## Task shape

```json
{
  "task_id": "retail-0",
  "domain": "retail",
  "instruction": "I want to return order #123 ...",
  "initial_db_state": "...",
  "target_db_state": "...",
  "allowed_tools": ["search_order", "issue_refund", ...]
}
```

## Grading

After the conversation ends, the simulated DB is compared to `target_db_state` field-by-field. Partial credit is reported per field; a task passes only when the comparison is exact.

## Gotchas

- The simulated user is a separate LLM call per turn — total cost is **(agent turns + user turns) × per-turn cost**. Budget accordingly.
- Tool definitions must match the domain's allowed set exactly. The adapter loads them from the upstream package.
- Stochastic outcomes: a 5-task mock run is enough for smoke; for headline numbers, run each task 3× and average.

## See also

- [SWE-bench](./swe-bench.md), [SWE-Lancer](./swe-lancer.md), [NoCha](./nocha.md).
