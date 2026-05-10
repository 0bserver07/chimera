---
title: "Tools"
description: "Tools"
---

**Tools** give agents the ability to interact with the world -- reading files, writing code, running shell commands, searching codebases, and more. Chimera ships 20 built-in tools and provides two ways to define custom tools: a class-based approach via `BaseTool` and a decorator-based shortcut via `@tool`.

## The BaseTool ABC

Every tool in Chimera extends `BaseTool`, defined in `chimera.core.tool`:

```python
class BaseTool(ABC):
    name: str                          # Tool name (used in LLM function calling)
    description: str                   # Description shown to the model
    parameters: dict[str, Any]         # JSON Schema for arguments
    requires_approval: bool = False    # Whether the tool needs human approval

    @abstractmethod
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the tool with given arguments."""

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Convert to Anthropic tool use format."""
```

Key points:

- `execute()` receives the parsed arguments and an optional `Environment`, and returns a `ToolResult(output, error, metadata)`.
- `to_openai_schema()` and `to_anthropic_schema()` generate the schema format each provider expects. You only need to define `parameters` once as JSON Schema.
- `requires_approval` can flag dangerous tools for human-in-the-loop confirmation (when used with a `PermissionPolicy` in `LoopConfig`).

## The `@tool` Decorator

For simple tools, skip the class and use the `@tool` decorator:

```python
from chimera.core.tool import tool

@tool(
    name="word_count",
    description="Count words in a file",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file"},
        },
        "required": ["path"],
    },
)
def word_count(args, env):
    content = env.read_file(args["path"])
    count = len(content.split())
    return ToolResult(output=f"{count} words")
```

The decorator wraps your function in a `_FunctionTool` instance that implements `BaseTool`. The function signature is always `(args: dict, env: Environment | None) -> ToolResult`.

## ToolGroup

`ToolGroup` organizes tools into named collections, like a preset toolkit:

```python
from chimera.core.tool_group import ToolGroup
from chimera.tools import ReadFileTool, WriteFileTool, BashTool, SearchTool

coding_tools = ToolGroup("coding", [
    ReadFileTool(),
    WriteFileTool(),
    BashTool(),
    SearchTool(),
])

# Iterate, look up by name, add tools dynamically
coding_tools.has("bash")       # True
coding_tools.get("bash")       # BashTool instance
coding_tools.add(my_tool)      # Add a custom tool
len(coding_tools)              # Number of tools
list(coding_tools)             # Iterate over tools
```

### DEFAULT_TOOLS

Chimera provides a pre-built `DEFAULT_TOOLS` group with the essential tools:

```python
from chimera.core.tool_group import DEFAULT_TOOLS

# Contains: ReadFileTool, WriteFileTool, BashTool, ImageReadTool
agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
```

:::tip[Use `list()` when passing to Agent]
`Agent` expects a `list[BaseTool]`, so wrap `DEFAULT_TOOLS` with `list()` to convert from `ToolGroup`.
:::### AGENT_TOOLS

For interactive sessions (like the REPL), Chimera provides `AGENT_TOOLS` -- a 23-tool preset that extends `DEFAULT_TOOLS` with the full coding-agent surface: edit, search, list_files, test, git, replace_in_file, repo_map, think, todo, verify, web_search, plus the W13-added structured-edit and lifecycle tools (apply_patch, write_guard, notebook_edit, enter_worktree, exit_worktree, cron_create, cron_list, cron_delete):

```python
from chimera.core.tool_group import AGENT_TOOLS

# Contains:
#   ReadFileTool, WriteFileTool, EditFileTool, BashTool, SearchTool,
#   ListFilesTool, TestTool, GitTool, ReplaceInFileTool, ImageReadTool,
#   RepoMapTool, ThinkTool, TodoTool(persist=True), VerifyTool,
#   WebSearchTool, ApplyPatchTool, WriteGuardTool, NotebookEditTool,
#   EnterWorktreeTool, ExitWorktreeTool, CronCreateTool,
#   CronListTool, CronDeleteTool
agent = Agent(provider=provider, tools=list(AGENT_TOOLS))
```

The REPL (`chimera code`) and every coding-agent CLI (`mink`, `otter`,
`ferret`, `weasel`, `shrew`, `stoat`, `badger`) use `AGENT_TOOLS` by
default. `TodoTool` is constructed with `persist=True` so todo state
survives `/resume`; bare `TodoTool()` instances default to in-memory.

#### W13 additions

Eight tools were added to `AGENT_TOOLS` in W13 to close gaps that
previously left the bare REPL (and the per-CLI shims) without a default
structured-edit / Jupyter / git-worktree / scheduled-task surface:

| Tool | Class | Purpose |
|------|-------|---------|
| `apply_patch` | `ApplyPatchTool` | Multi-file structured edits with fuzzy matching (Codex / GPT-family format) |
| `write_guard` | `WriteGuardTool` | Surfaces the `write_file` (create-only) vs `edit_file` (modify-existing) invariant |
| `notebook_edit` | `NotebookEditTool` | Edit `.ipynb` cells without breaking notebook JSON |
| `enter_worktree` | `EnterWorktreeTool` | Spawn a git worktree for parallel exploration |
| `exit_worktree` | `ExitWorktreeTool` | Tear down an enter_worktree branch |
| `cron_create` | `CronCreateTool` | Schedule a recurring or one-shot agent task |
| `cron_list` | `CronListTool` | List scheduled tasks |
| `cron_delete` | `CronDeleteTool` | Cancel a scheduled task |

## Built-in Tools

Chimera ships 20 tools in `chimera.tools`:

| Tool | Class | Description |
|------|-------|-------------|
| `read_file` | `ReadFileTool` | Read the contents of a file |
| `write_file` | `WriteFileTool` | Write content to a file |
| `bash` | `BashTool` | Execute a shell command |
| `edit_file` | `EditFileTool` | Make targeted edits to a file |
| `search` | `SearchTool` | Search for patterns across files |
| `list_files` | `ListFilesTool` | List files matching a glob pattern |
| `test` | `TestTool` | Run the test suite |
| `web_fetch` | `WebFetchTool` | Fetch content from a URL |
| `git` | `GitTool` | Run git commands |
| `replace_in_file` | `ReplaceInFileTool` | Find and replace text in a file |
| `delegate` | `DelegateTool` | Delegate a subtask to another agent |
| `repo_map` | `RepoMapTool` | Generate a structural map of the repository |
| `verify` | `VerifyTool` | Verify code correctness |
| `image_read` | `ImageReadTool` | Read and describe image files |
| `browser` | `BrowserTool` | Interact with web pages via a browser |
| `import_graph` | `ImportGraphTool` | Analyze module import dependencies |
| `think` | `ThinkTool` | Scratchpad for agent reasoning (no external action) |
| `ask_user` | `AskUserTool` | Pause and ask the user a question |
| `todo` | `TodoTool` | Manage a task checklist during agent execution |
| `dmail` | `DmailTool` | Send structured messages between agents |

All built-in tools are importable from `chimera.tools`:

```python
from chimera.tools import (
    ReadFileTool, WriteFileTool, BashTool, EditFileTool,
    SearchTool, ListFilesTool, TestTool, WebFetchTool,
    GitTool, ReplaceInFileTool, DelegateTool, RepoMapTool, VerifyTool,
    ImageReadTool, BrowserTool, ImportGraphTool, ThinkTool, AskUserTool,
    TodoTool, DmailTool,
)
```

Pre-instantiated singletons are also available:

```python
from chimera.tools import read_file, write_file, bash, edit_file, search
```

## Code Example: Class-Based Tool

```python
from chimera.core.tool import BaseTool
from chimera.types import ToolResult

class LintTool(BaseTool):
    name = "lint"
    description = "Run ruff linter on a Python file"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the Python file"},
        },
        "required": ["path"],
    }

    def execute(self, args, env):
        result = env.run_command(f"ruff check {args['path']}")
        if result.success:
            return ToolResult(output="No lint errors found.")
        return ToolResult(output=result.stdout, error=result.stderr)
```

## Code Example: Composing a Custom Toolset

```python
from chimera.core.agent import Agent
from chimera.core.tool_group import ToolGroup, DEFAULT_TOOLS
from chimera.providers.factory import create_provider

# Start with defaults, add custom tools
tools = list(DEFAULT_TOOLS)
tools.append(LintTool())
tools.append(word_count)  # From @tool decorator example above

agent = Agent(
    provider=create_provider(model="claude-sonnet-4-20250514"),
    tools=tools,
)
```

:::caution[Tool names must be unique]
If two tools share the same `name`, the agent's tool dispatch will use whichever appears last. Give each tool a distinct name.
:::## API Reference

- `chimera.core.tool.BaseTool` -- abstract base class for tools
- `chimera.core.tool.tool` -- decorator for function-based tools
- `chimera.core.tool_group.ToolGroup` -- named collection of tools
- `chimera.core.tool_group.DEFAULT_TOOLS` -- pre-built default toolset (4 tools)
- `chimera.core.tool_group.AGENT_TOOLS` -- extended toolset for interactive sessions (23 tools)
- `chimera.core.tool_group.create_default_tools(read_ops, write_ops, bash_ops, search_ops)` -- factory for ops-backed default tools
- `chimera.types.ToolResult` -- return type from `execute()`
