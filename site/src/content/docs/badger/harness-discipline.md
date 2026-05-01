---
title: Harness Discipline
description: Why badger ships max-steps=25 and rerun-on-failure as opt-in defaults.
---

# Harness Discipline

Badger inherits a **harness-rewrite** posture. The thesis (paraphrased
from the upstream's own framing): a clean rewrite of an agent harness
is more valuable than a museum-quality archive of the leaked snapshot.
What survives the rewrite is the technique — what gets dropped is
incidental complexity.

Badger applies that thesis to Chimera in three ways.

## 1. Tighter max-step budget

`--max-steps` defaults to **25** in badger, vs **50** elsewhere.

Why: long agent trajectories are expensive both in tokens and in
context drift. A 25-step ceiling forces the agent to plan more
carefully up front. When the trajectory legitimately needs more steps,
the operator opts in explicitly:

```bash
chimera badger -p "..." --max-steps 80
```

## 2. Rerun-on-failure as a first-class flag

Other Chimera CLIs treat the agent's first attempt as definitive.
Badger ships `--rerun-on-failure`: when the first attempt produces a
test failure or an obvious syntax error in tool output, the harness
resets and retries with a refined prompt up to `--max-reruns` extra
attempts.

```bash
chimera badger -p "Fix the broken pytest cases under tests/api/" \
  --rerun-on-failure --max-reruns 2
```

The detection is intentionally conservative — see
[rerun-on-failure.md](./rerun-on-failure.md) for the marker list.
False negatives (real failures that slip past) are preferred over
false positives (rerunning a healthy trajectory).

## 3. Parity-tracker

The `parity` subcommand diffs the **live** agent against a declared
schema (tools, max-steps, slash commands, default model,
`rerun_on_failure` flag). When the schema and the live snapshot match,
parity returns rc=0; when they diverge, rc=1 with a diff report.

The schema lives at `PARITY.md`, `PARITY.json`, or `PARITY.yaml`. Any
of those formats works — see [parity.md](./parity.md) for details.

## What badger is **not**

- A rewrite of any specific upstream codebase. Comparative language
  uses "badger", "the upstream", or "the harness-rewrite tradition".
- A drop-in replacement for ferret/otter/mink. Pick badger when the
  harness-rewrite posture is what you want.

## When to reach for badger

- You want a focused trajectory, not a sprawling one.
- You expect transient failures (flaky tests, syntax-error from a
  speculative edit) and want automatic recovery.
- You operate against a parity contract — a schema you want to enforce
  in CI.

## When not to reach for badger

- You want sandbox-first / IDE-first ergonomics: use ferret.
- You want a server-first posture: use otter.
- You want a Kimi-on-Ollama experience: use mink.
- You want shell-mode toggles: use stoat.
