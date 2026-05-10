---
title: "Composition"
description: "Composition"
---

`chimera.composition` chains multiple `Agent` instances into
higher-order patterns. Four primitives — `Pipeline`, `Ensemble`,
`Supervisor`, `RoleBasedTeam` — cover the common multi-agent
arrangements without any framework lock-in.

## Quick Start

```python
from chimera.composition import Pipeline
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider

provider = create_provider(model="claude-sonnet-4-6")
planner = Agent(provider=provider, name="planner")
coder   = Agent(provider=provider, name="coder")
reviewer = Agent(provider=provider, name="reviewer")

pipeline = Pipeline(agents=[planner, coder, reviewer])
result = pipeline.run("Build a CLI calculator.", env=None)
print(result.output)
```

## Pipeline (sequential)

`Pipeline` chains agents so the output of agent *N* becomes the input
of agent *N+1*. Execution stops early on the first failure and
returns the failing stage's `AgentResult`.

```python
from chimera.composition import Pipeline

pipeline = Pipeline(agents=[planner, coder, reviewer])
result = pipeline.run(task, env=sandbox)
```

| Property | Description |
|---|---|
| `agents` | Ordered list of `Agent` instances forming the stages. |

| Method | Description |
|---|---|
| `run(task, env)` | Execute the pipeline end-to-end. Returns aggregated `AgentResult` (cost, step count, tool-call totals). |

## Ensemble (parallel fan-out)

`Ensemble` runs every agent on the *same* task and collects all
results. When `env` supports `clone()`, agents run in parallel using
a `ThreadPoolExecutor`; otherwise they run sequentially sharing the
same environment.

```python
from chimera.composition import Ensemble

ensemble = Ensemble(
    agents=[agent_a, agent_b, agent_c],
    max_workers=3,
    timeout=120.0,
)
results = ensemble.run("Solve the problem.", env=None)
winner = ensemble.best(results)            # first successful result
```

| Property | Description |
|---|---|
| `agents` | The pool of agents that each attempt the task. |
| `max_workers` | Maximum parallel threads (default: number of agents). |
| `timeout` | Per-agent timeout in seconds (default: no timeout). |

| Method | Description |
|---|---|
| `run(task, env)` | Fan-out every agent. Returns a list of `AgentResult`. |
| `best(results)` | Pick the winning result; default = first successful. Override for majority voting, lowest cost, etc. |

## Supervisor (coordinator + workers)

`Supervisor` gives a *coordinator* agent a `DelegateTool` for every
*worker* agent. The coordinator dispatches sub-tasks to workers and
synthesises their results.

```python
from chimera.composition import Supervisor

supervisor = Supervisor(
    coordinator=manager_agent,
    workers={"research": researcher, "code": coder, "test": tester},
)
result = supervisor.run("Implement and test a caching layer.", env=sandbox)
```

The coordinator's tool surface includes `DelegateTool(name="research",
worker=researcher)` for each entry in the `workers` dict.

## RoleBasedTeam

`RoleBasedTeam` runs a sequence of role-specialised agents. Each
`Role` defines the system prompt, tool access, loop type, and step
budget for that stage. Default roles wire a planner → coder → reviewer
→ tester pipeline.

```python
from chimera.composition.roles import Role, RoleBasedTeam
from chimera.providers.factory import create_provider

provider = create_provider(model="claude-sonnet-4-6")
team = RoleBasedTeam(provider=provider)
result = team.run("Build a REST API for user management.", env=sandbox)
```

Custom roles:

```python
from chimera.composition.roles import Role, RoleBasedTeam, CODER

analyst = Role(
    name="analyst",
    description="Analyze requirements and break them into tasks",
    tool_names=["read_file", "search", "list_files", "think"],
    system_prompt="You are a requirements analyst...",
    loop_type="plan_act",
)
team = RoleBasedTeam(provider=provider, roles=[analyst, CODER])
```

| Field | Description |
|---|---|
| `name` | Role identifier (used for routing in the team). |
| `description` | Human-readable summary surfaced in the orchestrator prompt. |
| `tool_names` | Tools available to this role's agent. |
| `system_prompt` | Role-specific system prompt. |
| `loop_type` | `"react"`, `"plan_act"`, `"reflexion"`, `"tree_of_thought"`. |
| `max_steps` | Optional step budget for this role. |

## Comparison

| Pattern | Topology | When to use |
|---|---|---|
| **Pipeline** | Linear | Each stage depends on the previous one (plan → code → test). |
| **Ensemble** | Fan-out | Multiple model attempts to compare; best-of-N. |
| **Supervisor** | Coordinator + workers (delegated) | Coordinator decides what to delegate, when, and how to combine. |
| **RoleBasedTeam** | Linear with role-specific tool / prompt / loop | Same as Pipeline but each agent is auto-built from a `Role` instead of pre-instantiated. |

## Integration

- **Workflows**: `ReviewOrchestrator` is a specialised two-agent
  composition (reviewer + author iteration) built on the same
  primitives. See [Code Review Workflow](/workflows/review/).
- **Synthesis strategies**: `MajorityVoting`, `Ensemble` (synthesis
  variant), and `AIMOEnsemble` reuse the parallel fan-out shape from
  `chimera.composition.Ensemble` but layer in synthesis-specific
  voting and budget logic.
- **DelegateTool**: `chimera.tools.delegate.DelegateTool` is the
  underlying primitive that `Supervisor` uses; it's a regular
  `BaseTool` and can be wired into any agent that wants
  hierarchical delegation.

## Import Reference

```python
from chimera.composition import (
    Ensemble,
    Pipeline,
    Role,
    RoleBasedTeam,
    Supervisor,
)
```
