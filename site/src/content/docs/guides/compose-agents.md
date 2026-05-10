---
title: "Compose Agents"
description: "Compose Agents"
---

A single agent is powerful, but real workflows often need multiple specialists
working together.  Chimera provides three composition patterns out of the box:
**Pipeline**, **Ensemble**, and **Supervisor**.

---

## Pipeline -- Sequential Handoff

A `Pipeline` runs agents one after another.  The output of agent N becomes
the input (task) of agent N+1.  If any agent fails, the pipeline stops early.

```python
from chimera import Agent, Pipeline, Prompt, create_provider

provider = create_provider(model="glm-5")

planner = Agent(
    provider=provider,
    tools=[],
    prompt=Prompt.from_string(
        "You are a planning agent. Break the task into concrete steps."
    ),
    name="planner",
)

coder = Agent(
    provider=provider,
    tools=[],  # add tools as needed
    prompt=Prompt.from_string(
        "You are a coding agent. Implement the plan you receive."
    ),
    name="coder",
)

reviewer = Agent(
    provider=provider,
    tools=[],
    prompt=Prompt.from_string(
        "You are a code reviewer. Review the code for bugs and style issues. "
        "Return the final, approved code."
    ),
    name="reviewer",
)

pipeline = Pipeline(agents=[planner, coder, reviewer])
result = pipeline.run("Build a Python CLI that converts CSV to JSON.", env=None)

print(result.output)
print(f"Total steps: {result.steps}, Total cost: ${result.cost:.4f}")
```

`Pipeline.run` returns a single `AgentResult` with aggregated step counts,
tool call totals, and cost.

---

## Ensemble -- Parallel Attempts

An `Ensemble` runs every agent on the **same task** independently and
collects all results.  Use this when you want diversity of approaches -- then
pick the best one.

```python
from chimera import Agent, Ensemble, Prompt, create_provider

provider = create_provider(model="glm-5")

agents = [
    Agent(
        provider=provider,
        tools=[],
        prompt=Prompt.from_string(
            "Solve the problem. Prefer simple, readable code."
        ),
        name=f"solver-{i}",
    )
    for i in range(3)
]

ensemble = Ensemble(agents=agents)
results = ensemble.run("Write a function to check if a string is a palindrome.", env=None)

# Pick the best result (default: first successful)
best = ensemble.best(results)
print(best.output)
```

### Custom selection

`ensemble.best()` returns the first successful result by default.  Override it
for smarter selection -- for example, pick the cheapest successful solution:

```python
successful = [r for r in results if r.success]
cheapest = min(successful, key=lambda r: r.cost)
print(cheapest.output)
```

---

## Supervisor -- Coordinator + Workers

A `Supervisor` gives one coordinator agent the ability to **delegate** tasks
to specialist workers.  Under the hood, each worker is exposed to the
coordinator as a `DelegateTool`.

```python
from chimera import Agent, DEFAULT_TOOLS, Prompt, Supervisor, create_provider

provider = create_provider(model="glm-5")

# Workers -- each has its own tools and prompt
frontend_dev = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    prompt=Prompt.from_string("You are a frontend engineer. Write HTML/CSS/JS."),
    name="frontend",
)

backend_dev = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    prompt=Prompt.from_string("You are a backend engineer. Write Python APIs."),
    name="backend",
)

test_writer = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    prompt=Prompt.from_string("You are a QA engineer. Write pytest test cases."),
    name="tester",
)

# Coordinator -- gets delegate tools automatically
coordinator = Agent(
    provider=provider,
    tools=[],  # Supervisor adds delegate tools for each worker
    prompt=Prompt.from_string(
        "You are a tech lead. Break down the task and delegate to your team:\n"
        "- 'frontend' for UI work\n"
        "- 'backend' for API work\n"
        "- 'tester' for writing tests\n"
        "Synthesise their outputs into a final deliverable."
    ),
    name="coordinator",
)

supervisor = Supervisor(
    coordinator=coordinator,
    workers={
        "frontend": frontend_dev,
        "backend": backend_dev,
        "tester": test_writer,
    },
)

result = supervisor.run("Build a todo-list web app with a REST API.", env=None)
print(result.output)
```

When the coordinator calls the tool named `"frontend"`, Chimera runs
`frontend_dev.run(task, env)` behind the scenes and returns the result.  The
coordinator sees the worker's output as a tool result and can continue
reasoning.

---

## Choosing a Pattern

| Pattern | When to use | Trade-off |
|---|---|---|
| **Pipeline** | Clearly ordered stages (plan, implement, review) | Sequential -- slow but predictable |
| **Ensemble** | Need diverse solutions to pick from | Runs N agents -- higher cost, higher quality ceiling |
| **Supervisor** | Complex task that requires coordination across specialists | Flexible but relies on the coordinator making good delegation decisions |

All three patterns return `AgentResult` (or `list[AgentResult]` for Ensemble),
so you can nest them: a Pipeline whose first stage is a Supervisor, or an
Ensemble whose agents are themselves Pipelines.

---

## Next Steps

- [Build a Coding Agent](/build-a-coding-agent/) -- master the single-agent fundamentals first.
- [Add a Custom Tool](/add-custom-tool/) -- give your workers specialised tools.
- [Configure Permissions](/configure-permissions/) -- add guardrails to multi-agent systems.
