# Retrospective Planning: Scaling Chimera for Benchmarks

**Date:** 2026-02-20
**Context:** Aligning Chimera's architecture to solve ARC-AGI, SWE-bench, and Terminal Bench.
**Goal:** Ensure Chimera is not just a wrapper, but architecturally native to these problems.

---

## 1. The Core Insight

Chimera's current architecture is **Linear Refinement** (Hill Climbing).
- **Agent:** ReAct (Linear: Think -> Act -> Observe).
- **Strategy:** `TestConvergence` (Linear: Edit -> Test -> Rollback).
- **Environment:** `LocalEnvironment` (Stateless: New shell per command).

**This is insufficient.**
- **ARC-AGI** requires **Tree Search** (Exploring multiple discrete hypotheses).
- **SWE-bench** requires **Context Management** (Navigating massive state spaces).
- **Terminal Bench** requires **Session Persistence** (Shell state must survive between steps).

We need to upgrade the "Synthesis" and "Environment" layers to support **Branching** and **Persistence**.

---

## 2. Benchmark-Specific Analysis

### A. ARC-AGI (Abstraction & Reasoning)

**The Hard Part:**
ARC is not about "fixing bugs" (local optimization); it's about finding a *program* that generates the output. A linear agent will get stuck in a local optimum (e.g., a rule that works for 2/3 examples).
- **Failure Mode:** "I tried a loop, it failed. I tried a map, it failed. I give up."
- **Required Capability:** **Hypothesis Search**. The agent needs to generate 5 different programs, test them all, and recursively refine the best one.

**Chimera's Gap:**
- `TestConvergence` only maintains one "current" codebase.
- No "backtracking" across entirely different approaches (only small edits).

**The Plan (Synthesis Layer):**
1.  **Implement `TreeSearchStrategy`:**
    - Uses `Environment.checkpoint()` to fork the universe.
    - Maintains a priority queue of `(checkpoint_id, score)`.
    - Explores multiple solution paths in parallel (or sequentially via restore).
2.  **Implement `PythonDSL`:**
    - ARC requires grid manipulations. We shouldn't make the LLM write raw list comprehensions every time.
    - Provide a `grid_lib.py` in the environment by default.

### B. SWE-bench (Software Engineering)

**The Hard Part:**
Real repos are huge. "Read file" is expensive.
- **Failure Mode:** "Context Window Exceeded" or "I fixed the wrong file."
- **Required Capability:** **Repo Mapping** and **Surgical Editing**.

**Chimera's Gap:**
- `LocalEnvironment` is stateless. If an agent runs `cd frontend`, the next command `ls` runs in the root, not `frontend`. This is disorienting for agents trained on persistent shells.
- Docker support is missing.

**The Plan (Environment Layer):**
1.  **Upgrade `Environment` to `PersistentSession`:**
    - Use a long-running `subprocess` (or `pexpect`) to keep the shell alive.
    - `env.run_command("cd src")` should affect subsequent commands.
2.  **Implement `RepoMap` Tool:**
    - A tool that generates a compressed tree structure of the repo (like `tree` but with symbols).

### C. Terminal Bench

**The Hard Part:**
State. Setting `export API_KEY=...` must persist. Background processes must stay alive.

**Chimera's Gap:**
- `LocalEnvironment` resets the shell on every `run_command`.

**The Plan (Environment Layer):**
- **Same as SWE-bench:** The `PersistentSession` upgrade is mandatory here.

---

## 3. Unified Architectural Roadmap

We will tackle these upgrades in a logical order that unlocks capabilities for all benchmarks.

### Phase 1: The "Persistent Shell" Upgrade (Target: Terminal Bench)
*Goal: Enable stateful interactions.*

- [ ] **Refactor `Environment`**: Add `start_session()` and `end_session()`.
- [ ] **Implement `PersistentLocalEnvironment`**:
    - Spawns a background `bash` process.
    - Pipes stdin/stdout.
    - Handles `cwd` tracking.
- [ ] **Verify**: Run a test where tool 1 does `cd x` and tool 2 does `pwd`, asserting it returns `.../x`.

### Phase 2: The "Tree Search" Upgrade (Target: ARC-AGI)
*Goal: Enable non-linear problem solving.*

- [ ] **Enhance `Environment.checkpoint()`**: Ensure it's fast (maybe use git branches instead of full copies?).
- [ ] **Implement `TreeSearchStrategy`**:
    - Input: `Spec` (the goal).
    - Loop:
        1. Select best leaf node (checkpoint).
        2. Generate $N$ next steps (actions/code).
        3. Evaluate each (run tests).
        4. Prune failures, add successes to tree.
    - Output: The path to the solution.

### Phase 3: The "Harness" Integration (Target: SWE-bench)
*Goal: Connect the engine to the data.*

- [ ] **Implement `SWEBenchHarness`**:
    - Download datasets from HuggingFace.
    - Convert "Fail-to-Pass" tests into Chimera `Spec` objects.
    - Connect to `DockerEnvironment` (which wraps `PersistentSession`).

---

## 4. Immediate Action Items

1.  Create `chimera/env/persistent.py` (Draft the stateful shell).
2.  Create `chimera/training/strategies/search.py` (Draft the tree search logic).
3.  Update `chimera/tools/bash.py` to use the session if available.
