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

| Field | Req | Meaning |
|---|---|---|
| `name` | yes | Unique id. Lowercase, hyphenated. |
| `version` | yes | SemVer string. |
| `entry` | yes | File to load, relative to manifest. |
| `language` | yes | `python`, `typescript`, or `javascript`. |
| `description` | no | One-line summary, shown in `/extensions`. |
| `weasel_min_version` | no | Refuse to load on older weasel. |
| `tools` / `hooks` / `slash_commands` / `prompts` | no | What the extension contributes. |
| `permissions` | no | Per-tool overrides (`allow` / `ask` / `deny`). |
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

TypeScript / JavaScript extensions run in a Node subprocess;
communication is JSON-RPC over stdio. The shape mirrors Python:

```ts
import type { ExtensionApi } from "chimera-weasel";

export default function register(api: ExtensionApi) {
  api.registerTool({
    name: "hello",
    description: "Say hi to <name>.",
    schema: { name: { type: "string" } },
    async run({ name }) { return `hello, ${name}!`; },
  });
}
```

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

Drop Jinja2-compatible Markdown files under `prompts/` (variables
as `{{ var }}`). A template like `prompts/review.md`:

```text
You are reviewing {{ file }}. Focus on correctness, security, and
performance. Begin by reading the file with `Read`.
```

Render and steer:

```python
session.steer(api.render_prompt("review.md", file="src/foo.py"))
```

## Permissions for extensions

An extension cannot escalate permissions. Its manifest
`permissions` block can only **tighten** what the user has
configured; it can request `ask` or `deny` but never relax what
the user already constrained. Project-level `.weasel/settings.json`
always wins.

First load of a new extension prompts in interactive mode or
requires `--allow-extensions <names...>` in one-shot / RPC.
Allowed extensions are recorded in `.weasel/settings.json` under
`extensions.allowed` (and `extensions.blocked` for shadowed ones).

## Lifecycle

On startup weasel walks the discovery roots, parses each manifest,
validates `weasel_min_version`, resolves `depends_on`, and loads
extensions in dependency order. Each `register(api)` runs once.
Tools, hooks, and slash commands fire per-turn as the loop runs.
On shutdown, `agent_end` hooks run and subprocess extensions get a
graceful SIGTERM.

## Distribution

Extensions are plain files. Ship them by copy-paste, git submodule,
pip-installable package (drops into `~/.weasel/extensions/<name>/`
on install), or npm package for TS/JS. The Node subprocess that
hosts TS extensions can `require` from `node_modules` if you ship a
`package.json` next to your extension. Weasel does not (yet) ship a
marketplace; install is `mv` or `git clone`.

## Node subprocess bridge (Wave 9)

Wave 9 (issue W1) wires JS/TS extensions into the agent's toolbelt
through a Node subprocess. Each tool declared on a JS/TS manifest is
wrapped in a `NodeExtensionTool` that, on call, spawns:

```bash
node <plugin_dir>/<entry>.js --tool <name> --args '<json-args>'
```

…and parses a single JSON object off stdout.

### Wire protocol

The Node side reads `--tool` and `--args` from `process.argv` and
prints exactly one JSON object to stdout. Two response shapes are
recognised:

```json
{ "output": "result string", "metadata": { "k": "v" } }   // success
{ "error":  "explanation" }                                // failure
```

Anything else (non-JSON, top-level array/scalar, missing keys, empty
stdout with non-zero exit) is surfaced as a `ToolResult` with a clear
`error` and the raw `stdout` / `stderr` / `exit_code` captured in
`metadata` so the failure is debuggable from the agent transcript.

### Manifest shape for JS/TS tools

```json
{
  "name": "review",
  "version": "0.2.1",
  "main": "index.js",
  "language": "javascript",
  "tools": [
    {
      "name": "review_diff",
      "description": "Review a diff against the codebase.",
      "parameters": {
        "type": "object",
        "properties": { "diff": { "type": "string" } }
      },
      "timeout": 60
    },
    "review_file"
  ]
}
```

`tools` may be a list of bare strings (uses the permissive default
schema and 30s timeout) or full descriptor dicts. Duplicate names
collapse to one wrapper.

### CommonJS vs. ESM

Both shapes are supported. The bridge inspects `package.json` for
`"type": "module"`; either way Node 14+ runs the entry via `node
<file>` without a flag. ESM extensions ship `index.mjs`, CommonJS
ships `index.js`. Authors writing TypeScript ship a build artifact —
the bridge never transpiles `.ts` itself.

### Failure modes

- **Node not on PATH** — the loader records a single explanatory load
  error; tool wrappers still build but each call returns a
  `ToolResult(error="Node is not installed...")`. Fail-open.
- **No JS entry point** — manifest declared `tools` but neither
  `manifest.main` nor an `index.{js,mjs,cjs}` exists. Same posture:
  loader-level error, runtime errors at call time.
- **Subprocess timeout** — per-tool `timeout` field (defaults to 30s)
  surfaces a clean `timed out` error rather than hanging the agent.
- **Crash inside JS** — non-zero exit with stderr captured in
  `metadata.stderr`; `error` says "exited N with no stdout".

### Minimal Node fixture

A self-contained `index.js` (no `node_modules`, no transpiler) that
satisfies the wire protocol:

```js
'use strict';
function getArg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : null;
}
const tool = getArg('--tool');
const args = JSON.parse(getArg('--args') || '{}');

function emit(o) { process.stdout.write(JSON.stringify(o)); }

if (tool === 'echo') emit({ output: String(args.text || '') });
else                 emit({ error: 'unknown tool: ' + tool });
```

Pair with a `package.json` declaring
`"language": "javascript"` and `"tools": ["echo"]` and the loader
will index it, build a `NodeExtensionTool("echo")`, and the agent
can call it like any other tool.

## See also

- [`modes.md`](modes.md) — the four modes share one extension surface.
- [`sdk.md`](sdk.md) — the same `extension` / `tool` / `hook` /
  `slash` decorators are exported from the SDK.
- [`security-and-trademarks.md`](security-and-trademarks.md) —
  permission posture for third-party extensions.
- [`parity-matrix.md`](parity-matrix.md) — extension-surface
  parity with the upstream minimal harness.
