---
title: "SWE-bench Verified"
description: "OpenAI's 500-instance human-validated SWE-bench subset. The cleanest comparison surface in the SWE-bench family."
---

# SWE-bench Verified (human-validated subset)

[`SWE-bench Verified`](https://openai.com/index/introducing-swe-bench-verified/) is OpenAI's 500-instance subset of SWE-bench where every instance was hand-validated by professional engineers: the problem statement is unambiguous, the test patch correctly covers the bug, and the gold patch actually fixes it. It's the cleanest apples-to-apples surface in the SWE-bench family.

References:

- Announcement: <https://openai.com/index/introducing-swe-bench-verified/>
- Dataset: <https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified>

## Status: TODO (full 500 not yet run)

| Run | Score |
| --- | --- |
| Chimera, full 500 | NOT RUN |

The adapter is identical to [`SWE-bench`](./swe-bench.md) — the `SWEBenchVerified` class is a thin specialisation with a different default `name()`. Once the Lite gap closes (see [`docs/benchmarks/README.md`](../benchmarks/)), the Verified run becomes the canonical headline number.

## How to run

```bash
# Pull the dataset
huggingface-cli download princeton-nlp/SWE-bench_Verified \
  --repo-type dataset --local-dir ./swe-bench-verified

# Run via Python
python -c "
from chimera.eval.benchmarks.swe_bench_verified import SWEBenchVerified
from chimera.eval.harness import Harness
bench = SWEBenchVerified(dataset_path='./swe-bench-verified/data.jsonl')
print(bench.name(), len(bench.tasks()))
"
```

```python
from chimera.eval.benchmarks.swe_bench_verified import SWEBenchVerified

bench = SWEBenchVerified(dataset_path="swe-bench-verified.jsonl")
print(bench.name())          # "swe-bench-verified"
print(len(bench.tasks()))    # 500
```

## Task shape

Same as SWE-bench plus an `is_verified: true` flag.

## Grading

Same `FAIL_TO_PASS` / `PASS_TO_PASS` contract as SWE-bench. Run inside per-instance Docker images for byte-identical reproducibility.

## Gotchas

- A full 500-instance run is expensive — budget 100+ hours of agent runtime and $100–500 in API cost depending on the model.
- The verified subset is **biased toward tractable instances**. A high Verified score does not extrapolate to the full 2,294-instance SWE-bench.
- Use [SWE-bench top-20](./swe-bench.md) for a fast smoke run before committing to the full sweep.

## See also

- [SWE-bench](./swe-bench.md), [Multi-SWE-bench](./multi-swe-bench.md).
