# Tools

**Tools** give agents the ability to interact with the world -- reading files, writing code, running shell commands, searching codebases, and more. Chimera ships 13 built-in tools and provides two ways to define custom tools: a class-based approach via `BaseTool` and a decorator-based shortcut via `@tool`.

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

Chimera provides a pre-built `DEFAULT_TOOLS` group with the three essential tools:

```python
from chimera.core.tool_group import DEFAULT_TOOLS

# Contains: ReadFileTool, WriteFileTool, BashTool
agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
```

!!! tip "Use `list()` when passing to Agent"
    `Agent` expects a `list[BaseTool]`, so wrap `DEFAULT_TOOLS` with `list()` to convert from `ToolGroup`.

## Built-in Tools

Chimera ships 13 tools in `chimera.tools`:

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

All built-in tools are importable from `chimera.tools`:

```python
from chimera.tools import (
    ReadFileTool, WriteFileTool, BashTool, EditFileTool,
    SearchTool, ListFilesTool, TestTool, WebFetchTool,
    GitTool, ReplaceInFileTool, DelegateTool, RepoMapTool, VerifyTool,
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

!!! warning "Tool names must be unique"
    If two tools share the same `name`, the agent's tool dispatch will use whichever appears last. Give each tool a distinct name.

## API Reference

- `chimera.core.tool.BaseTool` -- abstract base class for tools
- `chimera.core.tool.tool` -- decorator for function-based tools
- `chimera.core.tool_group.ToolGroup` -- named collection of tools
- `chimera.core.tool_group.DEFAULT_TOOLS` -- pre-built default toolset
- `chimera.types.ToolResult` -- return type from `execute()`
