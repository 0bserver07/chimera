---
title: "chimera.workflows"
description: "Reference for chimera.workflows — GitWorkflow, CIFix, Reviewer, Researcher, Migration, DocGenerator, TestGenerator."
---

`chimera.workflows` (and the per-feature submodules) wraps the agent
loop in opinionated, end-to-end workflows. Each workflow has an
equivalent CLI subcommand.

## Top-level exports

```python
from chimera.workflows.git_workflow import GitWorkflow
from chimera.ci.fix_workflow import CIFixWorkflow
from chimera.review.orchestrator import ReviewOrchestrator
from chimera.research.researcher import Researcher
from chimera.migration.planner import MigrationPlanner
from chimera.docs.generator import DocGenerator
from chimera.testgen.generator import TestGenerator
```

| Workflow | Module | CLI | Purpose |
|---|---|---|---|
| `GitWorkflow` | `chimera.workflows.git_workflow` | (component) | Branch isolation, diff context, commit strategies. Composed by other workflows. |
| `CIFixWorkflow` | `chimera.ci.fix_workflow` | `chimera ci-fix` | Parse CI logs → prompt agent → run loop → retry until green. |
| `ReviewOrchestrator` | `chimera.review.orchestrator` | `chimera review` | Reviewer agent + author agent iteration. |
| `Researcher` | `chimera.research.researcher` | `chimera research` | Plan decomposition → `Agent.run()` per sub-question → synthesis. |
| `MigrationPlanner` | `chimera.migration.planner` | `chimera migrate` | Rule-based code transforms with presets (`python2-to-3`, `commonjs-to-esm`). |
| `DocGenerator` | `chimera.docs.generator` | `chimera docs` | AST-based doc scanning + generation. |
| `TestGenerator` | `chimera.testgen.generator` | `chimera testgen` | Source analysis → test-case skeletons. |

## See also

- [`chimera.cli`](/reference/cli/) for the matching subcommand flags.
- [`chimera.review.perspective`](/modules/) for the eight review
  perspectives `ReviewOrchestrator` cycles through.
