# settings.json — ecosystem-compatible loader

`chimera/mink/settings.py` reads the same `.claude/settings.json` files
that the reference ecosystem reads, plus a `.chimera/settings.json` escape
hatch, and returns a unified `MinkSettings` dataclass. Use
`load_mink_settings()` to fetch it; use
`MinkSettings.to_chimera_loop_config()` to drop the result into
`AgentLoop`. (The legacy `chimera.config.cc_settings` /
`load_cc_settings` / `CCSettings` import path remains as a deprecated
alias for one release cycle.)

## Precedence ladder (low to high)

| # | Source                                       | Notes                                  |
|---|----------------------------------------------|----------------------------------------|
| 1 | System defaults                              | empty today; reserved                  |
| 2 | `~/.claude/settings.json`                    | user global                            |
| 3 | `<cwd>/.claude/settings.json`                | project, git-tracked                   |
| 4 | `<cwd>/.claude/settings.local.json`          | project local, gitignored              |
| 5 | `<cwd>/.chimera/settings.json`               | Chimera-only override                  |
| 6 | env vars (`ANTHROPIC_MODEL`, ...)            | applied last; runtime wins             |

Higher layers **override scalars** and **deep-merge dicts**. See
"Deep-additive divergence" below for arrays.

## Key schema

```jsonc
{
  "permissions": {
    "defaultMode": "default",      // default | plan | acceptEdits | bypassPermissions | dontAsk
    "allow": ["Read", "Bash(git status)"],
    "ask":   ["Bash(git push *)"],
    "deny":  ["WebFetch", "Bash(rm *)"],
    "additionalDirectories": ["/tmp/sandbox"],
    "disableBypassPermissionsMode": false
  },
  "hooks": {
    "PreToolUse":         [{"command": "echo pre"}],
    "PostToolUse":        [{"command": "echo post"}],
    "PostToolUseFailure": [{"command": "echo fail"}],
    "PermissionRequest":  [{"command": "echo perm"}]
  },
  "mcp": {
    "servers": {
      "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
    }
  },
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:11434"
  },
  "model": "ollama/kimi-k2.6:cloud",
  "outputFormat": "text",
  "agent": "general",
  "enableAllProjectMcpServers": false,
  "apiKeyHelper": "/usr/local/bin/anthropic-key",
  "autoMemoryEnabled": false
}
```

`camelCase` and `snake_case` are both accepted on input (the ecosystem
schema writes camelCase; Chimera-native edits often use snake_case). The
dataclass fields use `snake_case`.

## Permission pattern grammar

Recognised by `to_chimera_loop_config()`:

| Pattern             | Meaning                                          |
|---------------------|--------------------------------------------------|
| `Read`              | match any invocation of `Read`                   |
| `Bash(git status)`  | match when first arg matches the literal         |
| `Read(path:~/*)`    | match when `args["path"]` glob-matches `~/*`     |

The extended `Tool(arg_key:pattern)` form is M2's grammar extension. Legacy
`Tool(content)` continues to work and is parsed as `args["command"]` matching.

## Deep-additive divergence (intentional)

The ecosystem reference implementation **silently replaces** arrays under
`permissions/*`, `hooks/*`, and `mcp.servers` when a higher layer redefines
them, breaking team-policy stacks. See
[research/mink/12-cc-config.md](../../research/mink/12-cc-config.md)
"Bug to mirror or avoid".

Chimera's loader **diverges** here on purpose:

* Arrays under `permissions.allow / ask / deny / additional_directories`
  are concatenated, de-duplicated, order-preserving.
* Arrays under any `hooks.<EventName>` are concatenated.
* Dicts under `mcp.servers` are merged key-wise; if a user models
  `mcp.servers` as a list, lists are also concatenated.
* All other arrays are last-write-wins (ecosystem parity).
* All dicts are merged recursively.
* All scalars are last-write-wins.

This makes a project's `.claude/settings.json` **augment** the user's global
hooks rather than wiping them out.

## Environment overrides

Applied after every file layer:

| Env var                       | Maps to                          |
|-------------------------------|----------------------------------|
| `ANTHROPIC_MODEL`             | `model`                          |
| `ANTHROPIC_BASE_URL`          | `env.ANTHROPIC_BASE_URL`         |
| `ANTHROPIC_API_KEY`           | `env.ANTHROPIC_API_KEY`          |
| `ANTHROPIC_AUTH_TOKEN`        | `env.ANTHROPIC_AUTH_TOKEN`       |
| `CLAUDE_CODE_OUTPUT_FORMAT`   | `output_format`                  |

## Errors

`MinkSettingsError` (subclass of `ValueError`) is raised on:

* malformed JSON (message includes the file path, line, and column);
* a top-level JSON value that is not an object;
* a layer file that exists but cannot be read.

Missing files are silently skipped.

## Examples

```python
from pathlib import Path
from chimera.mink.settings import load_mink_settings

settings = load_mink_settings(cwd=Path.cwd())
print(settings.model)                     # e.g. "ollama/kimi-k2.6:cloud"
print(settings.permissions.allow)         # ["Read", "Bash(git status)", ...]

cfg = settings.to_chimera_loop_config()
# Pass cfg straight into ReAct/AgentLoop:
from chimera.core.loop import ReAct
loop = ReAct(max_steps=50, config=cfg)
```

To pin Ollama for a single project, drop this in `<project>/.claude/settings.json`:

```json
{
  "model": "ollama/kimi-k2.6:cloud",
  "env": {"ANTHROPIC_BASE_URL": "http://localhost:11434"},
  "permissions": {"allow": ["Read", "Glob", "Grep", "Bash(git status)"]}
}
```

User-global hooks in `~/.claude/settings.json` will be **preserved** (deep-merge),
not wiped, when the project file loads.
