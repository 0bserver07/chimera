---
title: "chimera.tools"
description: "Reference for chimera.tools — built-in tools, BaseTool, and the @tool decorator."
---

`chimera.tools` ships ~30 built-in tools plus the primitives for adding
your own. Tools are plain Python objects: they expose a JSON-schema for
their parameters and an `execute(args, env)` method.

For tutorial-style guidance, see [Add a Custom Tool](/add-custom-tool/).
This page is the canonical export list.

## Authoring tools

```python
from chimera import tool, BaseTool, ToolGroup, DEFAULT_TOOLS
from chimera.core.tool import @tool  # alternative import path
```

| Symbol | Module | Purpose |
|---|---|---|
| `BaseTool` | `chimera.core.tool` | ABC. Define `name`, `description`, `parameters` (JSON Schema), and `execute(args, env)`. Auto-generates Anthropic / OpenAI schemas. |
| `@tool(...)` | `chimera.core.tool` | Decorator that wraps a plain function and returns a ready-to-use tool. |
| `ToolGroup` | `chimera.core.tool_group` | Iterable bundle of tools. Use `DEFAULT_TOOLS` or build your own. |
| `DEFAULT_TOOLS` | `chimera.core.tool_group` | The standard toolset (read, write, edit, bash, search, ...). Iterable, so `list(DEFAULT_TOOLS)` works. |
| `create_default_tools(ops=...)` | `chimera.core.tool_group` | Factory variant that lets you swap the underlying `Operations` (test-time injection). |
| `CancellableTool` | `chimera.core.cancellation` | Mixin for tools that respect `CancellationToken`. |

## Built-in tools

The following tool modules ship under `chimera/tools/`. Most expose a
single class or factory whose `name` matches the filename:

### File system

| Tool | Module | Notes |
|---|---|---|
| `read` | `chimera.tools.read` | Read file contents. |
| `write` | `chimera.tools.write` | Create / overwrite files. |
| `edit` | `chimera.tools.edit` | Anchored substitution. |
| `multi_edit` | `chimera.tools.multi_edit` | Batched edits in a single call. |
| `replace_in_file` | `chimera.tools.replace_in_file` | Search-and-replace with regex. |
| `apply_patch` | `chimera.tools.apply_patch` | Apply unified-diff patches. |
| `list_files` | `chimera.tools.list_files` | Directory listing. |
| `cached_read` | `chimera.tools.cached_read` | Read with file-tracker caching. |
| `notebook_edit` | `chimera.tools.notebook_edit` | Edit Jupyter notebooks. |

### Shell / process

| Tool | Module |
|---|---|
| `bash` | `chimera.tools.bash` |
| `powershell` | `chimera.tools.powershell` |
| `ipython` | `chimera.tools.ipython` |
| `test` | `chimera.tools.test` |

### Search / navigation

| Tool | Module |
|---|---|
| `search` | `chimera.tools.search` |
| `repo_map` | `chimera.tools.repo_map` |
| `import_graph` | `chimera.tools.import_graph` |
| `definition_lookup` | `chimera.tools.definition_lookup` |
| `codebase_index` | `chimera.tools.codebase_index` |
| `embedding_index` | `chimera.tools.embedding_index` |
| `grounded_search` | `chimera.tools.grounded_search` |
| `tool_search` | `chimera.tools.tool_search` |

### Git / VCS

| Tool | Module |
|---|---|
| `git` | `chimera.tools.git` |
| `worktree_tool` | `chimera.tools.worktree_tool` |
| `rollback` | `chimera.tools.rollback` |

### Web / external

| Tool | Module |
|---|---|
| `web_fetch` | `chimera.tools.web_fetch` |
| `web_search` | `chimera.tools.web_search` |
| `browser` | `chimera.tools.browser` |
| `image_read` | `chimera.tools.image_read` |

### Agent control

| Tool | Module |
|---|---|
| `delegate` | `chimera.tools.delegate` |
| `agent_tool` | `chimera.tools.agent_tool` |
| `task_tool` | `chimera.tools.task_tool` |
| `task_tools` | `chimera.tools.task_tools` |
| `plan_mode` | `chimera.tools.plan_mode` |
| `skill_tool` | `chimera.tools.skill_tool` |
| `ask_user` | `chimera.tools.ask_user` |
| `send_message` | `chimera.tools.send_message` |
| `dmail` | `chimera.tools.dmail` |
| `todo` | `chimera.tools.todo` |
| `think` | `chimera.tools.think` |

### Verification / quality gates

| Tool | Module |
|---|---|
| `verify` | `chimera.tools.verify` |
| `write_guard` | `chimera.tools.write_guard` |

### Misc

| Tool | Module |
|---|---|
| `batch` | `chimera.tools.batch` |
| `compiled_function_tool` | `chimera.tools.compiled_function_tool` |
| `cron_tools` | `chimera.tools.cron_tools` |
| `config_tool` | `chimera.tools.config_tool` |
| `strategies` | `chimera.tools.strategies` |
| `relative_indent` | `chimera.tools.relative_indent` |
| `edit_formats` | `chimera.tools.edit_formats` |

## Tool result type

`chimera.types.ToolResult` is the return type of every `execute()` call:

```python
@dataclass
class ToolResult:
    output: str
    error: str | None = None

    @property
    def success(self) -> bool: ...
```

## See also

- [Add a Custom Tool](/add-custom-tool/) for the decorator + subclass workflow.
- [`chimera.core`](/reference/core/) for `BaseTool`, `ToolGroup`, and the
  `Operations` injection seam.
- [Configure Permissions](/configure-permissions/) to allow / deny / ask
  on individual tool calls.
