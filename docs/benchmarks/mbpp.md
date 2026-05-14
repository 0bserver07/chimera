---
title: "MBPP"
description: "Mostly Basic Python Problems — 974 short tasks with assert-based test cases. The standard 'small-model coding' eval."
---

# MBPP (Mostly Basic Python Problems)

[`MBPP`](https://github.com/google-research/google-research/tree/master/mbpp) is Google's 974-problem Python eval. Each task is a one-line natural-language description plus three `assert` statements. A solution passes when all three asserts hold.

References:

- GitHub: <https://github.com/google-research/google-research/tree/master/mbpp>
- Paper: arXiv:2108.07732
- HuggingFace dataset: <https://huggingface.co/datasets/mbpp>

## Status: TODO (adapter wired; not benchmarked yet)

| Run | Score |
| --- | --- |
| Chimera | NOT RUN |

The adapter is feature-complete; the gap is a baseline run. Tracked as issue #11.

## How to run

```python
from chimera.eval.benchmarks import MBPP
from chimera.eval.harness import Harness

bench = MBPP(dataset_path="mbpp.jsonl", split="test")
print(bench.name())            # "mbpp-test"
print(len(bench.tasks()))      # 974 (or per split)

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
print(results.pass_rate())
```

Splits: `train` (374), `validation` (90), `test` (500), `prompt` (10). The 974 total is the union. The `name()` is suffixed with the split (`mbpp-test`, `mbpp-validation`, ...).

## Task shape

```json
{
  "task_id": 11,
  "text": "Write a function to remove first and last occurrence of a given character from the string.",
  "code": "def remove_Occ(s, ch): ...",
  "test_list": [
    "assert remove_Occ(\"hello\", \"l\") == \"heo\"",
    "assert remove_Occ(\"abcda\", \"a\") == \"bcd\"",
    "assert remove_Occ(\"PHP\", \"P\") == \"H\""
  ]
}
```

## Grading

In-process: the agent's output is `exec()`'d, then each `assert` from `test_list` is run. A task passes only if **all** asserts pass (no partial credit).

## Gotchas

- MBPP's prompt style is terse — small models often need a few-shot prefix to grasp the convention. The adapter does not prepend examples by default.
- Watch for prompt leakage: the dataset's `code` field is the reference solution. Filter it out of your prompt format.
- Some asserts depend on Python version quirks (dict ordering, float formatting). The adapter does not retry on near-misses.

## See also

- [HumanEval](./humaneval.md), [BigCodeBench](./bigcodebench.md), [LiveCodeBench](./livecodebench.md).
