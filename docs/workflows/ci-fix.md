# CI Fix Workflow

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

agent = Agent(provider=create_provider(model="claude-sonnet-4-20250514"))
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

## Import

```python
from chimera.ci import CIFixWorkflow, FailureInfo, parse_ci_log
```
