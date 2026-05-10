---
title: "chimera.types"
description: "Reference for chimera.types — shared dataclasses passed across providers, tools, and loops."
---

`chimera.types` defines the dataclasses that flow across the
provider / loop / tool boundary. Importing from a single module avoids
circular imports between layers.

## Tool I/O

| Type | Fields | Purpose |
|---|---|---|
| `ToolCall` | `id`, `name`, `arguments` (dict) | One LLM-emitted tool invocation. |
| `ToolResult` | `output` (str), `error` (str \| None) | Return value of `BaseTool.execute()`. `success` property is `error is None`. |
| `CommandResult` | `stdout`, `stderr`, `returncode` | Result of a shell command. |
| `TestResult` | `passed`, `failed`, `skipped`, `output` | Test-runner result. |

## Messages

| Type | Fields |
|---|---|
| `Message` | `role` (`"system"` \| `"user"` \| `"assistant"` \| `"tool"`), `content`, `tool_calls`, `tool_call_id` |
| `ContentBlock` | base class for typed content |
| `TextContent` | `text` |
| `ImageContent` | `media_type`, `data` |

## Step / agent results

| Type | Fields |
|---|---|
| `StepResult` | `tool_calls`, `tool_results`, `text`, `usage`, `cost` |
| `AgentResult` | `output`, `success`, `steps`, `tool_calls`, `cost`, `error`, `messages` |

## File diffs

| Type | Fields |
|---|---|
| `ChangeType` | enum: `CREATE`, `MODIFY`, `DELETE` |
| `FileChange` | `path`, `change_type`, `before`, `after` |

## Approvals

`PendingApproval` (`chimera.types.PendingApproval`) wraps a tool call
that needs human confirmation. Carries the `tool_name`, `args`, and a
callable to resolve the decision.

## Import shortcut

Most user code does not import `chimera.types` directly — common types
are re-exported from the top-level `chimera`:

```python
from chimera import Agent, ToolResult, AgentResult
```

## See also

- [`chimera.core`](/reference/core/) for the loop that produces `StepResult`.
- [`chimera.events`](/reference/events/) for the event types emitted
  alongside the dataclasses above.
