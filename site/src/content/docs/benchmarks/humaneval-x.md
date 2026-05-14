---
title: "HumanEval-X"
---

# HumanEval-X (multi-language HumanEval)

`HumanEval-X` translates the original 164 HumanEval problems into 5 languages
(Python, Java, C++, Go, JavaScript) — same problems, language-specific
function signatures and tests. Different surface from
`humaneval_plus.py`, which extends Python coverage with more tests for the
same Python problems.

References:

- HuggingFace: <https://huggingface.co/datasets/THUDM/humaneval-x>
- GitHub: <https://github.com/THUDM/CodeGeeX>
- Paper: arXiv:2303.17568

## Status: SCAFFOLD

| Surface | State |
| --- | --- |
| `HumanEvalXTask` dataclass | DONE |
| Loader (JSON / JSONL / `{"tasks": [...]}` / language filter / limit) | DONE |
| Python in-process grading | DONE (mirrors `HumanEval`) |
| Java / C++ / Go / JavaScript grading | STUBBED — returns `False` (not `NotImplementedError`) |
| Discoverable via `chimera eval --benchmark humaneval-x` | DONE |

Live grading for compiled languages should reuse the runners under
`chimera/eval/benchmarks/runners/` (see `MultiSWEBench`). Tracked as a
follow-up.

## Quick start

```python
from chimera.eval.benchmarks import HumanEvalX

# Filter to Python only
bench = HumanEvalX(dataset_path="humaneval-x.jsonl", language="python")
print(bench.name())                # "humaneval-x-python"
print(HumanEvalX.supported_languages())
# ['cpp', 'go', 'java', 'javascript', 'python']
```

## Test harness format

Python tasks use HumanEval's self-driving harness — the test must call its
own `check(candidate)`:

```python
task = {
    "language": "python",
    "prompt": "def add(a, b):\n",
    "test": (
        "def check(candidate):\n"
        "    assert candidate(1, 2) == 3\n"
        "check(add)\n"
    ),
}
```
