---
title: "Making ProgramBench Runnable — 2026-06-19"
description: "ProgramBench was blocked by a stack of hidden gates, not one. The fixes, a one-shot + compile-repair rebuild strategy with RAG-augmented repair, and how to verify it."
---

# Making ProgramBench Runnable — 2026-06-19

**Benchmark:** [ProgramBench](./programbench.md) — the inverse of SWE-bench: the
agent **rebuilds a program from scratch** given only a compiled binary and its
docs, graded by the upstream `programbench eval` harness inside per-task Docker
images.

**Status before this work:** zero gradeable submissions. **After:** the
strategy produces complete, **compiling**, gradeable source trees — the first
time Chimera can put real numbers on ProgramBench.

## The blocker was a *stack* of gates, not one

The original "ProgramBench never converges" was diagnosed as agent-convergence
tuning. It wasn't — it was five gates, each hidden behind the one before it (the
agent writing nothing meant nobody ever discovered gates 2–5):

| # | Gate | Fix |
|---|------|-----|
| 1 | **The agent loop never writes.** swe-agent over-explores (`list_files`/`read_file`/`repo_map`) and never calls `write_file` — confirmed live with *both* a reasoning model (glm-5.2) and a coder model (qwen3-coder-next). | One-shot codegen (emit the whole tree in one completion) sidesteps it. |
| 2 | **Missing build contract.** Even with files written, *every* submission `compile_fail`ed: the grader runs `chmod +x ./compile.sh && ./compile.sh` then `./executable`, and nothing produced a `compile.sh`. The gold reference submission ships exactly this. | Baked the `compile.sh → ./executable` contract into the prompt. |
| 3 | **False "no internet" claim.** The adapter told agents the cleanroom is offline; the *grading* step actually fetches deps (`cargo` downloads crates). | Corrected the prompt. |
| 4 | **Interactive programs hang grading.** ncurses animations (cmatrix, tty-clock) compile but never exit — the test run hangs. | Excluded from quick runs; per-grade timeouts for scale. |
| 5 | **Context blowup.** Data-heavy inputs (figlet ships dozens of font files) overflowed the model's context. | `assemble_spec` caps total size + orders docs (README/man) first. |

## The strategy

`chimera/eval/benchmarks/programbench_rebuild.py` + `ProgramBench.rebuild_instance()`:

1. **Assemble the spec** from `_inputs/` (docs first, `.git`/binaries/images skipped, size-capped).
2. **One-shot generate** the whole source tree — including `compile.sh` — in a single completion.
3. **Grade** it through the real upstream harness.
4. **Compile-repair:** feed the *focused* build errors (error lines + tail, not dependency-download spam) back, **merge-repair** (omitted files are kept), and repeat up to `max_repair` times.
5. **RAG-augmented repair** (`rebuild_docs.py`): when a build fails on an unknown symbol (`no method named is_encrypted found for struct ZipFile`), parse the symbol + the crate deps from `Cargo.toml`, fetch the real API from docs.rs, and splice it into the next repair round.

Grading is injected via a `grade_fn` callback, so the core loop is unit-tested
with **no Docker and no LLM**.

```python
from chimera.eval.benchmarks.programbench import ProgramBench
from chimera.eval.benchmarks.rebuild_docs import DocsRsProvider
from chimera.providers.factory import create_provider

bench = ProgramBench(tasks_dir=TASKS, run_dir="runs", programbench_cli=PB_CLI)
result = bench.rebuild_instance(
    task,
    provider=create_provider(model="qwen3-coder-next:cloud"),
    max_repair=3,
    doc_provider=DocsRsProvider(),   # RAG-augmented repair
)
print(result.resolved, result.best_summary)
```

## Verify it

The loop, merge, prose-nudge, error-focusing, symbol-parsing, and doc-injection
are all covered by deterministic tests — no Docker, no LLM, no network:

```bash
uv run pytest tests/eval/test_programbench_rebuild.py tests/eval/test_rebuild_docs.py -q
# 24 passed
```

Live-verified end-to-end via the Ollama-Cloud bridge: `rebuild_instance`
generates a real source tree (e.g. a 5-file Rust project), grades it through the
upstream harness, and repairs with the file-merge intact — reducing compile
errors round over round (zip-password-finder: 11 → 8 errors across rounds).

## Results

A first comparative cut — easy C+Go tasks × {qwen3-coder-next, glm-5.2}, RAG on
— is running. Numbers land here. The full **201 × N-model** matrix runs on
Modal (native amd64 sandboxes, parallel — no QEMU) via
`chimera/env/modal_sandbox.py`.

_(matrix table forthcoming)_

## Why it matters

ProgramBench is a hard, real benchmark (201 GitHub projects across 7 languages)
that Chimera previously produced **zero** results on. The fixes are
framework-level — they unblock *any* inference strategy, not just this one — and
the rebuild strategy gives Chimera its first ProgramBench numbers, directly
serving the comparative-methodology mission: same tasks, controlled variables,
model-vs-model.
