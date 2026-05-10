---
title: "chimera.core"
description: "Reference for chimera.core — Agent, Context, ReAct loop, LoopConfig, BaseTool, cancellation, file tracker, message queues, operations."
---

`chimera.core` is the heart of the agent: the `Agent` class, the
conversation `Context`, the ReAct loop with all its lifecycle hooks, and
the base classes for tools and prompts.

## Agent + loop

| Symbol | Module | Purpose |
|---|---|---|
| `Agent` | `chimera.core.agent` | Main entry point. `Agent(provider, tools, loop=..., prompt=...)`. Methods: `run(task, env=None)`, `iter_steps(task, env=None)`. |
| `Context` | `chimera.core.context` | Conversation history manager. Tracks messages, tool calls/results, system prompts. |
| `ReAct` | `chimera.core.loop` | The default reason-act-observe loop. Stream-level cancellation, steering drain, 10 lifecycle event emissions. |
| `LoopConfig` | `chimera.core.loop_config` | Single dataclass that funnels every loop-level feature: permissions, detection, events, audit, checkpoints, git workflow, cancellation, message queues, file tracker. `None` everywhere = unchanged behaviour. |
| `ToolExecutor` | `chimera.core.tool_executor` | Shared tool-execution path with permission / event / detection / audit / cancellation / file-tracking hooks. |
| `Prompt` | `chimera.core.prompt` | System-prompt builder with `{var}` substitution. `Prompt.from_string(s)`, `Prompt.from_template(...)`. |

## Tools (`chimera.core.tool`, `chimera.core.tool_group`)

| Symbol | Purpose |
|---|---|
| `BaseTool` | ABC. Define `name`, `description`, `parameters` (JSON Schema), `execute(args, env)`. |
| `@tool(name=, description=, parameters=)` | Decorator that wraps a plain function. |
| `ToolGroup` | Iterable bundle of tools. |
| `DEFAULT_TOOLS` | Standard toolset (read, write, edit, bash, search, ...). |
| `create_default_tools(ops=None)` | Factory; pass a custom `Operations` to swap filesystem / shell / search backends. |

## Cancellation (`chimera.core.cancellation`)

```python
from chimera.core.cancellation import CancellationToken, OperationCancelled, CancellableTool
```

| Symbol | Purpose |
|---|---|
| `CancellationToken` | Thread-safe cooperative cancel. `.cancel()`, `.is_cancelled`. |
| `OperationCancelled` | Exception raised by tools that observe a cancelled token. |
| `CancellableTool` | Mixin that injects token-checking into the tool's `execute`. |

The REPL's Ctrl-C handler triggers `CancellationToken.cancel()`; the
loop drains the streamer and surfaces a prompt for the next user
message.

## File tracking (`chimera.core.file_tracker`)

```python
from chimera.core.file_tracker import FileTracker
```

`FileTracker` records every file read or written by a session and
survives compaction boundaries (so a compacted summary can still cite
which files the agent has already looked at).

## Message queues (`chimera.core.message_queue`)

```python
from chimera.core.message_queue import MessageQueues
```

Two thread-safe queues:

- **Steering queue** — user messages injected mid-turn (consumed at the
  next step boundary).
- **Follow-up queue** — user messages queued for the *next* turn
  (consumed when the current turn completes).

## Operations (`chimera.core.operations`)

Protocol-based filesystem / shell / search abstraction. Implementations
plug into `create_default_tools(ops=...)` so tools can be tested without
a real filesystem or sandboxed for remote execution.

| Protocol | Local impl |
|---|---|
| `ReadOps` | `LocalReadOps` |
| `WriteOps` | `LocalWriteOps` |
| `BashOps` | `LocalBashOps` |
| `SearchOps` | `LocalSearchOps` |

## Specialised loops (`chimera.core.loops`)

| Module | Loop |
|---|---|
| `chimera.core.loops.plan_and_execute` | `PlanAndExecute` — plan-mode → act-mode handoff. |
| `chimera.core.loops.reflexion` | `Reflexion` — self-critique with retry. |
| `chimera.core.loops.tree_of_thought` | `TreeOfThought` — branch-and-prune search. |

## See also

- [`chimera.providers`](/reference/providers/) for the LLM backend.
- [`chimera.tools`](/reference/tools/) for the built-in toolset.
- [`chimera.events`](/reference/events/) for the loop's lifecycle event surface.
- [`chimera.permissions`](/reference/permissions/) for the policy types
  carried on `LoopConfig`.
