---
title: "Context-Aware Tools"
description: "Context-Aware Tools"
---

`ContextAwareTool` is a subclass of `BaseTool` for tools that need access to
the agent's conversation `Context`.  The `Agent` class detects context-aware
tools in its tool list and calls `bind_context()` automatically before the loop
starts.

## Why It Exists

Most tools operate on the environment (filesystem, shell, web) and never need
to see the agent's message history.  Some tools, however, need to read or
modify the context itself.  `DMailTool` is the primary example -- it truncates
the message list to rewind to a checkpoint.

Rather than giving every tool a context reference, `ContextAwareTool` provides
an opt-in pattern: only tools that subclass it receive the binding.

## How It Works

```python
from chimera.core.tool import ContextAwareTool
from chimera.core.context import Context
from chimera.types import ToolResult

class MyContextTool(ContextAwareTool):
    name = "my_tool"
    description = "A tool that reads the conversation length."
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        # self._context is set by bind_context()
        count = len(self._context.messages)
        return ToolResult(output=f"Context has {count} messages.")
```

When you pass this tool to an Agent:

```python
import chimera

my_tool = MyContextTool()
agent = chimera.Agent(
    provider=chimera.create_provider(),
    tools=list(chimera.AGENT_TOOLS) + [my_tool],
)

# Agent calls my_tool.bind_context(context) internally
result = agent.run("How many messages are in context?", env=env)
```

## API

`ContextAwareTool` adds a single method on top of `BaseTool`:

```python
class ContextAwareTool(BaseTool):
    def bind_context(self, context: Context) -> None:
        """Bind the agent's context to this tool. Called by Agent before the loop."""
        self._context = context
```

After binding, `self._context` is a `Context` instance with access to
`messages`, `add()`, `system_prompt`, and other context management methods.

## Currently Used By

- **`DMailTool`** -- uses the context to create checkpoints (save the message
  index) and rewind (truncate messages, inject a summary).

## Creating Your Own

1. Subclass `ContextAwareTool` instead of `BaseTool`
2. Use `self._context` in your `execute()` method
3. Add the tool to the agent's tool list -- binding happens automatically

```python
class ContextSummaryTool(ContextAwareTool):
    name = "context_summary"
    description = "Summarize the current context state."
    parameters = {"type": "object", "properties": {}}

    def execute(self, args, env=None):
        msgs = self._context.messages
        roles = [m.role for m in msgs]
        return ToolResult(
            output=f"{len(msgs)} messages: {roles.count('user')} user, "
                   f"{roles.count('assistant')} assistant"
        )
```

## Import Reference

```python
from chimera.core.tool import BaseTool, ContextAwareTool
from chimera.core.context import Context
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/core/tool.py` -- verified against current source at wave-17.
