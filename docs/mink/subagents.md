# Subagents — the `Task` tool

Chimera mink reproduces the ecosystem subagent model: a parent agent can
invoke a `Task` tool that spawns a child agent loop with an isolated
context. The child runs to completion and returns its final output, or
runs in the background and writes its result to a file.

## Subagent definition schema

Subagent definitions live in Markdown files with YAML frontmatter:

```markdown
---
name: reviewer
description: Reviews staged changes for correctness and style
model: kimi-k2.6:cloud
tools: [read_file, search, list_files]
loop: react
max_iterations: 30
triggers: [review, audit]
---
You are a code reviewer. Read the changed files, run the tests, and
report concrete issues with file:line references.
```

### Discovery order

The `AgentLoader` walks three search paths and applies last-wins
overrides per name:

1. **Built-in** (`chimera/builtin_agents/*.md`)
2. **User**    (`~/.claude/agents/*.md`)
3. **Project** (`./.claude/agents/*.md`)

This matches the ecosystem layout 1:1 so an existing user's `.claude/agents`
directory works without changes.

### Frontmatter fields

| Field            | Type      | Default      | Notes                                 |
|------------------|-----------|--------------|---------------------------------------|
| `name`           | string    | filename     | Lookup key for `subagent_type`.       |
| `description`    | string    | `""`         | Shown in `task_list` output.          |
| `system_prompt`  | body      | required     | Body after the second `---`.          |
| `model`          | string    | parent's     | Optional override.                    |
| `tools`          | list[str] | parent's     | Whitelist; missing names are dropped. |
| `loop`           | string    | `react`      | Resolves via `_LOOP_REGISTRY`.        |
| `max_iterations` | int       | `50`         | Capped by parent's `max_steps`.       |

## `Task` tool input schema

```json
{
  "description":       "Short (3-5 word) summary",
  "prompt":            "Detailed task prompt",
  "subagent_type":     "reviewer",
  "isolation":         "full",
  "run_in_background": false,
  "model":             "kimi-k2.6:cloud",
  "allowed_tools":     ["read_file", "bash"],
  "name":              "review-pr-42"
}
```

`description`, `prompt`, and `subagent_type` are required. Everything
else has sensible defaults.

## Isolation tiers

| Tier        | Context                              | File tracker         | Permissions                         | Use case                            |
|-------------|--------------------------------------|----------------------|-------------------------------------|-------------------------------------|
| `full`      | Fresh; parent history hidden         | Cloned snapshot      | `AllowList(allowed_tools)` or `AutoApprove` | Default; isolated subtasks. |
| `selective` | Fresh                                | Cloned snapshot      | Shared permission flags             | Constrained delegation.             |
| `shared`    | Fresh (system prompt only)           | Shared instance      | Shared                              | Coordinator/worker patterns.        |

In all tiers, **child writes to its own `Context` never bubble up to the
parent**. Tier `shared` shares the file tracker so reads/writes propagate
to the parent's compaction view.

## Cancellation cascade

Each child is constructed with a fresh `CancellationToken`. When the
parent loop has its own token, `_create_child_context` registers a
callback that calls `child_token.cancel()` whenever the parent token
trips. The reverse never happens — cancelling the child does **not**
cancel the parent. This matches the ecosystem's `abort_signal` semantics.

The child loop checks its token between every step and during stream
accumulation, so cooperative shutdown is sub-second under typical
provider latencies.

## Foreground vs. background

### Foreground (default)

```python
result = task_tool.execute(
    {"description": "lint", "prompt": "Lint chimera/cli/", "subagent_type": "linter"},
    env=env,
)
print(result.output)         # blocks until the child finishes
```

### Background

```python
result = task_tool.execute(
    {"description": "bench", "prompt": "Run swe-bench-lite", "subagent_type": "bencher",
     "run_in_background": True},
    env=env,
)
agent_id = json.loads(result.output)["agent_id"]
```

The call returns immediately. The child runs in a daemon thread and
writes its final result JSON to:

```
~/.chimera/tasks/<agent_id>.output
```

The output file shape:

```json
{
  "agent_id": "task_a1b2c3d4e5f6",
  "description": "bench",
  "subagent_type": "bencher",
  "status": "completed",
  "started_at": 1714076400.123,
  "error": null,
  "result": {
    "output": "...",
    "steps": 7,
    "tool_calls_total": 12,
    "cost": 0.034,
    "success": true,
    "error": null
  }
}
```

## Companion tools

| Tool          | Purpose                                                |
|---------------|--------------------------------------------------------|
| `task_list`   | Enumerate every known task with status.                |
| `task_get`    | Inspect a single task's metadata.                      |
| `task_output` | Read the JSON result of a finished task (raises `TaskNotFinished` while running). |
| `task_stop`   | Signal cooperative cancellation for a running task.    |

All four operate on a shared `TaskManager`. Pass the same manager
instance into `TaskTool` and the companion tools to keep them in sync.

## Parity matrix vs. the reference implementation

| Capability                             | Reference   | Chimera Task tool |
|----------------------------------------|-------------|-------------------|
| `.claude/agents/*.md` discovery        | yes         | yes (3-source)    |
| Project > user > built-in priority     | yes         | yes               |
| Foreground spawn + return result       | yes         | yes               |
| Background spawn + output file         | yes         | yes (`~/.chimera/tasks/`) |
| Cancellation cascade (parent → child)  | yes         | yes               |
| Reverse cascade (child → parent)       | no          | no (intentional)  |
| Tool allowlist per spawn               | yes         | yes               |
| Model override per spawn               | yes         | yes               |
| Three-tier isolation                   | yes         | yes               |
| Auto-backgrounding (>2 min threshold)  | yes         | not yet (planned) |
| Direct teammate messaging (SendMessage)| experimental| not yet (planned) |
| Worktree-per-spawn                     | manual      | manual            |

## Building a parent that exposes `Task`

```python
from chimera.core.agent import Agent
from chimera.tools.task_tool import TaskTool, TaskManager, TaskListTool, TaskOutputTool

manager = TaskManager()
task_tool = TaskTool(task_manager=manager)
parent = Agent(
    provider=provider,
    tools=[BashTool(), ReadFileTool(), task_tool, TaskListTool(manager), TaskOutputTool(manager)],
    prompt=Prompt.from_string("..."),
)
task_tool.bind_parent(parent)
parent.run("Spawn a reviewer subagent on the staged diff.", env=env)
```

`TaskTool` accepts the parent at construction time, but if your wiring
order makes that awkward, `bind_parent()` defers the link until the
`Agent` exists.
