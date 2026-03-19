---
title: "Automate CI Fixes"
description: "Automate CI Fixes"
---

**Goal:** Automatically diagnose CI failures from log output and have an agent fix them, with optional branch isolation and retry logic.

---

## Prerequisites

- A CI log file (pytest, jest, go test, or cargo test output)
- An API key for your LLM provider (e.g. `ANTHROPIC_API_KEY`)
- Chimera installed (`pip install chimera`)

---

## Step 1: Parse the CI log

`parse_ci_log()` extracts structured `FailureInfo` objects from raw log text. It handles pytest, jest, go test, and cargo test formats automatically.

```python
from chimera.ci.failure_parser import parse_ci_log

with open("build.log") as f:
    log = f.read()

failures = parse_ci_log(log)
for fail in failures:
    print(fail.summary)
```

Each `FailureInfo` contains: `test_name`, `file_path`, `line_number`, `error_type`, `error_message`, `stack_trace`. The `summary` property joins these into a readable one-liner like `tests/test_auth.py:42 | test_login | AssertionError | expected 200 got 401`.

If no framework-specific patterns match, the parser falls back to generic `Error`/`Exception`/`FATAL` extraction.

---

## Step 2: Create the workflow

```python
from chimera.ci.fix_workflow import CIFixWorkflow

workflow = CIFixWorkflow(
    max_attempts=3,   # retry up to 3 times
    budget=1.0,       # stop if total cost exceeds $1.00 (optional)
)
```

---

## Step 3: Diagnose

Call `diagnose()` to parse failures without running the agent yet:

```python
failures = workflow.diagnose(log)
print(f"Found {len(failures)} failures")
```

This is the same as calling `parse_ci_log()` directly but keeps the workflow as the single entry point.

---

## Step 4: Build the prompt

Generate an agent prompt from the parsed failures:

```python
prompt = workflow.build_prompt(
    failures,
    context="FastAPI app with SQLAlchemy models",  # optional
)
print(prompt)
```

Output:

```
Fix the following CI failures:
1. tests/test_auth.py:42 | test_login | AssertionError | expected 200 got 401
2. tests/test_users.py::test_create_user | TypeError | missing required argument

Context: FastAPI app with SQLAlchemy models
Diagnose the root cause, make minimal changes, and verify the fix by running tests.
```

---

## Step 5: Run with an agent

The simplest path -- `workflow.run()` handles the full diagnose-prompt-retry loop:

```python
from chimera.core.agent import Agent
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider

provider = create_provider(model="claude-sonnet-4-20250514")
agent = Agent(provider=provider)
env = LocalEnvironment(workdir="/path/to/repo")

success = workflow.run(log, agent, env)
```

The workflow calls `agent.run()` up to `max_attempts` times, stopping early on success or when the budget is exhausted.

---

## Step 6: Check results

After `run()` completes, inspect the attempt history:

```python
print(f"Succeeded: {workflow.succeeded}")
print(f"Total cost: ${workflow.total_cost:.4f}")
print(f"Attempts: {len(workflow.attempts)}")

for i, attempt in enumerate(workflow.attempts, 1):
    print(f"  #{i}: success={attempt.success}, cost=${attempt.cost:.4f}")
```

Each `FixAttempt` records: `failures`, `prompt`, `success`, `cost`, `error`.

---

## Step 7: Branch isolation with GitWorkflow

Combine `CIFixWorkflow` with `GitWorkflow` to make fixes on an isolated branch:

```python
from chimera.env.git_env import GitEnvironment
from chimera.workflows.git_workflow import GitWorkflow

git_env = GitEnvironment(workdir="/path/to/repo")
git_wf = GitWorkflow(git_env)

# Create an isolated branch
git_wf.start("fix-ci-failures")

# Run the fix workflow on the branch
success = workflow.run(log, agent, git_env)

if success:
    git_wf.commit("fix: resolve CI failures")
    git_wf.finish(merge=True)  # merge back to original branch
else:
    git_wf.abort()  # discard changes, return to original branch
```

---

## Step 8: CLI shortcut

Skip the Python script entirely:

```bash
chimera ci-fix --log build.log --max-attempts 3 --model claude-sonnet-4-20250514
```

This reads the log, creates a provider and agent, runs `CIFixWorkflow.run()`, and prints `CI fix: SUCCESS` or `CI fix: FAILED after N attempts`.

---

## Complete example

```python
from chimera.ci.fix_workflow import CIFixWorkflow
from chimera.core.agent import Agent
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider

# Read CI log
with open("build.log") as f:
    log = f.read()

# Set up
provider = create_provider(model="claude-sonnet-4-20250514")
agent = Agent(provider=provider)
env = LocalEnvironment(workdir=".")

# Run workflow
workflow = CIFixWorkflow(max_attempts=3, budget=2.0)
success = workflow.run(log, agent, env)

# Report
if success:
    print(f"Fixed in {len(workflow.attempts)} attempt(s), cost: ${workflow.total_cost:.4f}")
else:
    print(f"Failed after {len(workflow.attempts)} attempts, cost: ${workflow.total_cost:.4f}")
    for i, attempt in enumerate(workflow.attempts, 1):
        print(f"  Attempt #{i}: {len(attempt.failures)} failures detected")
```

---

## Next steps

- [Compose Agents](/compose-agents/) -- chain CI fix with review using a Pipeline
- [Configure Permissions](/configure-permissions/) -- add guardrails to agent file edits
