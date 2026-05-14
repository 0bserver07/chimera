---
title: "HumanEval"
description: "OpenAI's original 164 Python function-generation problems. Chimera baseline: 90.9% pass@1 (GLM-5, raw data lost) / 66.5% (GLM-5.1)."
---

# HumanEval (function generation)

[`HumanEval`](https://github.com/openai/human-eval) is OpenAI's original code-generation eval: **164 Python problems**, each with a function signature, docstring, and 7.7 unit tests on average. The agent fills in the function body; pass@1 is computed against the test set.

References:

- GitHub: <https://github.com/openai/human-eval>
- Paper: arXiv:2107.03374
- HuggingFace dataset: <https://huggingface.co/datasets/openai_humaneval>

## Status: DONE (partial run reported)

| Model | Score | Notes |
| --- | --- | --- |
| GLM-5 | 90.9% pass@1 (claimed) | Raw data lost — unverifiable. |
| GLM-5.1 | 66.5% pass@1 (reported) | Raw data in `data/`. |

See [`docs/benchmarks/2026-03-30-humaneval-glm51.md`](../benchmarks/2026-03-30-humaneval-glm51.md) for the report.

## How to run

```bash
# Smallest path — Python script
source .env
python examples/benchmarks/humaneval_full.py --count 164

# Via the CLI
chimera eval --benchmark humaneval --dataset ./humaneval.jsonl --limit 164 --output results.json
```

Or directly in code:

```python
from chimera.eval.benchmarks import HumanEval
from chimera.eval.harness import Harness

bench = HumanEval(dataset_path="humaneval.jsonl")
print(bench.name())          # "human-eval"
print(len(bench.tasks()))    # 164

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
print(results.pass_rate())   # e.g. 0.665
```

## Task shape

```json
{
  "task_id": "HumanEval/0",
  "prompt": "def has_close_elements(numbers, threshold):\n    \"\"\"...\"\"\"\n",
  "canonical_solution": "...",
  "test": "def check(candidate):\n    assert candidate([1.0, 2.0], 0.5) is False\n...",
  "entry_point": "has_close_elements"
}
```

## Grading

In-process: the agent's output is concatenated onto the prompt, the file is executed, and the test's `check(candidate)` is called. A 30-second timeout applies per task.

## Gotchas

- Dataset shape: HumanEval ships as JSONL via the `human_eval` pip package, but the adapter also accepts `{"tasks": [...]}` JSON. Pass either.
- Common failure mode: the model emits the full function instead of just the body. The adapter handles both — the post-processor strips a redundant signature when the prompt is repeated.
- For stricter coverage (extra hidden tests on the same problems), use [HumanEval+](./humaneval-plus.md).
- For multi-language coverage, use [HumanEval-X](./humaneval-x.md).

## See also

- [HumanEval+](./humaneval-plus.md), [HumanEval-X](./humaneval-x.md), [MBPP](./mbpp.md), [LiveCodeBench](./livecodebench.md).
