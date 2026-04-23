---
title: "Philosophy"
description: "Why Chimera exists and where agentic coding is heading"
---

## Program Synthesis, Not Chat

Chimera's core verb is `.synthesize()`. Not `.generate()`, not `.create()`. Synthesize. Because that's what it is.

Program synthesis has been studying automated code generation from specifications for decades. The search engine changed from enumerative search to constraint solving to LLMs, but the structure is the same: specify what you want, search for a program that satisfies the spec, verify the result. The concepts are well defined: DSLs, grammars, synthesizers, verifiers, oracles.

Agentic coding maps directly onto this. The spec (and its tests) is the loss function. Agent iterations are training steps. Each pass through the code, each test run, each edit moves toward convergence. The output is a synthesized codebase, an artifact you deploy without inspecting every line, the same way we deploy neural networks without reading individual weights.

The question is what the right set of high-level abstractions looks like for steering this process with minimal cognitive overhead. Chimera is one attempt at an answer, grounded in program synthesis.

## Classic ML Problems Apply

If agentic coding really is ML, then classic ML problems apply:

- **Overfitting to the spec**: the agent passes all tests but the code doesn't generalize
- **Clever Hans shortcuts**: the agent finds a hack that satisfies tests without understanding the problem
- **Data leakage**: the agent reads test files and reverse-engineers the expected output
- **Concept drift**: the codebase works today but breaks as dependencies change

Chimera's synthesis layer is designed with these problems in mind. Constraints check for things beyond test passage: code complexity, type coverage, lint compliance. Strategies like CEGIS (counterexample-guided synthesis) focus the agent on one failing test at a time to prevent oscillation. Validation splits detect overfitting by holding out test cases.

## The Monolith Problem

Every major coding agent today is a monolith. Claude Code is closed-source. Codex is Rust, tightly coupled to OpenAI. Aider is 50,000+ lines organized around one workflow. If you want to understand how any of them work, you reverse-engineer them. If you want to build your own, you start from scratch.

This is what every field looks like before decomposition happens. Custom stacks everywhere. No shared vocabulary. Results you can't reproduce because the infrastructure isn't shared.

Chimera decomposes the agent into composable primitives: providers, tools, loops, environments, and strategies. The bet is that the right architecture hasn't been found yet. By making every component swappable, researchers and developers can explore the design space without rebuilding from scratch.

## Where This Goes

The gap between having the primitives and getting competitive results is real. Chimera's benchmarks are honest: HumanEval 90.9%, SWE-bench 10%. The question is which combination of loop, tools, context management, and prompting gets you from 10% to 70% on SWE-bench with an open model.

The primitives existed in deep learning for years before someone found the right composition. That's a research problem now, not an infrastructure problem. Chimera is the infrastructure that makes the research tractable.
