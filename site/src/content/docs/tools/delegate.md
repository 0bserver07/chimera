---
title: "delegate — hand a task to a sub-agent"
description: "Run a task on a separately-configured sub-agent and return its final output. Useful for capability isolation and for parallel decomposition via composition primitives."
---

`delegate` wraps a sub-`Agent` as a tool. The parent agent calls `delegate({"task": "..."})`, the sub-agent runs to completion in the same environment, and its final answer comes back as the tool output. Each `DelegateTool` instance is bound to one sub-agent — register multiple instances under different `tool_name`s to expose multiple specialists.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `task` | string | yes | The task description to hand off. |

## Constructor

| Arg | Type | Description |
|---|---|---|
| `sub_agent` | `Agent` | Required. The agent to delegate to. |
| `tool_name` | string | Override the default `delegate` name (e.g. `delegate_to_reviewer`). |

## Example

```python
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider
from chimera.tools.delegate import DelegateTool
from chimera.tools.read import ReadFileTool
from chimera.tools.search import SearchTool

reviewer = Agent(
    provider=create_provider(model="glm-5"),
    tools=[ReadFileTool(), SearchTool()],
    system_prompt="You are a code reviewer. Find bugs.",
)

main_agent = Agent(
    provider=create_provider(model="glm-5"),
    tools=[DelegateTool(sub_agent=reviewer, tool_name="delegate_to_reviewer")],
)

main_agent.run("Have the reviewer audit chimera/core/agent.py", env)
```

## Output sample

```
The reviewer found 2 issues:
1. agent.py:142 — missing null check before .run()
2. agent.py:178 — exception swallowed without logging
```

## See also

- [`agent` tool](https://github.com/0bserver07/chimera/blob/master/chimera/tools/agent_tool.py) — alternative shape that constructs sub-agents lazily.
- [Composition](/chimera/concepts/composition/) — `Pipeline`, `Ensemble`, `Supervisor` for multi-agent topologies.
