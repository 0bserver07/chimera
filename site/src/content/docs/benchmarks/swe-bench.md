---
title: "SWE-bench"
description: "Real GitHub issues with PASS_TO_PASS / FAIL_TO_PASS test verification. Chimera's headline agent benchmark."
---

# SWE-bench (real GitHub issues)

[`SWE-bench`](https://www.swebench.com/) (Princeton, 2023) gives an agent a real GitHub issue plus a frozen repository snapshot. The agent edits the codebase; the patch is graded by running the issue's `FAIL_TO_PASS` and `PASS_TO_PASS` test sets.

References:

- Website: <https://www.swebench.com/>
- Paper: arXiv:2310.06770
- GitHub: <https://github.com/princeton-nlp/SWE-bench>

## Variants

| Variant | Instances | Notes |
| --- | --- | --- |
| **Lite** | 300 (filtered for tractability) | The most common comparison surface. |
| **Lite top-20** | 20 (smallest patches, hand-picked) | Smoke set used in Chimera's reports. |
| **Verified** | 500 | OpenAI's human-validated subset; see [`swe-bench-verified`](./swe-bench-verified.md). |
| **Full** | 2,294 | All collected instances. Rarely run end-to-end. |

## Status: GAP (active work to close the score)

| Run | Score | Notes |
| --- | --- | --- |
| Chimera + GLM-5.1, top-20 Lite | 10% (2 / 20) | [Report](../benchmarks/2026-03-30-swebench-lite-glm51.md) |
| GLM-5 + OpenHands (reference) | 77.8% Lite | Published. |

Root-cause analysis in [`docs/benchmarks/README.md`](../benchmarks/) — five gaps identified, in priority order: max iterations (100 → 500), action space (bash → bash+IPython), LLM-based condensation, native `str_replace`, multi-action turns.

## How to run

```bash
# Docker-isolated, proper FAIL_TO_PASS / PASS_TO_PASS
python examples/benchmarks/swe_bench_proper.py --count 10

# Canonical Lite top-20 (matches the 10% number above)
python examples/benchmarks/swe_bench_lite_run.py --count 20 --max-steps 50
```

```python
from chimera.eval.benchmarks import SWEBench
from chimera.eval.harness import Harness

bench = SWEBench(dataset_path="swe-bench-lite.jsonl", limit=20)
print(bench.name())          # "swe-bench"
print(len(bench.tasks()))    # 20

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
print(results.pass_rate())
```

## Task shape

```json
{
  "instance_id": "django__django-12345",
  "repo": "django/django",
  "base_commit": "abc123",
  "problem_statement": "Fix the bug where ...",
  "test_patch": "...",
  "FAIL_TO_PASS": ["tests.test_x::test_y"],
  "PASS_TO_PASS": ["tests.test_x::test_z"]
}
```

## Gotchas

- **Docker required** for proper grading. The `swe_bench_proper.py` runner pulls a per-instance image (`princeton-nlp/sweb.eval.x86_64.<instance_id>`); first run can take 15+ min and >50 GB disk.
- **Cost ceiling.** A 20-instance run with 50 max-steps costs $30–60 on Claude Sonnet, $5–10 on GLM-5.
- Rather than the cherry-picked top-20, use [`swe-bench-verified`](./swe-bench-verified.md) for human-validated numbers comparable to published research.

## See also

- [SWE-bench Verified](./swe-bench-verified.md), [Multi-SWE-bench](./multi-swe-bench.md), [SWE-Lancer](./swe-lancer.md).
