---
title: "chimera.core"
description: "chimera.core"
---

::: chimera.core.agent

::: chimera.core.context

::: chimera.core.loop

::: chimera.core.loop_config

::: chimera.core.tool

::: chimera.core.tool_executor

::: chimera.core.prompt

::: chimera.core.tool_group

## New modules (pi-mono)

The following core modules were added as part of the pi-mono adoption:

| Module | Key exports | Description |
|--------|-------------|-------------|
| `cancellation.py` | `CancellationToken`, `OperationCancelled`, `CancellableTool` | Cooperative cancellation: tools check a shared token and raise `OperationCancelled` when signalled |
| `file_tracker.py` | `FileTracker` | Tracks which files have been read or modified during a session; used by `FileAwareCompaction` |
| `message_queue.py` | `MessageQueues` | Thread-safe queue for injecting steering messages and queued user turns into a running loop |
| `operations.py` | `ReadOps`, `WriteOps`, `BashOps`, `SearchOps` | Abstract operation interfaces with local implementations; makes tool logic testable without a real filesystem |

::: chimera.core.cancellation

::: chimera.core.file_tracker

::: chimera.core.message_queue

::: chimera.core.operations
