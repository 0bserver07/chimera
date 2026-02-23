# Implementation Plan: Benchmarking Chimera

This plan outlines the steps to implement the `chimera.eval` layer and tackle the ARC-AGI and SWE-bench benchmarks.

## Phase 1: Foundation (The `eval` Layer)

We need a standardized way to run agents against datasets.

- [ ] **`chimera/eval/__init__.py`**: Expose `Harness`, `Benchmark`, `Metric`.
- [ ] **`chimera/eval/harness.py`**: The runner.
    - `run(agent, benchmark, limit=None)`
    - Handling timeouts, errors, and collecting results.
- [ ] **`chimera/eval/metrics.py`**:
    - `pass_rate`: Percentage of tasks solved.
    - `cost`: Token usage/cost.
    - `steps`: Number of turns.

## Phase 2: ARC-AGI (The "Synthesis" Proof)

ARC is a great test of the "Spec -> Synthesis" loop.

- [ ] **`chimera/eval/benchmarks/arc.py`**:
    - Load ARC-AGI JSON data (train/test pairs).
    - `ARCBenchmark` class.
- [ ] **`chimera/eval/env/arc_env.py`**:
    - A lightweight Python sandbox for grid manipulation.
    - `check_solution(grid, expected)` logic.
- [ ] **`chimera/training/strategies/search.py`**:
    - Implement a `TreeSearch` or `BestFirstSearch` strategy (essential for ARC).
    - Unlike `TestConvergence` (which edits code), this explores a tree of possible programs.

## Phase 3: SWE-bench (The "Engineering" Proof)

SWE-bench is the primary target for the "composable agent framework" vision.

- [ ] **`chimera/eval/benchmarks/swe.py`**:
    - Load `princeton-nlp/SWE-bench_Lite` from HuggingFace.
    - `SWEBenchmark` class.
- [ ] **`chimera/env/docker.py`**:
    - Ensure robust Docker execution (mounting repos, running tests).
    - **Critical:** Needs to support the specific environment setup of each repo.

## Phase 4: Execution

1.  Run `chimera bench arc --limit 5` (Solve 5 easy ARC tasks).
2.  Run `chimera bench swe-lite --instance django-123` (Solve 1 real bug).
