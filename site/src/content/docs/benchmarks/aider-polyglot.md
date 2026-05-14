---
title: "Aider Polyglot"
description: "Aider's 225-task polyglot edit benchmark across 6 languages: Python, JavaScript, Rust, Go, C++, Java. Tests code-edit fluency rather than from-scratch generation."
---

# Aider Polyglot (multi-language code edits)

[`Aider Polyglot`](https://aider.chat/docs/leaderboards/) is the standard "code edit" benchmark — instead of generating code from scratch, the agent receives a buggy or incomplete file and must edit it. 225 exercises across Python, JavaScript, Rust, Go, C++, and Java.

References:

- Aider leaderboard: <https://aider.chat/docs/leaderboards/>
- GitHub (exercises): <https://github.com/Aider-AI/polyglot-benchmark>

## Status: TODO (adapter wired; not benchmarked yet)

| Run | Score |
| --- | --- |
| Chimera | NOT RUN |

The adapter supports two grading modes that coexist on a per-task basis:

| Mode | Grader |
| --- | --- |
| `diff-match` | Compare the agent's patch against the gold patch line-for-line. Strict. |
| `test-pass` | Run the language's native test runner against the patched tree. Loose. |

Diff-match takes precedence; falls through to test-pass when the task carries tests but no canonical patch.

## How to run

```bash
git clone https://github.com/Aider-AI/polyglot-benchmark ~/.chimera/datasets/aider-polyglot
```

```python
from chimera.eval.benchmarks import AiderPolyglot
from chimera.eval.harness import Harness

# All languages
bench = AiderPolyglot(dataset_path="~/.chimera/datasets/aider-polyglot")
print(bench.name())     # "aider-polyglot"

# Filter
bench = AiderPolyglot(
    dataset_path="~/.chimera/datasets/aider-polyglot",
    languages=["python", "rust"],
)
print(bench.name())     # "aider-polyglot:python+rust"

harness = Harness(agent=my_agent, benchmark=bench)
results = harness.run()
```

## Task shape

```json
{
  "exercise": "reverse-string",
  "language": "rust",
  "instructions": "Reverse a string. ...",
  "files": {"src/lib.rs": "pub fn reverse(s: &str) -> String { todo!() }"},
  "tests_path": "tests/reverse_string.rs"
}
```

## Grading

For tests-mode: the agent's patch is applied, then `cargo test` / `pytest` / `go test` / `npm test` is run inside the workspace. Tools live under `chimera/eval/benchmarks/runners/`.

## Gotchas

- The dataset is **not** pip-installable — clone the GitHub repo directly.
- Each language brings its own toolchain (`rustc`, `cargo`, `go`, etc.). Run inside a fat Docker image or skip the languages you don't have set up.
- Aider's own leaderboard uses test-pass mode with their custom diff format. To match their numbers, use `mode="test-pass"` and Aider's edit format.

## See also

- [SWE-bench](./swe-bench.md), [Multi-SWE-bench](./multi-swe-bench.md), [HumanEval-X](./humaneval-x.md).
