# `chimera mink` slash commands

The `chimera mink` REPL ships with 30 built-in slash commands (registered in
`chimera/cli/slash_commands.py`). Nineteen are ported one-to-one from the
existing `chimera code` REPL; eleven are new in M1 to reach Claude Code
parity. The dispatcher is `chimera.cli.slash_commands.dispatch(line,
session, env, out)`; tab completion uses `COMMAND_NAMES`.

## Reference table

| Command       | Args                          | Description                                | Source |
|---------------|-------------------------------|--------------------------------------------|--------|
| /help         | —                             | Show all registered commands.              | ported |
| /model        | `[next|prev|<name>]`          | Show or cycle the active model.            | ported |
| /cost         | —                             | Show cumulative cost and per-model totals. | ported |
| /clear        | —                             | Clear conversation context.                | ported |
| /history      | `[N]`                         | Show recent messages.                      | ported |
| /tools        | —                             | List available tools.                      | ported |
| /context      | —                             | Show context size in tokens/chars.         | ported |
| /debug        | —                             | Toggle debug mode.                         | ported |
| /session      | `save|list|fork [name]`       | Manage saved sessions.                     | ported |
| /compact      | —                             | Compact context (HARD threshold).          | ported |
| /audit        | —                             | Show permission audit log.                 | ported |
| /checkpoint   | `save|list|restore|undo [id]` | Snapshot / rewind workspace.               | ported |
| /agent        | —                             | List agent presets.                        | ported |
| /exit         | —                             | Leave the REPL.                            | ported |
| /quit         | —                             | Alias for /exit.                           | ported |
| /init         | —                             | Summarise the project into CLAUDE.md.      | ported |
| /yolo         | —                             | Toggle bypass-permissions mode.            | ported |
| /tree         | —                             | Print the session tree.                    | ported |
| /branch       | `<entry-id> <name>`           | Branch from a given context entry.         | ported |
| /switch       | `<leaf-id>`                   | Switch to a leaf in the session tree.      | ported |
| /status       | —                             | One-screen status (model, mode, cwd…).     | new    |
| /doctor       | —                             | Environment health checks.                 | new    |
| /permissions  | —                             | Print active permission ruleset.           | new    |
| /hooks        | —                             | List registered PreToolUse / PostToolUse hooks. | new |
| /mcp          | —                             | List MCP servers and their tools.          | new    |
| /resume       | `<session-id>`                | Resume a saved session.                    | new    |
| /sandbox      | —                             | Toggle sandbox mode.                       | new    |
| /subagent     | `<name> <prompt>`             | Spawn a registered subagent.               | new    |
| /plugin       | `list|discover|install|enable|disable|uninstall [name]` | Manage plugins.    | new    |
| /review       | —                             | Review the current `git diff HEAD`.        | new    |
| /config       | —                             | Print effective merged settings.           | new    |

## New commands

### /status
Single-screen summary of the active session: provider model name,
permission mode (`yolo` vs `interactive`), tool count, working
directory, session id, cumulative USD cost, and approximate context
size. Reads only — never mutates session state.

### /doctor
Best-effort environment audit. Prefers a dedicated `chimera.cli.doctor`
module if present; otherwise probes `OLLAMA_HOST/api/tags` for
reachability, validates `./.claude/settings.json` JSON, and counts
servers configured in `~/.chimera/mcp.json`. Reports each row as
`ok`, `not present`, or `not available: <reason>`.

### /permissions
Dumps the live `PermissionChecker` ruleset reachable through
`session.agent.loop.config.permissions`. Lists `allow`, `ask`, and
`deny` rules (truncated to 20 each) along with the active
`PermissionMode`. Prints `not available: ...` when no checker is
attached — useful for asserting test stubs.

### /hooks
Walks `chimera.hooks.events.HookEvent` and asks `HookLoader` for
matchers configured for the project root. Output groups commands by
event with their matcher pattern. Returns `(none registered)` when no
hook is wired — degrades gracefully if `chimera.hooks` is missing.

### /mcp
Reads `~/.chimera/mcp.json`, prints each configured server and its
launch command, and (best-effort) asks `MCPToolSource.from_config` for
the live tool list. Tools are truncated to 30 lines. Helpful for
verifying that a freshly added `.mcp.json` entry parses.

### /resume
Stub in M1: surfaces a usage hint (`pass --resume <id> at launch`).
Live mid-session resume is wired in M4-D against
`EventSourcedSession.resume`. Kept in the registry now so docs and
tab-completion stay stable across milestones.

### /sandbox
Toggles `chimera.permissions.sandbox.toggle(session)` if the sandbox
module is installed. M1 ships the command but the underlying sandbox
implementation is shipped by a later milestone — handler returns
`not available: ...` until then.

### /subagent
Looks up `<name>` in `chimera.agents.loader.create_default_registry()`,
calls `build()` (or `create()`) and runs the constructed agent against
the supplied prompt. Output is truncated to 2000 chars. Errors are
caught and printed inline so the REPL never crashes.

### /plugin
Sub-dispatcher for `list / discover / install / enable / disable / uninstall
[name]`, delegating to `chimera.plugins.manager.PluginManager`. The
manager is cached on the session under `_plugin_manager` so successive
calls share state. `enable` and `disable` are aliases for
`PluginManager.load` / `PluginManager.unload` (audit M-3 / M-16).

### /review
Runs `git diff HEAD` in the cwd, hands the diff to
`ReviewOrchestrator(provider=session.provider).run(diff)`, and prints
the (truncated) result. Bails early on empty diffs or missing `git`
binary. Useful as a parity surface for Claude Code's `/review`.

### /config
Prefers `chimera.mink.settings.load_mink_settings()` (added in M2).
Falls back to printing every settings.json found under `.chimera/`,
`.claude/`, or `~/.chimera/`, in that order. Each file is dumped as
JSON for easy diff against expected values.

## Test surface

`tests/integration/test_mink_capabilities.py::test_09_resume_cost_compact_slash_commands_wired`
parametrises slash command dispatch over the documented registry: each
command is invoked through a stub session (MagicMock provider,
SimpleNamespace cost tracker, empty context). The contract under test
is *"`dispatch` returns `True` and the handler does not raise"*;
output assertions are intentionally loose because many handlers degrade
gracefully when their underlying subsystem (MCP, hooks, plugins) is
absent. `tests/mink/test_mink_cli.py` smoke-tests the `chimera mink`
subcommand surface (`--help`, `--version`, and a single-prompt
invocation when `OLLAMA_HOST` is reachable).
