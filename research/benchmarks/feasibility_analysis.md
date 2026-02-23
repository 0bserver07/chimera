# Feasibility Analysis: Chimera for ARC-AGI, SWE-bench, and Terminal Bench

**Date:** 2026-02-20
**Status:** Draft
**Context:** Analyzing if the Chimera framework can be extended to tackle major AI benchmarks.

## Executive Summary

Chimera is uniquely positioned to solve these benchmarks because of its "Synthesis" philosophy. Unlike standard agents that just "chat," Chimera's `Trainer` and `Strategy` layers allow it to iterate, backtrack, and search for solutions—critical for ARC's reasoning and SWE-bench's complexity.

However, the `chimera.eval` layer (the interface to these benchmarks) is currently missing from the implementation and needs to be built.

---

## 1. ARC-AGI (Abstraction and Reasoning Corpus)

**Constraint:** Visual reasoning, few-shot learning, output must be pixel-perfect.
**Chimera Fit:** **High (as a Program Synthesis Engine)**

ARC is often solved via "Program Synthesis" (finding a program that transforms input to output). Chimera's core loop (`Synthesize Code -> Run Tests -> Refine`) is perfectly aligned with this.

*   **Mapping:**
    *   **Spec:** The `train` input/output pairs.
    *   **Tests:** Running the generated python program against the `train` pairs.
    *   **Environment:** A lightweight Python sandbox (already exists).
    *   **Strategy:** Needs a new `Search` strategy (e.g., BFS/DFS over a DSL) rather than just LLM refinement.

*   **Gap:**
    *   Need to implement `ARCBenchmark` loader.
    *   Need to implement a "Visual" or "Grid" DSL (optional, but standard for ARC).
    *   Need a `Search` loop optimized for short programs, not full codebases.

## 2. SWE-bench (Software Engineering Benchmark)

**Constraint:** Solve GitHub issues, run tests, navigate large codebases.
**Chimera Fit:** **Perfect (Primary Use Case)**

Chimera was essentially designed for this. Its default `TestConvergence` strategy is a mirror of the SWE-bench evaluation protocol (fix -> test -> fix).

*   **Mapping:**
    *   **Spec:** The GitHub Issue description.
    *   **Environment:** `Environment.docker` (already planned/exists) to mount the repo.
    *   **Constraint:** `Constraint.tests_pass`.
    *   **Strategy:** `TestConvergence` (default).

*   **Gap:**
    *   The `swebench` harness code (loading datasets, handling the `golden` patch evaluation) is missing.
    *   Docker integration needs to be robust enough to handle the specific environment setup of SWE-bench (which uses custom images).

## 3. Terminal Bench (e.g., Claude Institute / Laude)

**Constraint:** Complex shell interactions, state management, tools.
**Chimera Fit:** **High**

Chimera's `Tool` and `Loop` primitives are sufficient.

*   **Mapping:**
    *   **Tools:** `chimera.tools.bash` (already exists).
    *   **Environment:** `Environment.docker` or `Environment.local`.

*   **Gap:**
    *   Need a harness to pipe the benchmark's specific prompts/objectives into the Agent.

---

## Recommendation

We should proceed with implementing the `chimera.eval` layer, prioritizing **SWE-bench** as the "Hello World" for Chimera, as it aligns 1:1 with the framework's "Synthesis" goal. ARC-AGI should be a secondary target demonstrating the flexibility of the `Strategy` layer (swapping "Refinement" for "Search").

### Proposed Roadmap

1.  **Scaffold `chimera/eval/`**: Create the directory structure.
2.  **Implement `Harness`**: The base class for running a benchmark.
3.  **Implement `SWEBenchLite`**: A connector to the `princeton-nlp/SWE-bench_Lite` dataset.
4.  **Verify**: Run a simple Chimera agent against 1 easy SWE-bench task.
