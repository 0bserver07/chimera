# Chimera Playbooks

Recipes for integrating Chimera with Claude Code. Each playbook solves a specific pain point, includes setup instructions, and contains a machine-readable recipe that an AI coding agent can follow to build the feature from scratch.

## For Claude Code Users

Install the Chimera plugin and get immediate improvements:

| # | Playbook | Problem It Solves |
|---|----------|-------------------|
| 00 | [Quick Start](00-quick-start.md) | Install everything in one go |
| 01 | [Codebase Search](01-codebase-search.md) | Hallucinated file paths |
| 02 | [Auto-Test & Lint](02-auto-test-lint.md) | Silent regressions, style drift |
| 03 | [Code Review](03-code-review.md) | Shallow reviews, missed security issues |
| 04 | [Context Management](04-context-management.md) | Context degradation, forgetting |
| 05 | [Test Generation](05-test-generation.md) | Missing test coverage |
| 06 | [Migration](06-migration.md) | Manual refactoring |
| 07 | [Benchmarking](07-benchmarking.md) | No way to measure agent quality |

## For Agent Developers

| # | Playbook | What It Adds |
|---|----------|--------------|
| 09 | [Adaptive Learning](09-adaptive-learning.md) | Agents learn from errors across sessions |
| 10 | [Smart Dispatch](10-smart-dispatch.md) | Automatic agent selection |
| 11 | [Workflow Discipline](11-workflow-discipline.md) | Phase gates, scope guards, focus |
| 12 | [Review & Eval](12-review-eval.md) | Pluggable review perspectives and graders |

## For Developers

| # | Playbook | What You Build |
|---|----------|----------------|
| 08 | [Building Agents](08-building-agents.md) | Your own coding agent on Chimera |

## How Playbooks Work

Each playbook has three layers:

1. **Setup** -- install and configure for immediate use
2. **How It Works** -- understand the architecture and customize
3. **Recipe** -- machine-readable spec an AI agent can follow to build the feature
