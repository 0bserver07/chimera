---
title: Weasel Extensions
description: Author and ship extensions for chimera weasel — directory layout, manifest schema, the four extension kinds (tools, hooks, slash commands, prompt templates), and worked examples in Python and TypeScript.
---

# Weasel Extensions

Weasel ships a deliberately small core: a ReAct loop, the four
built-in tools (`Read`, `write`, `edit`, `bash`), a tight slash-command
palette, and the four operating modes from [`modes.md`](modes.md).
Everything else is an extension.

This page covers the extension surface end-to-end: where extensions
live on disk, what a manifest looks like, what an extension can
register, and worked examples in both Python and TypeScript.

## Discovery roots

Weasel auto-discovers extensions from these locations on startup,
in priority order (later wins on name collision):

1. **Project-local:** `./.weasel/extensions/` (cwd of the invocation).
2. **User-global:** `~/.weasel/extensions/`.
3. **Override:** path passed via `--extensions-dir` or
   `$WEASEL_EXTENSIONS_DIR`.

Disable discovery entirely with `--no-extensions`.

## Directory layout

The extension root holds a flat or nested mix of standalone files
and directories:

```text
.weasel/
  settings.json           # project-local settings (model, allowlist)
  extensions/
    hello.py              # standalone Python extension
    tidy.ts               # standalone TS extension (subprocess via Node)
    fmt.js                # standalone JS extension
    review/               # directory extension
      manifest.json       # required for directories
      index.py
      prompts/
        review.md
    audit/
      manifest.json
      index.ts
      hooks/
        pre_bash.ts
```

Single-file extensions (`*.py`, `*.ts`, `*.js`) work without a
manifest — the file itself declares everything via decorators.
Directory extensions require `manifest.json`.

## Manifest schema

`manifest.json` for directory extensions:

```json
{
  "name": "review",
  "version": "0.2.1",
  "description": "Multi-perspective code review tools.",
  "entry": "index.py",
  "language": "python",
  "weasel_min_version": "0.1.0",
  "tools": ["review_diff", "review_file"],
  "hooks": ["pre_tool_use"],
  "slash_commands": ["/review"],
  "prompts": ["prompts/review.md"],
  "permissions": {
    "bash": "ask",
    "write": "ask"
  },
  "depends_on": []
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Unique extension id. Lowercase, hyphenated. |
| `version` | yes | SemVer string. |
| `description` | no | One-line summary, shown in `/extensions`. |
| `entry` | yes | Path to the file to load (relative to manifest). |
| `language` | yes | `python`, `typescript`, or `javascript`. |
| `weasel_min_version` | no | Refuse to load on older weasel. |
| `tools` | no | Tool names this extension contributes. |
| `hooks` | no | Hook event names this extension subscribes to. |
| `slash_commands` | no | Slash commands registered (must start with `/`). |
| `prompts` | no | Prompt template files relative to the extension dir. |
| `permissions` | no | Per-tool permission overrides (`allow` / `ask` / `deny`). |
| `depends_on` | no | List of extension names this one requires. |

Single-file extensions infer `name` from the filename, `version`
from the `__version__` module attribute (or `"0.0.0"`), and
`language` from the suffix.

## Extension kinds

An extension can register four kinds of contribution. Most ship one
or two; nothing requires you to use all four.

### 1. Tools

Add a new tool to the agent's toolbelt. Tools are functions the
agent can call; the framework handles permission checks, audit
logging, event emission, and JSON-schema generation.

Python:

```python
from chimera.weasel.sdk import extension, tool

@extension(name="hello", version="0.1.0")
def register(api):
    @tool
    def hello(name: str) -> str:
        """Say hi to <name>."""
        return f"hello, {name}!"
    api.register_tool(hello)
```

TypeScript:

```ts
import type { ExtensionApi, ToolContext } from "chimera-weasel";

export default function register(api: ExtensionApi) {
  api.registerTool({
    name: "hello",
    description: "Say hi to <name>.",
    schema: { name: { type: "string" } },
    async run(args: { name: string }, ctx: ToolContext) {
      return `hello, ${args.name}!`;
    },
  });
}
```

TS / JS extensions run in a Node subprocess; communication is
JSON-RPC over stdio. Function calls between weasel and the
extension are RPC-marshalled per call.

### 2. Hooks

Subscribe to lifecycle events: pre-tool, post-tool, pre-step, on
error, on session start/end, on compaction. Hooks can mutate the
event, veto the action, or emit follow-up events.

Python:

```python
from chimera.weasel.sdk import extension, hook

@extension(name="audit", version="0.1.0")
def register(api):
    @hook("pre_tool_use")
    def gate_bash(event):
        if event.tool == "bash" and "rm -rf" in event.args.get("cmd", ""):
            return {"deny": "blocked by audit extension"}
        return None
    api.register_hook(gate_bash)
```

Available hook events: `agent_start`, `agent_end`, `turn_start`,
`turn_end`, `pre_tool_use`, `post_tool_use`, `permission_ask`,
`compaction`, `error`, `session_resumed`, `session_compacted`. Match
the names in `chimera/events/types.py`.

### 3. Slash commands

Register a `/foo` slash command for the interactive REPL.

Python:

```python
from chimera.weasel.sdk import extension, slash

@extension(name="cost", version="0.1.0")
def register(api):
    @slash("/cost-report")
    def cost_report(session) -> str:
        return f"This session spent ${session.cost:.4f} across {session.steps} steps."
    api.register_slash(cost_report)
```

The slash handler is called with the live session; its return value
is rendered to the REPL. Slash commands do not run in print or RPC
mode (except as `system_message` events).

### 4. Prompt templates

Drop Markdown files under `prompts/` and reference them by name.
Templates are Jinja2-compatible (variables in `{{ var }}`).

```text
review/
  manifest.json
  index.py
  prompts/
    review.md
```

`review.md`:

```text
You are reviewing {{ file }}. Focus on:
- correctness
- security
- performance

Begin by reading the file with `Read`, then walk through the diff.
```

Use it from a tool or slash command:

```python
prompt = api.render_prompt("review.md", file="src/foo.py")
session.steer(prompt)
```

## Permissions for extensions

An extension cannot escalate permissions. The `permissions` block
in the manifest can only **tighten** what the user has configured;
it can request `ask` or `deny` for a tool but never `allow` what
the user has set to `ask`. The user's project-level
`.weasel/settings.json` always wins.

The first time weasel loads a new extension that registers a tool
with bash or network access, the user is prompted (interactive
mode) or `--allow-extensions <names...>` (one-shot / RPC) is
required. Allowed extensions are recorded in
`.weasel/settings.json`:

```json
{
  "extensions": {
    "allowed": ["hello", "review", "audit"],
    "blocked": []
  }
}
```

## Lifecycle

1. **Startup.** Weasel walks discovery roots, parses each
   manifest (or single-file decorator), validates against
   `weasel_min_version`, resolves `depends_on`, then loads
   extensions in dependency order.
2. **Registration.** Each extension's `register(api)` runs once;
   side effects are limited to API calls.
3. **Per-turn.** Tools, hooks, and slash commands fire as the
   agent loop progresses.
4. **Shutdown.** Hooks subscribed to `agent_end` run; subprocess
   extensions get a graceful SIGTERM.

## Worked examples

### Tiny — single-file Python extension

`.weasel/extensions/echo.py`:

```python
from chimera.weasel.sdk import extension, tool

__version__ = "0.1.0"

@extension(name="echo")
def register(api):
    @tool
    def echo(text: str) -> str:
        """Echo the input."""
        return text
    api.register_tool(echo)
```

### Medium — directory extension with hook + slash command

`.weasel/extensions/audit/manifest.json`:

```json
{
  "name": "audit",
  "version": "0.2.0",
  "entry": "index.py",
  "language": "python",
  "hooks": ["pre_tool_use", "post_tool_use"],
  "slash_commands": ["/audit-summary"]
}
```

`.weasel/extensions/audit/index.py`:

```python
from chimera.weasel.sdk import extension, hook, slash

_calls: list[dict] = []

@extension(name="audit")
def register(api):
    @hook("post_tool_use")
    def record(event):
        _calls.append({"tool": event.tool, "ok": event.ok})

    @slash("/audit-summary")
    def summary(session) -> str:
        ok = sum(1 for c in _calls if c["ok"])
        return f"{ok}/{len(_calls)} tool calls succeeded this session."

    api.register_hook(record)
    api.register_slash(summary)
```

### TypeScript — registers a tool

`.weasel/extensions/tidy.ts`:

```ts
import type { ExtensionApi } from "chimera-weasel";

export default function register(api: ExtensionApi) {
  api.registerTool({
    name: "tidy",
    description: "Strip trailing whitespace from a file.",
    schema: { path: { type: "string" } },
    async run({ path }) {
      const fs = await import("node:fs/promises");
      const buf = await fs.readFile(path, "utf8");
      const cleaned = buf.replace(/[ \t]+$/gm, "");
      await fs.writeFile(path, cleaned);
      return `tidied ${path}`;
    },
  });
}
```

## Distribution

Extensions are plain files. Ship them by:

- **Copy-paste** into `.weasel/extensions/` (smallest unit of
  reuse).
- **Git submodule** under `.weasel/extensions/<name>/` (versioned).
- **Pip-installable package** that drops files into
  `~/.weasel/extensions/<name>/` on `pip install`. The package
  install hook is up to the publisher.
- **npm package** (TS/JS) similarly. The Node subprocess that
  hosts TS extensions can `require` from `node_modules` if you
  ship a `package.json` next to your extension.

Weasel does not (yet) ship a marketplace; install is `mv` or `git
clone`.

## See also

- [`modes.md`](modes.md) — the four modes share one extension surface.
- [`sdk.md`](sdk.md) — the same `extension` / `tool` / `hook` /
  `slash` decorators are exported from the SDK.
- [`security-and-trademarks.md`](security-and-trademarks.md) —
  permission posture for third-party extensions.
- [`parity-matrix.md`](parity-matrix.md) — extension-surface
  parity with the upstream minimal harness.
