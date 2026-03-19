---
title: "AGENT_TOOLS"
description: "AGENT_TOOLS"
---

`chimera.AGENT_TOOLS` is an extended `ToolGroup` with 13 tools designed for
interactive agent sessions.  It is the default tool set used by the CLI REPL
(`chimera code`).

## What's Included

| # | Tool | Description |
|---|------|-------------|
| 1 | `read` | Read files from the environment |
| 2 | `write` | Write files to the environment |
| 3 | `edit` | Apply targeted edits to files |
| 4 | `bash` | Execute shell commands |
| 5 | `search` | Search file contents with regex |
| 6 | `list_files` | List directory contents with glob |
| 7 | `test` | Run test commands |
| 8 | `git` | Git operations (status, diff, commit, etc.) |
| 9 | `replace_in_file` | Find-and-replace across files |
| 10 | `image_read` | Read and describe images |
| 11 | `repo_map` | Generate a repository structure map |
| 12 | `think` | Internal reasoning (no side effects) |
| 13 | `todo` | Manage a task list |

## How It Differs from DEFAULT_TOOLS

`DEFAULT_TOOLS` is a minimal 4-tool group for simple agent runs:

| DEFAULT_TOOLS (4) | AGENT_TOOLS (13) |
|---|---|
| read, write, bash, image_read | Everything in DEFAULT_TOOLS + edit, search, list_files, test, git, replace_in_file, repo_map, think, todo |

Use `DEFAULT_TOOLS` for lightweight tasks. Use `AGENT_TOOLS` for full coding
sessions.

## Usage

```python
import chimera

# Use the full agent tool set
agent = chimera.Agent(
    provider=chimera.create_provider(),
    tools=list(chimera.AGENT_TOOLS),
)

result = agent.run("Refactor the utils module.", env=env)
```

You can extend it by adding more tools:

```python
from chimera.tools.dmail import DMailTool

tools = list(chimera.AGENT_TOOLS) + [DMailTool()]
agent = chimera.Agent(provider=provider, tools=tools)
```

Or select a subset:

```python
tools = [t for t in chimera.AGENT_TOOLS if t.name in ("read", "write", "bash", "edit")]
```

## Tools NOT in AGENT_TOOLS

Some tools require special setup and are not included by default:

| Tool | Reason |
|------|--------|
| `AskUserTool` | Requires a user-input callback |
| `DMailTool` | Requires context binding (`ContextAwareTool`) |
| `BrowserTool` | Requires `playwright` (optional dependency) |
| `WebFetchTool` | Requires network access configuration |
| `DelegateTool` | Requires multi-agent setup |

Add these explicitly when needed.

## ToolGroup API

Both `DEFAULT_TOOLS` and `AGENT_TOOLS` are `ToolGroup` instances:

```python
group = chimera.AGENT_TOOLS

len(group)              # 13
group.has("bash")       # True
group.get("bash")       # BashTool instance
group.add(my_tool)      # Add a tool to the group
list(group)             # Iterate over all tools
```

## Import Reference

```python
from chimera import AGENT_TOOLS, DEFAULT_TOOLS
from chimera.core.tool_group import ToolGroup
```
