---
title: "MultiSWE-bench"
---

# MultiSWE-bench (multi-language)

`MultiSWE-bench` extends SWE-bench beyond Python. Each instance carries a
`language` field and is graded with the language's native test runner —
`pytest` for Python, `mvn test` for Java, `go test` for Go,
`npm test` for JavaScript / TypeScript, `cargo test` for Rust.

References:

- GitHub: <https://github.com/multi-swe-bench/multi-swe-bench>
- Paper: arXiv:2504.02605

## Module map

| File | Purpose |
| --- | --- |
| `chimera/eval/benchmarks/multi_swe_bench.py` | `MultiSWEBench`, `MultiSWEBenchInstance` |
| `chimera/eval/benchmarks/runners/__init__.py` | `RUNNERS` registry, `get_runner()` |
| `chimera/eval/benchmarks/runners/base.py` | `LanguageRunner`, `RunnerResult`, `SkipReason` |
| `chimera/eval/benchmarks/runners/{python,java,go,javascript,rust}_runner.py` | One frozen runner per language |
| `tests/eval/test_multi_swe_bench.py` | 41 tests (loading, dispatch, skip patterns) |

## Quick start

```python
from chimera.eval.benchmarks import MultiSWEBench

# Load a JSON or JSON-lines dataset
bench = MultiSWEBench(dataset_path="multi-swe-bench.jsonl")

# Filter to a single language
go_bench = MultiSWEBench(dataset_path="multi-swe-bench.jsonl", language="go")

print(go_bench.name())             # "multi-swe-bench-go"
print(go_bench.language_breakdown())  # {"go": N}
```

## Supported languages

| Language | Test command | Toolchain probe |
| --- | --- | --- |
| Python | `pytest -x --no-header -rN` | `python --version` |
| Java | `mvn -q -B test` | `mvn --version` |
| Go | `go test ./...` | `go version` |
| JavaScript / TypeScript | `npm test --silent` | `node --version` |
| Rust | `cargo test --quiet` | `cargo --version` |

Aliases: `js` → `javascript`, `ts` → `typescript`, `golang` → `go`. The
canonical names are returned by `MultiSWEBench.supported_languages()`.

## Skip pattern

When the language toolchain isn't installed in the execution environment,
the runner short-circuits and records the skip reason instead of raising:

```python
bench.evaluate(task, agent_output="...", env=env)
# False
bench.last_skip_reasons
# [("py__demo__1", "python", "toolchain_missing")]
```

Skip reasons (`chimera.eval.benchmarks.runners.base.SkipReason`):

- `no_env` — `env` was `None`.
- `toolchain_missing` — the toolchain probe failed.
- `patch_failed` — `git apply` of the test patch failed.
- `execution_error` — the test command itself raised or returned a
  non-numeric status.

Use `MultiSWEBench.evaluate_detailed(task, env)` if you want the full
`RunnerResult` (with `stdout`, `stderr`, `exit_code`).

## Adding a new language

1. Create `chimera/eval/benchmarks/runners/<lang>_runner.py`:

   ```python
   from chimera.eval.benchmarks.runners.base import LanguageRunner

   KOTLIN_RUNNER = LanguageRunner(
       language="kotlin",
       test_command="gradle test --quiet",
       toolchain_command="gradle --version",
       display_name="Kotlin (Gradle)",
   )
   ```

2. Register it in `runners/__init__.py` (`RUNNERS["kotlin"] = KOTLIN_RUNNER`).
3. Add the language string to `SUPPORTED_LANGUAGES` in
   `multi_swe_bench.py`.
4. Add a fixture line + dispatch test in `test_multi_swe_bench.py`.

## Status

| Item | State |
| --- | --- |
| Loader (JSON / JSON-lines / `{"tasks": [...]}` / `{"instances": [...]}`) | DONE |
| Per-language dispatch | DONE — 5 runners |
| Skip pattern when toolchain missing | DONE |
| Detailed `RunnerResult` access | DONE |
| Live run vs upstream dataset | TODO — requires Docker images per language |
| Score reporting integration with `chimera bench` CLI | TODO |
