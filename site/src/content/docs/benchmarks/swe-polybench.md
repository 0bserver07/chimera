---
title: "SWE-PolyBench"
description: "Polyglot repository-level coding benchmark — Python / Java / JavaScript / TypeScript. Bug fixes, feature additions, refactoring graded by execution."
---

# SWE-PolyBench (multi-language)

[SWE-PolyBench](https://github.com/amazon-science/SWE-PolyBench) (Amazon Science, 2025) is a repository-level coding benchmark that extends the SWE-bench format across four languages: Python, Java, JavaScript, and TypeScript. Each task is a real GitHub bug fix, feature addition, or refactor; agents are graded by running the upstream test suite against their patch.

References:

- HuggingFace dataset: <https://huggingface.co/datasets/AmazonScience/SWE-PolyBench>
- GitHub: <https://github.com/amazon-science/SWE-PolyBench>
- Paper: [arXiv:2504.08703](https://arxiv.org/abs/2504.08703)

## Splits

| Split | Instances | Notes |
| --- | --- | --- |
| `pb500` | 500 | The standard headline split — balanced across languages and task types. |
| `verified` | smaller | Human-validated subset. |
| `full` | all | Full collected pool. Rarely run end-to-end. |

Languages: `python` · `java` · `javascript` · `typescript`. Pass `language=` to filter to one.

## How to run

```bash
# 1. Pull the dataset to disk. The upstream lives on HuggingFace; one easy
#    path is the datasets library:
python -c "
from datasets import load_dataset
import json
ds = load_dataset('AmazonScience/SWE-PolyBench', split='train')
with open('swe-polybench.jsonl', 'w') as f:
    for row in ds:
        f.write(json.dumps(dict(row)) + '\n')
"

# 2. Run via the bench harness
chimera bench swe-polybench --dataset swe-polybench.jsonl --limit 10
```

Programmatic use:

```python
from chimera.eval.benchmarks.swe_polybench import SWEPolyBench
from chimera.eval.harness import Harness

bench = SWEPolyBench(
    dataset_path="swe-polybench.jsonl",
    split="pb500",
    language="python",   # or None for all four
    limit=10,
)

print(bench.name())          # "swe-polybench"
print(len(bench.tasks()))    # 10
```

## Constructor arguments

| Argument | Default | Notes |
| --- | --- | --- |
| `dataset_path` | `None` | Path to JSON or JSONL file with instances. `None` keeps the benchmark empty (useful for smoke tests / programmatic seed). |
| `split` | `"pb500"` | One of `full`, `pb500`, `verified`. Used as a filter when records carry a `"split"` field. |
| `language` | `None` | Optional filter; one of `python`, `java`, `javascript`, `typescript`. |
| `limit` | `None` | Max tasks to keep after filtering. |

Invalid `split` or `language` raises `ValueError`. Missing `dataset_path` raises `FileNotFoundError`.

## Instance shape

Each task is a `SWEPolyBenchInstance` dataclass with:

- `instance_id`, `repo`, `base_commit`
- `problem_statement` — natural-language issue text
- `language` — one of the four
- `task_type` — `bug_fix`, `feature`, `refactor`
- `test_patch` — the upstream gold test diff
- `patch` — the gold solution patch
- `modified_files` — files the gold patch touches
- `cst_nodes` — CST-level annotations used by some upstream analyses
- `hints_text` — optional hints from the original GitHub thread

## Grading

`evaluate(task, agent_output)` runs the language-appropriate test command against the agent's patched workspace:

| Language | Test runner |
| --- | --- |
| Python | `pytest -x` |
| JavaScript | `npm test --silent` |
| TypeScript | `npm test --silent` |
| Java | `mvn -q test` |

A task passes if the test runner exits 0.

## Status

Adapter is implemented and registered. Live tier-1 runs against real models are tracked under the [benchmark transparency](../benchmarks/) framework — contribute a run to `data/swe-polybench-<model>-results.jsonl` to seed the comparison table.

## Why this benchmark matters

Most agent benchmarks are Python-only. SWE-PolyBench is the cleanest existing test of cross-language generalization for a coding agent — the same model has to navigate `setup.py` *and* `pom.xml` *and* `package.json` without losing the plot. Coupled with `chimera/ferret`'s sandbox-first execution and the language-aware runner table, it's the right benchmark to ask "does my agent know what kind of project it's in?"

## Related

- [SWE-bench](./swe-bench.md) — Python-only ancestor
- [MultiSWE-bench](./multi-swe-bench.md) — sibling, similar multi-language posture
- [Benchmarks overview](../benchmarks/)
