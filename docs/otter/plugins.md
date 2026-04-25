# `chimera otter` plugins

Otter loads plugins from the `~/.opencode/plugin/<name>/` directory
layout used by the open-source coding-agent ecosystem, while delegating
all heavy lifting to the existing Chimera plugin runtime
(`chimera/plugins/`).

## Sources

The loader (`chimera/otter/plugins.py`) scans two roots:

1. `~/.opencode/plugin/<name>/` — user-level. Loaded for every
   project on the host.
2. `<project_root>/.opencode/plugin/<name>/` — project-level.
   Overrides user-level on plugin-name conflict.

Each plugin lives in its own directory containing a manifest file
and (optionally) sub-directories for agents, commands, hooks, and
MCP server declarations.

## Manifest

The manifest file is JSON. The loader accepts four filenames (in
priority order):

1. `manifest.json`
2. `plugin.json`
3. `chimera-plugin.json`
4. `package.json`

The schema is intentionally permissive — only the well-known keys are
read; everything else is preserved on the resulting `PluginInfo` for
inspection but ignored at load time.

```json
{
  "name": "my-plugin",
  "version": "0.2.1",
  "description": "Adds project-specific tools and hooks.",
  "author": "Yad",
  "license": "MIT",
  "keywords": ["chimera", "otter"],
  "homepage": "https://example.com/my-plugin"
}
```

## Plugin contents

A loaded plugin can contribute, by directory convention:

- `agents/<name>.md` — markdown agent definitions. Same schema as
  `docs/otter/agents.md`.
- `commands/<name>.md` — markdown custom commands. Same schema as
  `docs/otter/commands.md`.
- `hooks/<event>.json` — hook definitions (see below).
- `mcp.json` — MCP server entries to merge into the project's MCP
  config.
- `tools/<name>.py` — Python tool modules (require explicit opt-in).

The convention is "drop-files-and-they-show-up". The loader walks
each subdirectory and forwards entries to the right Chimera registry:

| Plugin dir       | Forwards to                                          |
|------------------|------------------------------------------------------|
| `agents/`        | `chimera.agents.loader.AgentLoader`                  |
| `commands/`      | `chimera/otter/commands.py` registry                 |
| `hooks/`         | `chimera.hooks.executor.HookExecutor`                |
| `mcp.json`       | `chimera/otter/mcp.py` server list                   |
| `tools/`         | `chimera.plugins.PluginExtensionRegistry.tools`      |

## Hook definitions

A hook file declares one or more hooks that fire on specific events:

```json
{
  "hooks": [
    {
      "event": "PreToolUse",
      "matcher": "Bash",
      "command": "./scripts/audit-bash.sh"
    },
    {
      "event": "PostToolUse",
      "matcher": "Edit",
      "command": "ruff check ${CHIMERA_FILE_PATH}"
    }
  ]
}
```

Recognized events:

- `PreToolUse` / `PostToolUse` — fire around every tool call.
- `UserPromptSubmit` — fire on every user message.
- `AssistantResponse` — fire on every assistant message.
- `SessionStart` / `SessionEnd` — lifecycle hooks.
- `Stop` — fire when the agent reaches a stop condition.

The `command` is run with the cwd inherited from the agent and a
small set of `CHIMERA_*` environment variables set (`CHIMERA_TOOL`,
`CHIMERA_TOOL_INPUT`, `CHIMERA_FILE_PATH`, etc.). Standard output
becomes a hook event published on the `EventBus`. Non-zero exit codes
mark the hook as "failed" but do not abort the turn.

## Plugin discovery

```python
from chimera.otter.plugins import discover_plugins, load_plugins

infos = discover_plugins(project_root="/path/to/project")
# Returns a list of PluginInfo dataclasses.

# Load and activate the plugins (forward their entries to registries).
loaded = load_plugins(project_root="/path/to/project")
```

`PluginInfo` carries the manifest fields plus the absolute plugin
directory and a `source` field (`"user"` or `"project"`). The list
is sorted by name for stable output.

## Loading order

1. User-level plugins, sorted by name.
2. Project-level plugins, sorted by name.

When the same plugin name appears at both levels, the project version
wins. The user version is still inspected (its manifest is parsed)
but its contents are skipped to avoid duplicate registration.

## Activation

The runtime calls `load_plugins(...)` once at session bootstrap. Each
plugin's contents are forwarded to the appropriate registry:

- Agent and command markdown files are read and merged into the same
  registries that `.opencode/agent/` and `.opencode/command/` feed.
  Project-tree files still win on name conflict.
- Hook definitions are added to the `HookExecutor`.
- MCP entries are merged into the otter MCP server list.
- Tool modules are imported, but only when the plugin manifest
  explicitly opts in via `"tools": true`. This avoids surprising
  Python imports for plugins shipped without code.

## Disabling a plugin

Three options:

1. Delete the plugin directory.
2. Rename the manifest file to something the loader doesn't
   recognize (`manifest.json.bak`).
3. Add the plugin name to the otter config's `disabled_plugins` list:

```json
{ "disabled_plugins": ["heavy-plugin", "experimental-thing"] }
```

The third option keeps the directory in place so re-enabling is just
a config edit.

## Compatibility with upstream plugins

Most upstream plugin manifests load without modification because the
loader is permissive. Caveats:

- TypeScript plugin code is not executed; only the manifest +
  declarative subdirectories are honored. JS-coded hooks become
  no-ops.
- The upstream `Hooks.tool` hook (custom tool registration in JS) has
  no Python equivalent; ship the tool as a Python module under
  `tools/<name>.py` instead.
- The upstream `Hooks.auth` hook (custom provider auth flow) is not
  honored; use Chimera's auth providers (`chimera/auth/`).

## Test surface

`tests/otter/test_plugins.py` covers (twenty-four tests):

- Manifest discovery across all four accepted filenames.
- User-only / project-only / mixed configurations.
- Project-overrides-user precedence.
- Empty plugin directory (manifest only) loads cleanly.
- Malformed JSON manifest → loader warns and skips.
- Hook forwarding to `HookExecutor`.
- MCP entry merging.
- `disabled_plugins` config honored.

## See also

- `chimera/otter/plugins.py` — loader.
- `chimera/plugins/` — runtime (manager, registry, marketplace).
- `docs/otter/agents.md` — agent markdown schema (also used by
  plugin-shipped agents).
- `docs/otter/commands.md` — command markdown schema.
- `docs/otter/mcp.md` — MCP server schema.
- `docs/otter/parity-matrix.md` — overall parity status.
