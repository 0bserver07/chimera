---
title: "AIMO (Progress Prize 3)"
description: "AI Mathematical Olympiad — olympiad-level word problems with integer answers. Tests symbolic reasoning end-to-end."
---

# AIMO (math olympiad problems)

[`AIMO`](https://www.kaggle.com/competitions/ai-mathematical-olympiad-progress-prize-3) is Kaggle's AI Mathematical Olympiad competition. Each problem is an olympiad-style word problem with an **integer** answer in `[0, 999]`. The model has to derive the number — partial credit doesn't exist.

References:

- Kaggle: <https://www.kaggle.com/competitions/ai-mathematical-olympiad-progress-prize-3>
- HuggingFace dataset: <https://huggingface.co/datasets/AI-MO/aimo>

## Status: PARTIAL (adapter wired; benchmark run not yet reported)

| Run | Score |
| --- | --- |
| Chimera | adapter built, see issue #13. |

## How to run

```python
from chimera.eval.benchmarks import AIMOBenchmark
from chimera.eval.harness import Harness

bench = AIMOBenchmark(dataset_path="aimo.jsonl")
print(bench.name())          # "aimo3"
print(len(bench.tasks()))    # depends on the dump

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
```

Or via the CLI:

```bash
chimera eval --benchmark aimo --dataset ./aimo.jsonl --output results.json
```

## Task shape

```json
{
  "id": "aimo-2024-001",
  "problem": "Find the smallest positive integer N such that ...",
  "answer": 720
}
```

## Grading

The agent's final output is scanned for an integer (the **last** integer in the response, modulo 1000 to clamp into the legal range). It's compared against the gold answer. No partial credit.

For verification, pair with [`verify_answer`](../tools/verify.md) — let the model emit a Python check before claiming the integer.

## Tips

- Olympiad problems benefit from `<thinking>` budget. Crank `thinking="high"` on the provider; raw budget is a force multiplier here.
- Few-shot with 2–3 worked examples adds 5–10 points typically.
- Some answers are sensitive to integer overflow. Sanity-check with `verify_answer` before submitting.

## Gotchas

- The competition test set is private; the HuggingFace mirror is the public train split. Use it for capability evals, not for direct leaderboard claims.
- Some problems are language-ambiguous (esp. older Olympiad translations). The adapter does no NL preprocessing.

## See also

- [LiveCodeBench](./livecodebench.md) — contest problems with verified test cases.
- [`verify_answer`](../tools/verify.md), [`think`](../tools/think.md).
