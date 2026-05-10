---
title: "CI Fix Workflow"
description: "CI Fix Workflow"
---

## What It Does

`CIFixWorkflow` automates diagnosing and fixing CI failures. It parses raw CI log output into structured `FailureInfo` objects, builds a targeted prompt, runs an agent to apply fixes, and retries up to a configurable number of attempts. Supports pytest, Jest, Go test, and Cargo test log formats out of the box.

## CLI

```bash
chimera ci-fix --log build.log --model claude-sonnet-4 --max-attempts 3
```

## Python API

```python
from chimera.ci import CIFixWorkflow, parse_ci_log
from chimera.core.agent import Agent
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider

agent = Agent(provider=create_provider(model="glm-5"))
env = LocalEnvironment(".")

workflow = CIFixWorkflow(max_attempts=3, budget=1.0)
success = workflow.run(log=open("build.log").read(), agent=agent, env=env)

print(f"Fixed: {success}")
print(f"Attempts: {len(workflow.attempts)}")
print(f"Total cost: ${workflow.total_cost:.2f}")
```

## Key Classes

### `CIFixWorkflow`

```python
class CIFixWorkflow:
    def __init__(self, max_attempts: int = 3, budget: float | None = None) -> None
    def diagnose(self, log: str) -> list[FailureInfo]
    def build_prompt(self, failures: list[FailureInfo], context: str = "") -> str
    def run(self, log: str, agent: Agent, env: Environment) -> bool
    def record_attempt(self, failures, prompt, success=False, cost=0.0, error="") -> FixAttempt
```

**Properties:** `attempts` (list of `FixAttempt`), `max_attempts`, `total_cost`, `succeeded`.

### `FailureInfo`

Dataclass with fields: `test_name`, `file_path`, `line_number`, `error_type`, `error_message`, `stack_trace`. Property `summary` returns a pipe-delimited one-liner.

### `FixAttempt`

Dataclass with fields: `failures` (list of `FailureInfo`), `prompt`, `success`, `cost`, `error`.

### `parse_ci_log(log: str) -> list[FailureInfo]`

Standalone function that extracts failures from raw CI output. Handles pytest (`FAILED path::test`), Jest (`FAIL path`), Go (`--- FAIL: TestName`), Cargo (`test name ... FAILED`), and generic `Error:` patterns as fallback.

## Integration

- Wires through `chimera.core.agent.Agent.run()` once per attempt;
  the workflow itself is provider-agnostic and inherits whatever
  `Agent` you pass in.
- `parse_ci_log()` is exposed as a standalone function for tools that
  want CI-failure parsing without the full workflow (used internally
  by `chimera ci-fix --log`).
- `FixAttempt` records are appended to `workflow.attempts` on every
  retry, including failed ones — useful for budget tracking and
  reproducibility reports.
- The `chimera ci-fix` CLI is a thin wrapper around this workflow; see
  [CLI & REPL → Synthesis & evaluation subcommands](/modules/cli/#synthesis--evaluation-subcommands).

## Import

```python
from chimera.ci import CIFixWorkflow, FailureInfo, FixAttempt, parse_ci_log
```
