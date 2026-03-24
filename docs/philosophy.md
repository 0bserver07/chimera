---
title: "Philosophy"
description: "Why Chimera exists and where agentic coding is heading"
---

## Agentic Coding is Machine Learning

There's a growing recognition that agentic coding follows machine learning patterns. The spec is the loss function. The agent loop is the optimizer. The generated codebase is the model — an artifact you deploy without inspecting every line, the same way we deploy neural networks without reading individual weights.

This framing isn't new.  [asked publicly](https://x.com//status/) what the right set of high-level abstractions would be for steering codebase "training" with minimal cognitive overhead. Others have proposed answers — DSPy for prompt optimization, LangChain for agent orchestration, CrewAI for multi-agent workflows.

Chimera is a different answer. Instead of optimizing prompts or orchestrating conversations, Chimera decomposes coding agents into the same kind of composable primitives that made deep learning frameworks productive: providers (like backends), tools (like layers), loops (like optimizers), environments (like data pipelines), and strategies (like training schedules).

## What This Means in Practice

If agentic coding really is ML, then classic ML problems apply:

- **Overfitting to the spec** — the agent passes all tests but the code doesn't generalize
- **Clever Hans shortcuts** — the agent finds a hack that satisfies tests without understanding the problem
- **Data leakage** — the agent reads test files and reverse-engineers the expected output
- **Concept drift** — the codebase works today but breaks as dependencies change

Chimera's synthesis layer (`synthesize()`, `Trainer`, `Strategy`, `Constraint`) is designed with these problems in mind. Constraints check for things beyond test passage — code complexity, type coverage, lint compliance. Strategies like CEGIS (counterexample-guided synthesis) focus the agent on one failing test at a time to prevent oscillation. Validation splits detect overfitting by holding out test cases.

## Why Composability Matters

Every major coding agent today is a monolith. Claude Code, Codex, Aider, OpenHands — each is a complete system built around one set of assumptions. If you want to change the loop, you fork the project. If you want a different tool set, you rewrite the integration layer.

Chimera bets that the right architecture hasn't been found yet. By making every component swappable — the loop, the tools, the provider, the environment, the strategy — researchers and developers can explore the design space without rebuilding from scratch. The same way Keras let researchers try new architectures in an afternoon instead of a month.

## Where This Goes

The current state of coding agents is where deep learning was around 2012: custom training loops, no shared abstractions, results that can't be reproduced. The path forward is the same — shared primitives, standardized benchmarks, and a community iterating on architectures.

Chimera is early. The benchmarks are honest (HumanEval 90.9%, SWE-bench 10%). The gap between composable blocks and competitive results is real. But the blocks exist, and the question is no longer "can we build a coding agent framework" but "what's the right composition."
