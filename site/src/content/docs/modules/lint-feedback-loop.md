---
title: "Lint Feedback Loop"
description: "Lint Feedback Loop"
---

`chimera.core.loops.lint_feedback` runs a linter after each agent turn and
feeds any errors back as context for the agent to fix. Inspired by Aider's
lint-and-fix workflow where `lint_edited` feeds errors back as a
`reflected_message`.

## Key Classes

| Class | Description |
|-------|-------------|
| `LintFeedbackLoop` | Wraps an inner loop with post-turn linting and iterative fixing |

## Quick Start

```python
from chimera.core.loops.lint_feedback import LintFeedbackLoop

loop = LintFeedbackLoop(
    linter="ruff",
    lint_args=["check", "--no-fix"],
    max_lint_rounds=3,
)

result = loop.run(provider, tools, context, env)
print(f"Lint rounds: {len(loop.lint_history)}")
```

## How It Works

1. The inner loop runs the original task to completion.
2. The configured linter runs on the workspace.
3. If lint errors are found, the agent gets a new context with the errors
   and instructions to fix them without changing functionality.
4. Steps 2-3 repeat up to `max_lint_rounds` times or until the linter
   reports no errors.

## Custom Linter

Any linter that writes errors to stdout/stderr works:

```python
# Use flake8 instead of ruff
loop = LintFeedbackLoop(linter="flake8", lint_args=["--max-line-length=100"])

# Use eslint for JavaScript projects
loop = LintFeedbackLoop(linter="eslint", lint_args=["src/"])
```

## Inspecting Lint History

After `run()` completes, `lint_history` contains the raw output from
each lint round:

```python
for i, output in enumerate(loop.lint_history):
    if output.strip():
        print(f"Round {i+1}: {len(output.splitlines())} lines of errors")
    else:
        print(f"Round {i+1}: clean")
```

## Import Reference

```python
from chimera.core.loops.lint_feedback import LintFeedbackLoop
```

## Related

- [Loops](/../concepts/loops/) -- overview of all loop types
- [Retry Loop](/retry-loop/) -- retry wrapper with scoring
- [Plan/Act Loop](/plan-act-loop/) -- two-phase plan then execute
