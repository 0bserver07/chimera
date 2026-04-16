# Quickstart: Build a Coding Agent

This guide walks through building a coding agent with chimera, from a minimal 10-line setup to a fully customized production agent.

**Prerequisites:**

```bash
pip install chimera
export ANTHROPIC_API_KEY="sk-..."
```

## 10-Line Claude Code Clone

```python
import asyncio
from chimera.assembly.coding_agent import CodingAgent

async def main():
    agent = CodingAgent(model="claude-sonnet-4-20250514")
    async for event in agent.run("List all Python files in this project"):
        if hasattr(event.data, "content"):
            print(event.data.content, end="")

asyncio.run(main())
```

This gives you file read/write, bash execution, search, git, sub-agents, permissions, hooks, and transcripts -- all wired up by default.

## Using Presets

Four presets control which features are active:

```python
from chimera.assembly.coding_agent import CodingAgent

# Full-featured (default) -- all 8 phases active
agent = CodingAgent.from_preset("claude_code")

# OpenAI-style code generation -- hooks disabled, 50 turn limit
agent = CodingAgent.from_preset("codex", model="gpt-4o")

# Minimal -- 4 tools (bash, read, write, edit), no permissions or hooks
agent = CodingAgent.from_preset("minimal")

# Read-only exploration -- search, read, list_files only
agent = CodingAgent.from_preset("explore")
```

## Custom API Endpoint

Use any OpenAI-compatible provider by passing a model string to the provider factory:

```python
from chimera.assembly.coding_agent import CodingAgent
from chimera.providers.factory import create_provider

provider = create_provider(
    model="openai/gpt-4o",
    base_url="https://your-endpoint.example.com/v1",
    api_key="your-key",
)

agent = CodingAgent(model="claude-sonnet-4-20250514")  # model arg ignored when overriding
agent.provider = provider
```

## Custom Tools

Add your own tools by subclassing `BaseTool`:

```python
from chimera.core.tool import BaseTool
from chimera.assembly.coding_agent import CodingAgent

class DeployTool(BaseTool):
    name = "deploy"
    description = "Deploy the current project to staging"
    input_schema = {"type": "object", "properties": {
        "environment": {"type": "string", "enum": ["staging", "production"]},
    }, "required": ["environment"]}

    async def run(self, arguments: dict) -> str:
        env = arguments["environment"]
        return f"Deployed to {env} successfully"

# Get default tools and append yours
from chimera.assembly.tool_sets import coding_tools
tools = coding_tools() + [DeployTool()]
agent = CodingAgent(tools_override=tools)
```

## Custom System Prompt

Override the system prompt while keeping all other infrastructure:

```python
import asyncio
from chimera.assembly.coding_agent import CodingAgent

agent = CodingAgent()
agent._system_prompt_text = """\
You are a security auditor. Review code for vulnerabilities.
Focus on: SQL injection, XSS, path traversal, and secrets in code.
Use search and read tools to explore, but never modify files.
"""

async def audit():
    async for event in agent.run("Audit the authentication module"):
        if hasattr(event.data, "content"):
            print(event.data.content, end="")

asyncio.run(audit())
```

## Adding Permissions

Configure which tools are allowed, denied, or require approval:

```python
from chimera.assembly.coding_agent import CodingAgent
from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import PermissionRule, PermissionBehavior, RuleSource

rules = [
    PermissionRule(tool="bash", behavior=PermissionBehavior.ASK, source=RuleSource.USER),
    PermissionRule(tool="write_file", behavior=PermissionBehavior.ALLOW, source=RuleSource.USER),
    PermissionRule(tool="read_file", behavior=PermissionBehavior.ALLOW, source=RuleSource.USER),
]

agent = CodingAgent()
agent._permission_checker = PermissionChecker()
agent._permission_context = PermissionContext(
    mode=PermissionMode.NORMAL,
    allow_rules=[r for r in rules if r.behavior == PermissionBehavior.ALLOW],
    deny_rules=[],
    ask_rules=[r for r in rules if r.behavior == PermissionBehavior.ASK],
)
```

Or place rules in `.chimera/settings.json` at the project root and they will be loaded automatically.

## Adding Hooks

Hooks let you intercept lifecycle events. Define them in `.chimera/settings.json`:

```json
{
  "hooks": {
    "pre_tool_use": [
      {
        "matcher": {"tool_name": "bash"},
        "command": "echo 'About to run bash: {{tool_input.command}}'"
      }
    ],
    "post_tool_use": [
      {
        "matcher": {"tool_name": "write_file"},
        "command": "echo 'File written: {{tool_input.file_path}}'"
      }
    ]
  }
}
```

Or register hooks programmatically:

```python
from chimera.hooks.executor import HookExecutor
from chimera.hooks.events import HookEvent
from chimera.hooks.hook_types import HookDescriptor

executor = HookExecutor()
hook = HookDescriptor(
    event=HookEvent.POST_TOOL_USE,
    command="ruff check --fix {{tool_input.file_path}}",
    matcher={"tool_name": "write_file"},
)

agent = CodingAgent()
agent._hook_executor = executor
agent._hook_matchers = [hook.to_matcher()]
```

## Session Persistence

Transcripts are saved automatically when the `transcripts` preset option is enabled (default for `claude_code` and `codex`). To resume a session:

```python
from chimera.sessions.resume import resume_session
from chimera.sessions.transcript import TranscriptStorage
from pathlib import Path

transcript_dir = Path(".chimera/sessions")
storage = TranscriptStorage(transcript_dir, session_id="abc123")

# Load previous messages
messages = await storage.load()

# Continue the conversation with history
agent = CodingAgent()
# Pass messages to the agent loop via a custom run
```

Session transcripts are stored as JSONL files in `.chimera/sessions/`.

## Sub-Agents

The `AgentTool` lets the main agent spawn isolated sub-agents for complex tasks:

```python
import asyncio
from chimera.assembly.coding_agent import CodingAgent

agent = CodingAgent()

async def main():
    # The model will automatically use the agent tool when needed.
    # You can also define custom sub-agent types:
    async for event in agent.run(
        "Refactor the database module into separate files for "
        "models, queries, and migrations. Use sub-agents for each file."
    ):
        if hasattr(event.data, "content") and event.data.content:
            print(event.data.content, end="")

asyncio.run(main())
```

Sub-agents run with their own `AgentContext`, inheriting tools from the parent but maintaining isolated conversation history. Built-in agent definitions are available in `chimera/core/builtin_agents.py`.
