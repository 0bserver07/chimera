---
title: "Core"
description: "Core"
---

`chimera.core` is the foundational layer: Agent, ReAct loop, Context,
Tools, LoopConfig, and the operations protocols. Every higher layer
(synthesis, evaluation, sessions, workflows, the seven codename CLIs)
ultimately calls into `chimera.core`.

## Quick Start

```python
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider

provider = create_provider(model="claude-sonnet-4-6")
agent = Agent(provider=provider, name="coder")
result = agent.run("Write a hello-world script.", env=None)
print(result.output)
```

## Key Classes

| Class | Module | Description |
|-------|--------|-------------|
| `Agent` | `chimera.core.agent` | Agent = Provider + Tools + Loop + Prompt. Single entry point: `run()`, `run_async()`, `run_streaming()`, `iter_steps()`. |
| `Context` | `chimera.core.context` | Conversation history manager. Owns the message list, system prompt, and tool-call cursor. |
| `ReAct` | `chimera.core.loop` | The reason-act-observe loop. Stream-level cancellation, steering drain, 10 lifecycle event emissions. |
| `LoopConfig` | `chimera.core.loop_config` | Single dataclass funnelling all loop-level features: permissions, detection, events, audit, checkpoints, git workflow, cancellation, message_queues, file_tracker, compaction, streaming, instruction_layer. |
| `Prompt` | `chimera.core.prompt` | System prompt with variable substitution. |
| `BaseTool` | `chimera.core.tool` | Tool ABC with `@tool` decorator for quick functional tools. `ContextAwareTool` mixin gives a tool the running `Context`. |
| `ToolGroup` | `chimera.core.tool_group` | Curated bundle of tools. `DEFAULT_TOOLS`, `AGENT_TOOLS`, `BUILD_TOOLS` presets. `create_default_tools(ops=...)` factory swaps the operations layer. |
| `CancellationToken` | `chimera.core.cancellation` | Thread-safe cooperative cancel. `OperationCancelled` exception + `CancellableTool` mixin. |
| `FileTracker` | `chimera.core.file_tracker` | Tracks files read / modified across compaction boundaries. |
| `MessageQueues` | `chimera.core.message_queue` | Thread-safe steering + follow-up queues for mid-turn input. |
| `LoopDetector` | `chimera.core.loop_detection` | Exact-repeat + pattern-cycle detector. |
| `InstructionLayer` | `chimera.core.instruction` | Layered instruction stack (CLAUDE.md, AGENTS.md, .chimera/rules.md, skills, tool docstrings). |

## Operations protocols

`chimera.core.operations` defines four protocols that decouple the
agent's tools from the underlying environment. The default
implementation (`LocalReadOps`, `LocalWriteOps`, `LocalBashOps`,
`LocalSearchOps`) is the local filesystem; alternative implementations
drive Docker, remote, cloud, and persistent-shell environments.

| Protocol | Methods |
|---|---|
| `ReadOps` | `read(path) -> str`, `exists(path) -> bool`, `list_dir(path) -> list[str]` |
| `WriteOps` | `write(path, content)`, `delete(path)`, `mkdir(path)` |
| `BashOps` | `run(command, *, timeout, cwd, env) -> ProcessResult` |
| `SearchOps` | `search(pattern, *, path, glob, case_sensitive) -> list[Match]` |

Swap the operations layer to retarget the entire tool surface at a
different backend without rewriting tools:

```python
from chimera.core.tool_group import create_default_tools
from chimera.env.docker import DockerEnvironment

env = DockerEnvironment(image="python:3.11")
tools = create_default_tools(ops=env.operations())
agent = Agent(provider=provider, tools=tools)
```

## Built-in loops

| Loop | Module | Pattern |
|---|---|---|
| `ReAct` | `chimera.core.loop` | Default reason-act-observe (interleaved tool calls). |
| `PlanAndExecute` | `chimera.core.loops` | Plan up-front, execute in order, replan on failure. |
| `Reflexion` | `chimera.core.loops` | ReAct + post-turn self-critique that feeds back into the next attempt. |
| `TreeOfThought` | `chimera.core.loops` | Explore N candidate next-steps in parallel, vote, expand the winner. |

All loops accept the same `LoopConfig`, so swapping a loop is a
one-line change.

## LoopConfig — the single dataclass

Every loop-level feature funnels through one dataclass injected into
the loop constructor. When `loop_config=None` (the default), behavior
is unchanged.

```python
from chimera.core.loop_config import LoopConfig
from chimera.core.cancellation import CancellationToken
from chimera.core.message_queue import MessageQueues
from chimera.core.file_tracker import FileTracker
from chimera.events import EventBus
from chimera.permissions import AllowList, AuditLog
from chimera.compaction import CompositeCompaction, PruneCompaction

config = LoopConfig(
    permissions=AllowList({"read", "search"}),
    audit_log=AuditLog(),
    events=EventBus(),
    cancellation=CancellationToken(),
    message_queues=MessageQueues(),
    file_tracker=FileTracker(),
    compaction=CompositeCompaction([PruneCompaction()]),
)

loop = ReAct(provider=provider, tools=tools, loop_config=config)
result = loop.run("...", context, env=None)
```

## Three-tier API

Every core feature offers three layers of access:

1. **One-liner convenience** — `Agent(provider=provider).run("...")`
   uses every default and Just Works.
2. **Developer configuration** — `LoopConfig(permissions=...,
   compaction=...)` lets you swap concrete implementations without
   subclassing.
3. **Framework-author subclassing** — every dataclass / ABC is
   designed for extension. Subclass `BaseTool`, `CompactionStrategy`,
   `Provider`, `Loop`; register through the appropriate registry.

## Integration

- **Sessions**: `chimera.sessions.Session` wraps an `Agent` for
  multi-turn conversations with persistence, forking, and
  compaction.
- **Workflows**: `CIFixWorkflow`, `ReviewOrchestrator`, `Researcher`,
  `MigrationPlanner`, `DocGenerator`, `TestGenerator` all delegate to
  `Agent.run()` after composing a task-specific prompt.
- **Composition**: `Pipeline`, `Ensemble`, `Supervisor`, and
  `RoleBasedTeam` (`chimera.composition`) chain multiple `Agent`s.
- **CLIs**: every codename harness (mink, otter, ferret, weasel,
  shrew, stoat, badger) wires `Agent` + `Session` + `LoopConfig` +
  `LocalEnvironment` (or `DockerEnvironment`) together.

## Import Reference

```python
from chimera.core.agent import Agent
from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.cancellation import CancellationToken, OperationCancelled
from chimera.core.message_queue import MessageQueues
from chimera.core.file_tracker import FileTracker
from chimera.core.tool import BaseTool, ContextAwareTool, tool
from chimera.core.tool_group import (
    AGENT_TOOLS,
    BUILD_TOOLS,
    DEFAULT_TOOLS,
    ToolGroup,
    create_default_tools,
)
from chimera.core.operations import (
    BashOps,
    ReadOps,
    SearchOps,
    WriteOps,
    LocalBashOps,
    LocalReadOps,
    LocalSearchOps,
    LocalWriteOps,
)
from chimera.core.loops import PlanAndExecute, Reflexion, TreeOfThought
```
