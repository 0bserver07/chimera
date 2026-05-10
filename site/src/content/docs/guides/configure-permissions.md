---
title: "Configure Permissions"
description: "Configure Permissions"
---

Agents can read files, write code, and run shell commands.  Permissions let
you control exactly which tool invocations are allowed, denied, or require
human approval before proceeding.

---

## 1. Why Permissions Matter

Without permissions, an agent can execute any tool it has access to -- including
destructive commands like `rm -rf` or `sudo`.  Chimera's permission system
lets you put guardrails in place so that:

- **Read-only operations** run freely.
- **Dangerous operations** are blocked outright.
- **Everything else** pauses for human approval.

---

## 2. Using Presets

Chimera ships with ready-made permission policies for common scenarios.

### AutoApprove

Allow everything.  Useful for fully autonomous, sandboxed runs.

```python
from chimera import AutoApprove

policy = AutoApprove()
```

### ReadOnly

Allow `read_file`, `search`, `list_files`, and `repo_map`.  Deny everything
else.

```python
from chimera import ReadOnly

policy = ReadOnly()
```

### Interactive

Allow reads automatically.  Ask for approval on writes, bash, git, and other
mutating tools.

```python
from chimera import Interactive

policy = Interactive()
```

---

## 2a. The Five `--permission-mode` Choices

The `ferret`, `badger`, and `mink` CLIs all expose a single
`--permission-mode` flag with five canonical spellings, ordered from least
to most permissive:

| Mode | Behaviour | Backing policy |
|---|---|---|
| `read-only` | Only `read_file`, `search`, `list_files`, `repo_map` allowed; everything else denied. | `ReadOnly` |
| `suggest` | Reads auto-approve; every write/edit/bash/git call asks for explicit confirmation. | `Interactive` |
| `auto` | Reads + simple file edits auto-approve; bash/git/destructive ops still ask. | `AutoEditPolicy` |
| `yolo` | Every tool call auto-approves. Sandbox use only. | `AutoApprove` |
| `strict` | Every tool call (including reads) asks for confirmation. | `AlwaysAskPolicy` |

Resolve a mode string to a policy programmatically:

```python
from chimera.permissions.modes import ApprovalMode, parse_mode, policy_for_mode

mode = parse_mode("auto")          # ApprovalMode.AUTO
policy = policy_for_mode(mode)     # AutoEditPolicy()

# Legacy spellings round-trip too.
policy_for_mode("acceptEdits")     # → AutoEditPolicy() (was AUTO under mink's old flag)
policy_for_mode("plan")            # → ReadOnly() (was the legacy "plan" alias)
```

`parse_mode()` accepts the five canonical strings, the underscore variants
(`read_only`), the legacy ferret `--approval` values (`full` → `yolo`), and
the legacy mink `--permission-mode` choices (`default`, `acceptEdits`,
`bypassPermissions`, `plan`).

From the CLI:

```bash
chimera code --permission-mode auto
chimera code --permission-mode strict   # everything asks
chimera code --permission-mode yolo     # nothing asks
```

---

## 3. Custom Rules

For fine-grained control, build a `PermissionRuleset` from `Rule` objects.
Rules are evaluated in order and **last match wins** (like `.gitignore`).

```python
from chimera import PermissionAction, PermissionRuleset, Rule

permissions = PermissionRuleset(
    rules=[
        # Always allow reads
        Rule(tool_pattern="read_file",  action=PermissionAction.ALLOW),
        Rule(tool_pattern="search",     action=PermissionAction.ALLOW),
        Rule(tool_pattern="list_files", action=PermissionAction.ALLOW),

        # Block dangerous bash commands
        Rule(tool_pattern="bash", action=PermissionAction.DENY,
             arg_key="command", arg_pattern="rm *",
             description="Block file deletion"),
        Rule(tool_pattern="bash", action=PermissionAction.DENY,
             arg_key="command", arg_pattern="sudo *",
             description="Block privilege escalation"),

        # Ask for approval on everything else
        Rule(tool_pattern="*", action=PermissionAction.ASK),
    ],
    default=PermissionAction.ASK,
)
```

### Rule fields

| Field | Purpose |
|---|---|
| `tool_pattern` | Glob pattern matched against the tool name (`"bash"`, `"write_*"`, `"*"`). |
| `action` | `PermissionAction.ALLOW`, `.DENY`, or `.ASK`. |
| `arg_key` | Optional -- argument name to inspect (e.g. `"command"`). |
| `arg_pattern` | Glob pattern matched against the string value of `args[arg_key]`. |
| `description` | Human-readable explanation (for logging and auditing). |

### Evaluation order

Rules are checked top to bottom and the **last** matching rule wins.  In the
example above, a `bash` call with `command="rm -rf /"` matches both the
`"rm *"` DENY rule and the catch-all `"*"` ASK rule -- but because DENY comes
first and the catch-all is last, ASK wins.  To make DENY take priority, place
it **after** the catch-all:

```python
rules=[
    Rule(tool_pattern="read_file", action=PermissionAction.ALLOW),
    Rule(tool_pattern="*",         action=PermissionAction.ASK),
    # These come last, so they override the catch-all for matching calls
    Rule(tool_pattern="bash", action=PermissionAction.DENY,
         arg_key="command", arg_pattern="rm *"),
    Rule(tool_pattern="bash", action=PermissionAction.DENY,
         arg_key="command", arg_pattern="sudo *"),
]
```

---

## 4. Integration via LoopConfig

Permissions are injected into the agent's reasoning loop through `LoopConfig`.

```python
from chimera import Agent, DEFAULT_TOOLS, LoopConfig, Prompt, ReAct, create_provider

provider = create_provider(model="glm-5")

config = LoopConfig(permissions=permissions)
loop = ReAct(max_steps=30, config=config)

agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=loop,
    prompt=Prompt.from_string("You are a careful coding assistant."),
)

result = agent.run("Refactor the utils module.", env=None)
```

When the loop encounters a tool call:

1. It evaluates `permissions.evaluate(tool_name, args)`.
2. **ALLOW** -- the tool executes immediately.
3. **DENY** -- the tool call is skipped and the agent receives an error message.
4. **ASK** -- execution pauses for human confirmation (in interactive mode).

---

## 5. Monitoring with Events

Subscribe to `PermissionEvent` to log every permission decision.

```python
from chimera import EventBus, PermissionEvent

bus = EventBus()

@bus.on("permission")
def on_permission(event: PermissionEvent):
    status = "GRANTED" if event.granted else "BLOCKED"
    print(f"  [{status}] {event.tool_name} -> {event.action}")

config = LoopConfig(
    permissions=permissions,
    event_bus=bus,
)
```

You can also subscribe to `"*"` to see all events -- tool calls, results,
steps, errors, and more:

```python
@bus.on("*")
def on_any(event):
    print(f"  [{event.type}] {event}")
```

---

## 6. Full Example

```python
"""safe_agent.py -- Agent with full permission and monitoring setup."""

from chimera import (
    Agent,
    DEFAULT_TOOLS,
    EventBus,
    LoopConfig,
    PermissionAction,
    PermissionEvent,
    PermissionRuleset,
    Prompt,
    ReAct,
    Rule,
    ToolCallEvent,
    create_provider,
)

# --- Provider ---
provider = create_provider(model="glm-5")

# --- Permissions ---
permissions = PermissionRuleset(
    rules=[
        Rule(tool_pattern="read_file",  action=PermissionAction.ALLOW),
        Rule(tool_pattern="search",     action=PermissionAction.ALLOW),
        Rule(tool_pattern="list_files", action=PermissionAction.ALLOW),
        # Catch-all: ask before mutating
        Rule(tool_pattern="*",          action=PermissionAction.ASK),
        # Hard deny after catch-all so these override ASK
        Rule(tool_pattern="bash", action=PermissionAction.DENY,
             arg_key="command", arg_pattern="rm *"),
        Rule(tool_pattern="bash", action=PermissionAction.DENY,
             arg_key="command", arg_pattern="sudo *"),
    ],
)

# --- Event monitoring ---
bus = EventBus()

@bus.on("tool_call")
def on_tool(event: ToolCallEvent):
    print(f"  [call] {event.tool_name}")

@bus.on("permission")
def on_perm(event: PermissionEvent):
    status = "GRANTED" if event.granted else "BLOCKED"
    print(f"  [{status}] {event.tool_name} -> {event.action}")

# --- Agent ---
config = LoopConfig(permissions=permissions, event_bus=bus)
loop = ReAct(max_steps=30, config=config)

agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=loop,
    prompt=Prompt.from_string(
        "You are a helpful coding assistant. "
        "Always explain what you intend to do before doing it."
    ),
)

result = agent.run("Add a docstring to every function in main.py.", env=None)
print(result.output)
```

---

## Next Steps

- [Build a Coding Agent](/build-a-coding-agent/) -- see permissions in a full interactive agent.
- [Add a Custom Tool](/add-custom-tool/) -- create tools that respect `requires_approval`.
- [Compose Agents](/compose-agents/) -- apply permissions across multi-agent systems.
