---
title: "BigCodeBench"
description: "1,140 practical Python tasks with real library calls — 723 distinct libraries in scope. Tests whether a model can actually use the standard library, not just write toy functions."
---

# BigCodeBench (practical library-calling tasks)

[`BigCodeBench`](https://huggingface.co/datasets/bigcode/bigcodebench) is HuggingFace's 1,140-task eval that exercises real Python library usage. Each task requires calling functions from one or more of 723 distinct libraries — `requests`, `pandas`, `numpy`, `pathlib`, `subprocess`, etc. Solutions are graded by executing them against test cases that touch the libraries directly, so a stub that ignores the requested API fails.

References:

- HuggingFace: <https://huggingface.co/datasets/bigcode/bigcodebench>
- Paper: arXiv:2406.15877
- GitHub: <https://github.com/bigcode-project/bigcodebench>

## Status: TODO (adapter wired; not benchmarked yet)

| Run | Score |
| --- | --- |
| Chimera | NOT RUN |

## Splits

| Split | What it tests |
| --- | --- |
| `instruct` (default) | Natural-language prompt — "Write a function that …" |
| `complete` | Fill-in-the-blank: a partial function with a `# TODO` block. |

## How to run

```bash
# Stage the dataset locally (one-time)
huggingface-cli download bigcode/bigcodebench --repo-type dataset \
  --local-dir ~/.chimera/datasets/bigcodebench
```

```python
from chimera.eval.benchmarks import BigCodeBench
from chimera.eval.harness import Harness

bench = BigCodeBench(split="instruct")  # or "complete"
print(bench.name())            # "bigcodebench-instruct"
print(len(bench.tasks()))      # 1140

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
```

Override the dataset path via the `CHIMERA_BIGCODEBENCH_PATH` env var or the `dataset_path=` kwarg.

## Task shape

```json
{
  "task_id": "BigCodeBench/0",
  "complete_prompt": "...",
  "instruct_prompt": "Write a function that ...",
  "canonical_solution": "...",
  "test": "import unittest\nclass TestCases(unittest.TestCase): ...",
  "entry_point": "task_func",
  "libs": ["numpy", "pandas"]
}
```

## Grading

In-process: the agent's output is exec'd, then the `unittest.TestCases` defined in the task's `test` field is invoked. A task passes only when every test case in the suite passes.

## Gotchas

- **The dataset is not auto-downloaded.** `tasks()` returns `[]` if nothing is staged. Pre-flight with `BigCodeBench.dataset_available()`.
- Tests can touch the network and filesystem — run inside Docker or a sandboxed environment if you don't trust the model's output.
- Some tasks pin specific library versions in their imports. Build a matching venv before grading.

## See also

- [HumanEval](./humaneval.md), [MBPP](./mbpp.md), [LiveCodeBench](./livecodebench.md).
