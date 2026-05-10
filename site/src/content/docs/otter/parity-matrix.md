---
title: Otter Parity Matrix
description: Feature-by-feature parity table between chimera otter and the upstream open-source coding agent it mirrors.
---

# `chimera otter` Parity Matrix

**Source baseline:** `research/otter/SPEC.md` (Apr 25 2026), upstream tree
walk under `packages/opencode/src/` (cli, agent, command, mcp, lsp, acp,
plugin, config, permission).
**Updated:** 2026-04-25, after wave-1 ship (O1–O15). Refreshed 2026-04-30
by D1 at the wave-9 close — five YELLOW rows promoted to GREEN as the
wave-9 batch (O1/O2/O3/O4/O5) closed the carry-over gaps.
**Legend:** GREEN = shipped / at parity (or superset); YELLOW = partial; RED = deferred.

> **Wave-9 close (2026-04-30, D1).** Five carry-over items from the
> wave-1 follow-up list landed in this wave:
>
> - `agents create` (interactive scaffolder) — O1/W9, `chimera/otter/agents.py`.
> - `mcp add` / `mcp auth` (live add + OAuth device-flow) — O2/W9,
>   `chimera/otter/mcp_cli.py`.
> - `-f` / `--file` (one-shot file attachment, stdin via `-`, 100 KB
>   warn / 500 KB cap) — O3/W9, `chimera/otter/cli.py`.
> - `--title` + `sessions rename` (titleable runs) — O4/W9,
>   `chimera/otter/cli.py` + `chimera/otter/sessions.py`.
> - `chimera completion bash|zsh|fish [--cli all|<animal>]` —
>   O5/W9 cross-CLI shell-completion generator, `chimera/cli/completion.py`.
>
> All five reports live under `research/otter/{O1..O4}-W9-REPORT.md` and
> `research/O5-COMPLETION.md`. Live tests against the new files pass:
> 10 (`test_agents_create.py`) + 25 (`test_mcp_cli.py`) + 16
> (`test_file_attachment.py`) + 19 (`test_session_title.py`) + 25
> (`test_completion.py`) = 95 new tests. The trademark scrub still
> exits OK at the codename-aggregate level (`passed: 7`).

> **Trademark hygiene.** Throughout this document the upstream project is
> referred to as "the open-source coding agent" or "the upstream". Live
> references to filesystem paths such as `~/.opencode/config.json` are
> kept because they are facts (the directory `chimera otter` reads from
> on disk), not brand claims. See `docs/otter/security-and-trademarks.md`.

## Top-level CLI subcommands

The upstream CLI registers about twenty top-level commands (see
`packages/opencode/src/index.ts`). Otter's surface is intentionally
narrower: the long tail of `account` / `db` / `github` / `pr` / `upgrade`
/ `uninstall` / `web` / `import` / `export` / `generate` / `stats` /
`debug` is out of scope for wave-1 because Chimera already covers those
flows through different surfaces (`chimera mink runs`, `chimera eval`,
`pyproject.toml` install, etc.). Wave-1 focuses on the six surfaces that
make otter useful as a coding-agent driver.

| Upstream surface             | Otter status | File                                                            | Notes |
|------------------------------|--------------|-----------------------------------------------------------------|-------|
| `run [message..]`            | GREEN        | `chimera/otter/cli.py` (`-p`/`--print`)                         | Mirrors the one-shot `run` flow: prompt arg, `--model`, `--output-format text\|json\|stream-json`, `--cwd`, `--allowed-tools`, `--no-save`, `--run-id`. |
| `serve` (HTTP)               | GREEN        | `chimera/otter/server.py`, dispatched from `cli.py`             | Minimum REST + SSE surface (`/healthz`, `/session`, `/turn`, `/events`). Backs an out-of-tree TUI client. |
| `acp`                        | GREEN        | `chimera/otter/acp.py` (invoked via `serve --acp`)              | JSON-RPC 2.0 over stdio. `initialize`, `session/new`, `session/message`, `session/cancel`, permission round-trip. |
| `session list \| delete`     | GREEN / YELLOW | `chimera/otter/sessions.py`, `cli.py`                         | `sessions list` + `sessions show <id>` shipped (read-only). `delete` not yet wired (covered by `rm -rf <eventlog-dir>`). |
| `agent create \| <list>`     | GREEN        | `chimera/otter/agents.py` (`cmd_agents_create`)                 | `agents list/show` (read-only) + `agents create [<name>] [--user]` interactive scaffolder shipped. Stdlib-only (no `$EDITOR`, no readline); writes `.opencode/agent/<name>.md`. **Wave-9 close (O1/W9).** |
| `mcp list \| add \| auth`    | GREEN        | `chimera/otter/mcp.py` + `chimera/otter/mcp_cli.py`             | `mcp list` walks user + project scopes; `mcp add <name> [-- <command...>]` (or `--mcp-http`) writes the entry atomically to the chosen scope's config; `mcp auth <name>` runs `OAuthDeviceFlow` for HTTP entries with manual paste fallback. **Wave-9 close (O2/W9).** |
| `models [provider]`          | GREEN        | `chimera/otter/providers.py`                                    | `--model provider/model` honored everywhere; `OTTER_MODEL` env var pinned. Refresh-cache flag deferred. |
| `providers` (auth login/list)| YELLOW       | `chimera/otter/providers.py`                                    | Default chain (Anthropic → OpenRouter → OpenAI); device-flow OAuth ridden via `chimera.auth`. Plugin-driven auth methods deferred. |
| `stats`                      | GREEN        | `chimera otter sessions cost` (G6)                              | `sessions cost --since 7d --format json` aggregates `~/.chimera/eventlog/otter-*/summary.json`. Same rollup the `GET /runs/cost` HTTP route serves. |
| `export` / `import`          | YELLOW       | `chimera/otter/share_cmd.py` covers export                       | `share <id> --sink file --format json` writes a portable transcript. Import flow deferred. |
| `pr <number>` (PR checkout)  | RED          | n/a                                                             | Out of scope for wave-1; users run `gh pr checkout` then `chimera otter`. |
| `github` (CI bot)            | RED          | n/a                                                             | Defer; orthogonal to coding-agent runtime. |
| `db` (sqlite shell)          | RED          | n/a                                                             | Otter persists JSONL eventlogs, not sqlite. No equivalent needed. |
| `account` (auth/team)        | RED          | n/a                                                             | Single-tenant; no central account service. |
| `upgrade` / `uninstall`      | RED          | `pip install -U chimera-run` covers upgrade                     | Use the upstream packaging story. |
| `web` (web tunnel)           | RED          | n/a                                                             | Out of scope. |
| `generate` (OpenAPI dump)    | RED          | n/a                                                             | Defer; generated when needed via `chimera/otter/server.py`. |
| `tui thread \| attach`       | YELLOW       | `chimera otter` REPL                                            | Interactive REPL ships; multi-window thread mode deferred. |
| `plugin` (install/list/...)  | GREEN        | `chimera/otter/plugins.py`                                      | Reads `~/.opencode/plugin/<name>/` and project-level overrides; agents/commands/MCP entries are forwarded to existing Chimera registries. |
| `completion`                 | GREEN        | `chimera/cli/completion.py` (`chimera completion bash\|zsh\|fish`) | Cross-CLI shell-completion generator, walks the live argparse tree. Honors `--cli all\|<animal>`. **Wave-9 close (O5).** |

## `run` (and `chimera otter -p`) flag map

The upstream `RunCommand` (see `packages/opencode/src/cli/cmd/run.ts`)
exposes 17 flags. Otter mirrors the ones that affect agent semantics
and skips the ones that depend on opencode-specific server/session
plumbing.

| Upstream flag         | Otter status | Otter equivalent                                | Notes |
|-----------------------|--------------|-------------------------------------------------|-------|
| `--command`           | YELLOW       | `/<custom-command>` in REPL                     | One-shot `--command` invocation deferred; custom commands run in REPL today. |
| `--continue`/`-c`     | YELLOW       | `chimera otter sessions show <id>` + manual resume | Wave-2 will add `--continue` flag wired to `EventSourcedSession.resume`. |
| `--session`/`-s`      | YELLOW       | `chimera otter sessions show <id>`              | Same as above; live one-shot resume deferred. |
| `--fork`              | RED          | n/a                                             | Requires the `--continue` story; deferred. |
| `--share`             | YELLOW       | `chimera otter share <id>`                      | Out-of-band share subcommand instead of in-line flag. |
| `--model`/`-m`        | GREEN        | `--model`, `$OTTER_MODEL`                       | Identical syntax: `provider/model`. |
| `--agent`             | GREEN        | shimmed via `chimera.cli.code` agent resolution | Honors `chimera otter agents list`. |
| `--format`            | GREEN        | `--output-format text\|json\|stream-json`       | Adds `stream-json` (one event per line) on top of upstream's `default\|json`. |
| `--file`/`-f`         | GREEN        | `-f / --file PATH` (repeatable; `-` for stdin)  | Stacks via `action="append"`; wraps each file in `<file path="X" lines="N">…</file>` and prepends to `-p` prompt. 100 KB per-file warn, 500 KB cumulative cap with truncation marker. **Wave-9 close (O3/W9).** |
| `--title`             | GREEN        | `--title "..."` + `sessions rename <id> <title...>` | Persists in `summary.json`, surfaces in `sessions list` (TITLE column) + `sessions show` (`title:` line). Empty title clears the label. **Wave-9 close (O4/W9).** |
| `--attach`            | RED          | n/a                                             | Remote-server attach is covered by `chimera mink --remote ssh://...` instead. |
| `--password`/`-p`     | RED          | basic auth managed via reverse proxy           | Otter server is unauthenticated by default; production deployments front it with the user's reverse proxy. |
| `--dir`               | GREEN        | `--cwd`                                         | Same semantics. |
| `--port`              | GREEN        | `chimera otter serve --port`                    | Same. |
| `--variant`           | YELLOW       | n/a                                             | Provider-specific reasoning effort flag deferred. |
| `--thinking`          | YELLOW       | env var `CHIMERA_THINKING_LEVEL`                | Surface via env var instead of flag for now. |
| `--print-logs`/`--log-level` | YELLOW | `CHIMERA_LOG_LEVEL` env var                     | Otter inherits chimera-wide log handling. |
| `--pure`              | RED          | n/a                                             | Otter has no equivalent of opencode's "no external plugins" mode; use `--allowed-tools` to narrow surface. |

## Slash-command palette (REPL)

The upstream TUI registers about thirty slash-eligible commands (see
`packages/opencode/src/cli/cmd/tui/app.tsx`). Otter ships the
core conversational subset (twenty-five) and skips terminal-window
controls that don't apply to a CLI REPL.

| Upstream slash               | Otter status | Otter handler                                     |
|------------------------------|--------------|---------------------------------------------------|
| `/help`                      | GREEN        | `cmd_help` (shared registry)                      |
| `/exit` (`/quit`, `/q`)      | GREEN        | `cmd_exit`, `cmd_quit`                            |
| `/sessions` (`/resume`, `/continue`) | GREEN | `cmd_sessions` (`chimera/otter/slash.py`)         |
| `/new` (`/clear`)            | GREEN        | `cmd_new`, `cmd_clear`                            |
| `/share`                     | GREEN        | `cmd_share` -> `chimera/otter/share_cmd.py`       |
| `/models`                    | GREEN        | `cmd_models` (alias of shared `/model`)           |
| `/agents`                    | GREEN        | `cmd_agents` (alias of shared `/agent`)           |
| `/agent`                     | GREEN        | shared `cmd_agent`                                |
| `/model`                     | GREEN        | shared `cmd_model`                                |
| `/init`                      | GREEN        | shared `cmd_init`                                 |
| `/cost`                      | GREEN        | shared `cmd_cost`                                 |
| `/tools`                     | GREEN        | shared `cmd_tools`                                |
| `/mcp` / `/mcps`             | GREEN        | shared `cmd_mcp`, `cmd_mcps`                      |
| `/connect`                   | YELLOW       | `cmd_connect` (late-binds providers)              |
| `/status`                    | GREEN        | shared `cmd_status`                               |
| `/themes`                    | YELLOW       | `cmd_themes` stub                                 |
| `/yolo`                      | GREEN        | shared `cmd_yolo`                                 |
| `/compact`                   | GREEN        | shared `cmd_compact`                              |
| `/doctor`                    | GREEN        | shared `cmd_doctor`                               |
| `/config`                    | GREEN        | shared `cmd_config`                               |
| `/undo`                      | YELLOW       | `cmd_undo` stub                                   |
| `/redo`                      | YELLOW       | `cmd_redo` stub                                   |
| `/edit`                      | YELLOW       | `cmd_edit` stub                                   |
| `/variants`                  | RED          | n/a                                               |
| `/workspaces`                | RED          | n/a (single-cwd model)                            |
| terminal-title toggle        | RED          | n/a                                               |
| heap-snapshot / debug overlay | RED         | n/a                                               |

## Plugin hooks (server-side)

The upstream's plugin SDK defines a `Hooks` interface with sixteen
extension points (see the upstream's `plugin/index.ts` server module
and SDK `index.ts`). Otter maps each to existing Chimera primitives.

| Upstream hook                              | Otter status | Mapping                                                            |
|--------------------------------------------|--------------|--------------------------------------------------------------------|
| `event`                                    | GREEN        | `chimera.events.EventBus`                                          |
| `config`                                   | YELLOW       | `chimera.config.config_file.ChimeraConfig.load`                    |
| `tool` (custom tool registration)          | GREEN        | `chimera.plugins.PluginExtensionRegistry.tools`                    |
| `auth`                                     | YELLOW       | `chimera.auth` (device flow, OAuth); plugin-driven providers TODO  |
| `chat.message`                             | GREEN        | `Hook` with `event="chat.message"` in `chimera.hooks.events`        |
| `chat.params`                              | YELLOW       | not yet emitted (provider params bypass hooks today)               |
| `chat.headers`                             | YELLOW       | not yet emitted                                                    |
| `permission.ask`                           | GREEN        | `chimera.permissions.audit` + `ConfirmationPolicy`                 |
| `command.execute.before`                   | YELLOW       | `chimera.hooks` `PreCommand` event TODO                            |
| `tool.execute.before`                      | GREEN        | `Hook` `event="tool.execute.before"` (PreToolUse)                  |
| `tool.execute.after`                       | GREEN        | `Hook` `event="tool.execute.after"` (PostToolUse)                  |
| `shell.env`                                | YELLOW       | `BashOps.env` injection point exists; plugin event TODO            |
| `experimental.chat.messages.transform`     | RED          | not exposed                                                        |
| `experimental.chat.system.transform`       | YELLOW       | system-prompt rules ingest covers static case                      |
| `experimental.session.compacting`          | YELLOW       | `chimera.compaction` ABC supports custom strategies                |
| `experimental.text.complete`               | RED          | not exposed                                                        |
| `tool.definition` (tool description rewrite)| YELLOW       | `ToolGroup` middleware can wrap definitions                        |

## Configuration keys (`~/.opencode/config.json`)

Upstream's config schema (see `packages/opencode/src/config/config.ts`)
defines roughly sixty top-level keys. Otter ingests the subset that
maps onto first-class Chimera primitives and ignores keys that drive
TUI-only chrome.

| Upstream key                | Otter status | Notes |
|-----------------------------|--------------|-------|
| `mcp`                       | GREEN        | Read by `chimera/otter/mcp.py`. Both `local` (stdio) and `remote` (http) transports honored. |
| `agent`                     | GREEN        | Combined with `.opencode/agent/*.md` markdown agents. |
| `command`                   | GREEN        | Combined with `.opencode/command/*.md`. |
| `provider`                  | YELLOW       | Default-chain wiring lives in `chimera/otter/providers.py`; per-provider overrides honored opportunistically. |
| `permission`                | GREEN        | Mapped to `chimera.permissions` rules at session bootstrap. |
| `instructions`              | GREEN        | Concatenated by `chimera/otter/rules.py`. |
| `keybinds`                  | RED          | Otter is a CLI; no keybinds. |
| `theme`                     | RED          | TUI chrome only. |
| `share`                     | YELLOW       | `chimera otter share` covers the export sink; `auto`-share toggle deferred. |
| `plugin`                    | GREEN        | Spec list resolved by `chimera/otter/plugins.py`. |
| `disabled_providers`        | YELLOW       | Honored opportunistically when the env-var presence check fires. |
| `experimental.*`            | RED          | Defer until the upstream stabilizes them. |

## Counts

- **Top-level CLI subcommands:** 10 GREEN, 2 YELLOW, 8 RED of the 20 upstream commands (refreshed at wave-9 close — `agent create`, `mcp add/auth`, and `completion` promoted to GREEN). Coverage focuses on the agent runtime; admin/install/account flows are intentionally not mirrored.
- **`run` flags:** 7 GREEN, 5 YELLOW, 5 RED of the 17 upstream flags (refreshed at wave-9 close — `--file/-f` and `--title` promoted to GREEN).
- **Slash commands:** 17 GREEN, 7 YELLOW, 4 RED of the 28 reviewed.
- **Plugin hooks:** 5 GREEN, 8 YELLOW, 4 RED of the 17 reviewed.
- **Config keys:** 6 GREEN, 4 YELLOW, 4 RED of the 14 reviewed.

## Chimera-only capabilities (do not regress)

Wave-1 of otter inherits the same Chimera-only superset that mink
already advertises: cooperative `CancellationToken`, mid-turn
`MessageQueues` steering, loop detection, ghost commits, instruction
anchor, learning store, discipline guards, `EventSourcedSession`
crash recovery, `FileAwareCompaction`, `SessionTree` in-place
branching, `RedactionMiddleware`, `CostTracker` with cache/reasoning
breakdown, the 26-event `EventBus`, and the multi-tier
`AgentConfig.from_markdown()` registry.

## Follow-up issues to file (or already filed)

1. `chimera otter --continue` / `--session <id>` / `--fork` (one-shot resume).
2. ~~`chimera otter agents create` (interactive agent scaffolder).~~ Shipped at wave-9 close (O1/W9).
3. ~~`chimera otter mcp add` / `chimera otter mcp auth <name>` (live add + OAuth).~~ Shipped at wave-9 close (O2/W9).
4. `chimera otter session delete <id>` (currently `rm -rf` the eventlog dir).
5. Plugin-driven provider auth (mirror the upstream `Hooks.auth` flow).
6. ~~`chimera otter stats --since=<duration>` aggregating `summary.json` across `~/.chimera/eventlog/otter-*/`.~~ Shipped as `chimera otter sessions cost` (G6).
7. ~~File-attachment flag (`--file <path>`) for one-shot prompts.~~ Shipped at wave-9 close (O3/W9; `-f` / `--file PATH`, repeatable, stdin via `-`).
8. Theme switcher (`/themes`) for the REPL once a TUI palette lands.
9. Workspace mode (multiple cwds in a single session).
10. ~~Shell-completion script generation (`chimera otter completion`).~~ Shipped at wave-9 close as the cross-CLI `chimera completion bash|zsh|fish [--cli all|<animal>]` generator (O5).
11. Session titles. ~~Add a `--title "..."` flag and `sessions rename <id> <title>` subcommand.~~ Shipped at wave-9 close (O4/W9).

## How to use

When a user runs `chimera otter` from a project that already contains
`.opencode/config.json`, every GREEN row above is expected to behave
in lockstep with the upstream open-source coding agent at the
black-box level (one-shot prompt, slash command, MCP server discovery,
agent / command / rules ingest, plugin loading). YELLOW rows degrade
gracefully and emit a hint where the gap is user-visible. RED rows
are not implemented and will return a "not yet wired" notice with a
pointer to the issue tracker entry.

## Cross-links

- [Chimera architecture (8-phase map)](../architecture.md) — where the rows in this matrix live in the shared library.
