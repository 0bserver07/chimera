---
title: "Add a Custom Tool"
description: "Add a Custom Tool"
---

Chimera agents act through tools.  This guide shows two ways to create your
own: the **`@tool` decorator** for quick one-off functions, and the
**`BaseTool` subclass** for full control.

---

## Quick Way -- `@tool` Decorator

The `tool` decorator wraps a plain function and returns a `BaseTool` instance
ready to hand to an agent.  You supply the name, description, and a JSON
Schema for the parameters.

```python
from chimera import tool
from chimera.types import ToolResult

@tool(
    name="count_lines",
    description="Count the number of lines in a file.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to count.",
            },
        },
        "required": ["file_path"],
    },
)
def count_lines(args, env):
    """args is a dict matching the JSON Schema above."""
    path = args["file_path"]
    try:
        with open(path) as f:
            n = len(f.readlines())
        return ToolResult(output=f"{path} has {n} lines")
    except FileNotFoundError:
        return ToolResult(output="", error=f"File not found: {path}")
```

Use it in an agent:

```python
from chimera import Agent, DEFAULT_TOOLS, create_provider

provider = create_provider(model="claude-sonnet-4-20250514")
agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS) + [count_lines],
)

result = agent.run("How many lines does setup.py have?", env=None)
print(result.output)
```

---

## Full Control -- `BaseTool` Subclass

For tools that need internal state, complex validation, or custom schema
generation, subclass `BaseTool` directly.

```python
from typing import Any
from chimera import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class HttpGetTool(BaseTool):
    """Fetch the text content of a URL."""

    name = "http_get"
    description = "Fetch the text content of a URL via HTTP GET."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds.",
                "default": 10,
            },
        },
        "required": ["url"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        import urllib.request
        import urllib.error

        url = args["url"]
        timeout = args.get("timeout", 10)

        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return ToolResult(output=body[:4000])  # truncate large pages
        except urllib.error.URLError as exc:
            return ToolResult(output="", error=f"Request failed: {exc}")
```

### Key methods

| Method | Purpose |
|---|---|
| `execute(args, env)` | Run the tool. Return a `ToolResult`. |
| `to_anthropic_schema()` | Convert to Anthropic tool-use format (auto-generated from `name`, `description`, `parameters`). |
| `to_openai_schema()` | Convert to OpenAI function-calling format (auto-generated). |

The base class builds both schemas from the `name`, `description`, and
`parameters` attributes, so you only override them if you need non-standard
formatting.

---

## Register in a ToolGroup

Group related tools into a `ToolGroup` for reuse across agents.

```python
from chimera import ToolGroup

web_tools = ToolGroup("web", [HttpGetTool()])

agent = Agent(
    provider=provider,
    tools=list(web_tools),  # ToolGroup is iterable
)
```

You can also add tools to an existing group:

```python
from chimera import DEFAULT_TOOLS

DEFAULT_TOOLS.add(HttpGetTool())
```

---

## Testing a Custom Tool

Tools are plain Python objects -- test them without spinning up an agent.

```python
def test_count_lines(tmp_path):
    # Create a temp file with 5 lines
    p = tmp_path / "sample.txt"
    p.write_text("a\nb\nc\nd\ne\n")

    result = count_lines.execute({"file_path": str(p)}, env=None)
    assert result.success
    assert "5 lines" in result.output


def test_count_lines_missing_file():
    result = count_lines.execute({"file_path": "/no/such/file"}, env=None)
    assert not result.success
    assert "File not found" in result.error
```

Run with pytest:

```bash
pytest test_tools.py -v
```

---

## Putting It All Together

```python
"""custom_tool_demo.py"""

from typing import Any
from chimera import Agent, BaseTool, DEFAULT_TOOLS, Prompt, create_provider
from chimera.env.base import Environment
from chimera.types import ToolResult


class WordCountTool(BaseTool):
    name = "word_count"
    description = "Count words in the provided text."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to count."},
        },
        "required": ["text"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        words = len(args["text"].split())
        return ToolResult(output=f"Word count: {words}")


provider = create_provider(model="claude-sonnet-4-20250514")
agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS) + [WordCountTool()],
    prompt=Prompt.from_string("You are a writing assistant with word-count abilities."),
)

result = agent.run("Count the words in: 'The quick brown fox jumps.'", env=None)
print(result.output)
```

---

## Next Steps

- [Build a Coding Agent](/build-a-coding-agent/) -- use tools inside a full agent.
- [Configure Permissions](/configure-permissions/) -- control which tools the agent may call.
