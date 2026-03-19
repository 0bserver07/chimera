---
title: "D-Mail"
description: "D-Mail"
---

`chimera.tools.dmail` provides `DMailTool`, a context-rewind mechanism inspired
by Steins;Gate.  The agent creates checkpoints during a conversation, then
"sends a D-Mail" to rewind context to an earlier checkpoint with a summary of
what was learned -- allowing it to continue from that point without the clutter
of intermediate exploration.

This is context compaction disguised as time travel.

## Quick Start

```python
import chimera

dmail = chimera.DMailTool()
tools = list(chimera.AGENT_TOOLS) + [dmail]

agent = chimera.Agent(
    provider=chimera.create_provider(),
    tools=tools,
    loop=chimera.ReAct(max_steps=10),
    prompt=chimera.Prompt.from_string(
        "You have a 'dmail' tool. Use action='checkpoint' to save your "
        "position, then action='send' with a summary to rewind when you've "
        "gathered enough information."
    ),
)

result = agent.run("Explore the project, then summarize your findings.", env=env)
print(f"Checkpoints created: {dmail.checkpoint_count}")
```

## How It Works

DMailTool is a `ContextAwareTool` -- it needs a reference to the agent's
`Context` so it can truncate the message history.  The `Agent` class calls
`bind_context()` automatically before the loop starts.

### Action: `checkpoint`

Saves the current position in context (just a message index -- no filesystem
snapshot).

```
tool_call: dmail(action="checkpoint")
# -> "Checkpoint 0 created at message index 5."
```

### Action: `send`

Rewinds context to the given checkpoint, removes all messages after it,
and appends a `[D-Mail from future self]` user message with the summary.

```
tool_call: dmail(action="send", checkpoint_id=0, message="The project uses Flask + Postgres on port 5432.")
# -> "D-Mail sent. Context rewound to checkpoint 0."
```

After a send:

- All messages after the checkpoint are removed
- All checkpoints created after the target are removed
- The summary is injected as a user message
- The agent continues from that point with a clean context

## Parameters

```json
{
  "action": "checkpoint | send",
  "checkpoint_id": 0,
  "message": "Summary for your past self"
}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `action` | Always | `"checkpoint"` to save or `"send"` to rewind |
| `checkpoint_id` | For `send` | Which checkpoint to rewind to |
| `message` | For `send` | Summary message for your past self |

## Why DMailTool Is Not in AGENT_TOOLS

DMailTool requires context binding, which the Agent does automatically when it
finds a `ContextAwareTool` in the tool list.  Because it is a specialized tool,
it must be added explicitly:

```python
dmail = chimera.DMailTool()
tools = list(chimera.AGENT_TOOLS) + [dmail]
```

## Import Reference

```python
from chimera.tools.dmail import DMailTool
from chimera.core.tool import ContextAwareTool  # base class
```
