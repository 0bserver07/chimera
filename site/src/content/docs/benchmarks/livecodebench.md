---
title: "LiveCodeBench"
description: "Contamination-free competitive programming benchmark. Tasks are dated; filter by release date to guarantee zero leakage against a model's training cutoff."
---

# LiveCodeBench (contamination-free competitive programming)

[`LiveCodeBench`](https://livecodebench.github.io/) sources problems from LeetCode, AtCoder, and Codeforces and timestamps each. Pick a `start_date` after your model's training cutoff and you have a zero-contamination eval — the model can't have seen any of these problems during pretraining.

References:

- Website: <https://livecodebench.github.io/>
- Paper: arXiv:2403.07974
- GitHub: <https://github.com/LiveCodeBench/LiveCodeBench>

## Status: TODO (adapter wired; not benchmarked yet)

| Run | Score |
| --- | --- |
| Chimera | NOT RUN |

## Scenarios

| Scenario | What it tests |
| --- | --- |
| `codegeneration` (default) | Given a problem statement, generate a solution. |
| `selfrepair` | Given a buggy solution + failing tests, fix it. |
| `codeexecution` | Predict the output of code without running it. |
| `testoutput` | Generate test inputs that exercise a target branch. |

## How to run

```python
from chimera.eval.benchmarks import LiveCodeBench
from chimera.eval.harness import Harness

# Code generation, post-GLM-5 training cutoff
bench = LiveCodeBench(
    dataset_path="livecodebench.jsonl",
    scenario="codegeneration",
    start_date="2025-01-01",   # after model's training cutoff
)
print(bench.name())     # e.g. "livecodebench-codegeneration-2025-01-01_..."
```

```python
# Self-repair window
bench = LiveCodeBench(
    dataset_path="livecodebench.jsonl",
    scenario="selfrepair",
    start_date="2025-01-01",
    end_date="2025-06-30",
)
```

## Task shape

```json
{
  "problem_id": "twosum-v2-leetcode",
  "title": "Two Sum II",
  "difficulty": "easy",
  "platform": "leetcode",
  "release_date": "2025-03-15",
  "problem_statement": "...",
  "starter_code": "def two_sum(nums, target): ...",
  "public_tests": [{"input": "...", "output": "..."}],
  "private_tests": [...]
}
```

## Grading

Public + private test cases are run with a 30-second per-task timeout. Both sets must pass. The adapter reports per-difficulty breakdowns (`easy`, `medium`, `hard`).

## Gotchas

- **Pick `start_date` carefully.** A start date before your model's cutoff invalidates the contamination guarantee.
- The dataset is updated monthly. Re-pull periodically to keep the window fresh.
- Private tests are not redistributable in raw form — load them via the upstream JSONL releases on the dataset's GitHub releases page.

## See also

- [HumanEval](./humaneval.md), [MBPP](./mbpp.md), [BigCodeBench](./bigcodebench.md), [AIMO](./aimo.md).
