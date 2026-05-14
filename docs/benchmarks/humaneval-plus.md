---
title: "HumanEval+"
description: "EvalPlus extension of HumanEval — same 164 problems, 80× more tests on average. Detects shallow models that pass the base suite by luck."
---

# HumanEval+ (extended tests on HumanEval)

[`HumanEval+`](https://github.com/evalplus/evalplus) keeps HumanEval's 164 Python problems but adds 80× more test cases per problem. Many models that score ≥80% on base HumanEval drop 10–20 points on `plus`. It's the standard "is your model actually correct?" follow-up.

References:

- GitHub: <https://github.com/evalplus/evalplus>
- Paper: arXiv:2305.01210
- PyPI: `evalplus`

## Status: SCAFFOLD (adapter wired; runner gated on `evalplus`)

| Surface | State |
| --- | --- |
| `HumanEvalPlus` adapter | DONE |
| Falls back to local JSON / JSONL dataset | DONE |
| Uses `evalplus.evaluate` when the pip package is installed | DONE |
| Discoverable via `chimera eval --benchmark humaneval+` | TODO (#10) — load directly in Python today |

## How to run

```bash
pip install chimera-run[anthropic] evalplus
```

```python
from chimera.eval.benchmarks import HumanEvalPlus
from chimera.eval.harness import Harness

bench = HumanEvalPlus(version="plus")  # or "base"
print(bench.name())          # "human-eval-plus"
print(len(bench.tasks()))    # 164

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
print(results.pass_rate())
```

Without `evalplus` installed, point `dataset_path` at a local JSON / JSONL dump.

## Grading contract

`evaluate()` defers to `evalplus.evaluate` which runs the **base + plus** test set per problem under a 30-second timeout per case. A task passes only if every test passes.

## Gotchas

- `evalplus` is heavy (numpy + sandboxing deps). Run it in a fresh venv to avoid version conflicts with the rest of Chimera's deps.
- The `plus` suite includes adversarial inputs (extreme floats, empty containers); models that rely on common-case prompts often regress.
- Use [HumanEval](./humaneval.md) for the baseline number that's directly comparable to OpenAI's published score.

## See also

- [HumanEval](./humaneval.md), [HumanEval-X](./humaneval-x.md), [BigCodeBench](./bigcodebench.md).
