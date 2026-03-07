# Workflows (Layer 7)

Workflows are thin glue that compose `Agent.run()` with domain-specific parsing and prompting. Each workflow handles a specific development task -- parsing structured input (CI logs, diffs, coverage reports), building targeted prompts, and orchestrating one or more agents through a retry or iteration loop.

All workflows are accessible via both the CLI and the Python API.

## Umbrella Import

```python
from chimera.workflows import (
    CIFixWorkflow,
    ReviewOrchestrator,
    Researcher,
    MigrationPlanner,
    DocGenerator,
    TestGenerator,
)
```

## Available Workflows

| Workflow | Package | CLI Command | Description |
|----------|---------|-------------|-------------|
| [CI Fix](ci-fix.md) | `chimera.ci` | `chimera ci-fix` | Parse CI logs, diagnose failures, fix with retry |
| [Code Review](review.md) | `chimera.review` | `chimera review` | Two-agent reviewer + author iteration |
| [Research](research.md) | `chimera.research` | `chimera research` | Question decomposition, agent research, synthesis |
| [Migration](migration.md) | `chimera.migration` | `chimera migrate` | Rule-based code transforms with presets |
| [Doc Generation](docgen.md) | `chimera.docs` | `chimera docs` | AST-based documentation scanning |
| [Test Generation](testgen.md) | `chimera.testgen` | `chimera testgen` | Source analysis, test skeleton generation |

## Shared Infrastructure: GitWorkflow

`GitWorkflow` provides branch isolation, diff context, and commit strategies used by workflows that modify code. It is not a workflow itself but shared infrastructure that any workflow can use.

```python
from chimera.workflows import GitWorkflow, CommitStrategy

workflow = GitWorkflow(env, strategy=CommitStrategy.PER_TASK)
branch = workflow.start("fix-auth-bug")
# ... agent work ...
workflow.commit("fix: resolve auth token expiry")
workflow.finish(merge=True)
```

`CommitStrategy` options: `PER_STEP` (commit after each agent step), `PER_TASK` (commit when task completes), `MANUAL` (only on explicit request).
