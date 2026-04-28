---
title: "Mink Parity Matrix"
description: "Feature-by-feature parity table between chimera mink and the upstream open-source coding agent it mirrors."
---

# `chimera mink` Parity Matrix

**Source baseline:** `research/mink/24-gap-analysis.md` (Apr 23 2026).
**Updated:** 2026-04-25, after v0.4.0 ship (M0–M5 + wave-2 follow-ups).
**Legend:** GREEN = shipped / at parity (or superset); YELLOW = partial; RED = deferred.

## Subsystems

| # | Subsystem | Status | Shipped artifact | Notes |
|---|-----------|--------|------------------|-------|
| 1 | Turn loop | GREEN | `chimera/core/agent_loop.py`, `loop.py` | Already a superset (cancellation, steering, learning). Minor `PromptCacheEvent` shape diff is cosmetic. |
| 2 | Tool system / registry | GREEN | `chimera/core/tool.py`, `tool_executor.py` | Anthropic + OpenAI schemas; permission/event hooks; concurrent dispatch. |
| 3 | Built-in tools | GREEN | `chimera/tools/` (incl. `notebook_edit.py`, `worktree_tool.py`, `task_tool.py`, `cron_tools.py`, `powershell.py`, `config_tool.py`) | All M3+M5 tools landed. PowerShell falls through to `pwsh` on non-Windows. |
| 4 | Tool-call streaming | GREEN | `chimera/providers/ollama.py:stream()` | NDJSON parser accumulates `tool_calls`; emits `tool_call_start/delta/complete`. |
| 5 | Slash commands | GREEN | `chimera/cli/slash_commands.py` | Extracted from `code.py` for reuse by the mink REPL; full M1 set wired (31 commands per `research/mink/HANDOFF.md`). |
| 6 | Hooks | GREEN | `chimera/hooks/executor.py`, `emitter.py`, `events.py` | `updatedInput` mutation + cwd/env inheritance + audit trail wired in M2-B. |
| 7 | MCP | YELLOW | `chimera/mcp/{client,lifecycle,oauth,sse_transport,ws_transport,tools}.py` + `chimera/mink/cli.py:_load_mcp_tools` + `chimera/cli/code.py` | All transports + OAuth2 PKCE + `mcp__server__tool` prefix shipped in M5-A; `chimera mink` and `chimera code` now load `~/.chimera/mcp.json` and `<cwd>/.mcp.json` and merge servers (audit B-6 fix). End-to-end install of a community server over OAuth has not been independently verified. |
| 8 | Permissions | GREEN | `chimera/permissions/` + `chimera/cli/permission_prompt.py` + `chimera/mink/settings.py:to_chimera_loop_config` | `Tool(arg_key:pattern)` grammar; `.claude/settings.json` allow/ask/deny lists are now consumed by `chimera mink -p` (audit B-4 fix). |
| 9 | TUI / streaming render | GREEN | `chimera/cli/render.py:MinkStreamHandler` + `build_stream_handler` wired in `chimera/mink/cli.py:_run_print_mode` and `chimera/cli/code.py` (opt-in via `CHIMERA_RICH_TUI=1`) | MarkdownStream, Spinner, ToolBlockRenderer, ThinkingBlockRenderer, DiffRenderer all flow through the live runtime. Auto-detect: rich on TTY, plain on pipe / `NO_COLOR` / `--no-color` / `--no-rich`. Audit B-2 / B-7 / B-8 closed. |
| 10 | Sessions / resume / fork | GREEN | `chimera/sessions/` + `/resume` slash command | Already superset; `/resume <id>` exposed in M4. |
| 11 | Subagents / Task tool | GREEN | `chimera/tools/task_tool.py`, `chimera/cli/agent_teams.py` | Child `AgentLoop` spawn with linked cancellation; experimental teams behind env flag. |
| 12 | Settings / config | GREEN | `chimera/mink/settings.py` + `chimera/tools/config_tool.py` + `chimera/mink/cli.py:_run_print_mode` | User → project → local merge; rules now feed the live `LoopConfig.permissions` for one-shot `-p` runs (audit B-4 fix). M5-C `ConfigTool` exposes get/set/list. |
| 13 | Ollama provider (Kimi K2.6 / GLM-5) | YELLOW | `chimera/providers/ollama.py` | Streaming + tool-roundtrip + `num_ctx`/`keep_alive`/`think` shipped. Vision and JSON-grammar mode still deferred (Ollama-cloud limitation). |
| 14 | Cancellation / Ctrl-C | GREEN | `chimera/core/cancellation.py` | Chimera-only superset. |
| 15 | Secrets redaction | GREEN | `chimera/secrets/` + `chimera/cli/output_format.py` | `RedactionMiddleware` wired into `stream-json` output as of v0.4.0. Chimera-only superset. |
| 16 | Remote (SSH) execution[^remote] | YELLOW | `chimera/env/ssh.py` + `chimera/mink/cli.py:_build_environment` (`--remote ssh://user@host[:port][/path]`) | Scaffold landed in v0.4.0; routes file/bash tools through `SSHEnvironment`. 10/13 spec items implemented per HANDOFF; asyncssh + SFTP + ProxyJump still TODO (#127). |
| 17 | Run persistence + sharing[^share] | GREEN | `chimera/mink/runs.py` + `chimera/sessions/share.py` + `chimera/mink/cli.py:_dispatch_runs` (`runs list/show/share`) | Per-run eventlog dirs at `~/.chimera/eventlog/mink-<id>/`. `runs share` supports `file`, `gist`, `base64` sinks (#129). |
| 18 | Agents subcommand | GREEN | `chimera/mink/agents.py` + `chimera/mink/cli.py:_dispatch_agents` (`agents list/show <name>`) | Walks the same project > user > built-in chain `--agent <name>` resolves through. Read-only; no provider bring-up. |
| 19 | Allowed-tools filter | GREEN | `chimera/mink/cli.py:_filter_allowed_tools` (`--allowed-tools Bash,Read,...`) | Comma-separated, case-insensitive; unknown name exits 2 with the valid tool list (audit M-22). |
| 20 | Stream-json output[^streamjson] | GREEN | `chimera/cli/output_format.py` + `_run_stream_json` | One JSON line per `LoopEvent` with `RedactionMiddleware` applied; pairs with `--no-save` for sensitive prompts. |

[^remote]: Tracking issue: [#127](https://github.com/0bserver07/chimera/issues/127). Scaffold landed; needs asyncssh + SFTP + ProxyJump per `research/mink/HANDOFF.md` open-issues table.

[^share]: Tracking issue: [#129](https://github.com/0bserver07/chimera/issues/129) (closed in v0.4.0).

[^streamjson]: Redaction shipped via `RedactionMiddleware` in v0.4.0 (PR [#140](https://github.com/0bserver07/chimera/pull/140)).

## Built-in tools roll-up (41 reference tools surveyed)

| Bucket | Status | Coverage |
|--------|--------|----------|
| File ops (Read/Write/Edit/Glob/Grep/NotebookEdit/LSP) | YELLOW | 7 / 7 implemented; NotebookEdit not in `AGENT_TOOLS` default set (caller must add it explicitly). |
| Web (WebFetch/WebSearch) | GREEN | 2 / 2 |
| Task management (5) | GREEN | 5 / 5 (TaskOutput streaming via `task_tools.TaskOutputTool`) |
| Cron (Create/List/Delete) | YELLOW | 3 / 3 implemented; not in `AGENT_TOOLS` default set. |
| Shell (Bash/PowerShell) | GREEN | 2 / 2 (M5-C `PowerShellTool`) |
| Config (Config/Brief) | YELLOW | 1 / 2 (Config shipped; Brief deferred — niche) |
| MCP (MCPTool/ListMcpResources/ReadMcpResource/McpAuth) | GREEN | 4 / 4 (M5-A) |
| Coordination (Agent/Skill/EnterWorktree/ExitWorktree/EnterPlanMode/ExitPlanModeV2/TodoWrite/ToolSearch) | YELLOW | 8 / 8 implemented; EnterWorktree / ExitWorktree not in `AGENT_TOOLS` default set. |
| Collaboration (AskUser/SendMessage/RemoteTrigger/Team*) | YELLOW | 2 / 5 (AskUser, SendMessage shipped; RemoteTrigger + Team* deferred) |
| Testing/debugging (TestingPermission/SyntheticOutput/TaskOutput) | YELLOW | 1 / 3 (TaskOutput shipped) |

> Note (audit M-21): "implemented but not in `AGENT_TOOLS`" tools
> (NotebookEdit, EnterWorktree/ExitWorktree, Cron tools) are dispatchable
> when added explicitly to an `Agent` constructor's `tools=` list, but
> the live agent served by `chimera mink` does NOT see them by default.
> To enable them for a session, register them manually or extend
> `chimera/core/tool_group.py:AGENT_TOOLS`.

## Counts

- **GREEN:** 17 of 20 subsystems · 32 of 41 reference built-in tools.
- **YELLOW:** 3 of 20 subsystems · 5 of 41 tools (partial coverage).
- **RED:** 0 subsystems · 4 of 41 tools deferred (Brief, RemoteTrigger, TeamCreate, TeamDelete, TestingPermission, SyntheticOutput).

> NOTE (M7, 2026-04-25): rows 16–20 were added to reflect surfaces that
> shipped in v0.4.0 but were missing from the original 15-row matrix.
> Rows 1–15 are unchanged; row 13 was relabeled from "Kimi K2.6" to
> "Kimi K2.6 / GLM-5" to match the v0.3.0 default model switch.

## Chimera-only capabilities (do not regress)

Carried over from gap-analysis §"Chimera-only capabilities". Re-verified shipped:
cooperative `CancellationToken`, mid-turn `MessageQueues` steering, loop detection,
ghost commits, instruction anchor, learning store, discipline guards,
`EventSourcedSession` crash recovery, `FileAwareCompaction`, `SessionTree` in-place
branching, `RedactionMiddleware`, `CostTracker` with cache/reasoning breakdown,
checkpoint manager, `/yolo` mode toggle, unified `LoopConfig`, 26-event `EventBus`,
`AgentConfig.from_markdown()` + multi-tier registry.

## Follow-up issues to file (or already filed)

1. **Vision content blocks for Ollama provider** — currently deferred (subsystem 13).
2. **`format` (JSON-schema grammar) for Ollama** — Ollama Cloud limitation; revisit when upstream supports `:cloud` tags.
3. **Brief tool** — niche, defer until a user requests it.
4. **RemoteTrigger tool** — overlaps with `web_fetch`; decide if standalone tool is worth the duplicate surface.
5. **Team{Create,Delete} tools** — wait for `agent_teams` to graduate from experimental.
6. **TestingPermissionTool / SyntheticOutputTool** — internal reference-only utilities; not user-facing.
7. **`/status` and `/doctor` parity polish** — slash commands shipped; flesh out diagnostics output to match the reference verbosity.
8. **SSH transport hardening** — [#127](https://github.com/0bserver07/chimera/issues/127): asyncssh + SFTP + ProxyJump on top of the v0.4.0 `SSHEnvironment` scaffold.
9. **Event sourcing completeness** — [#128](https://github.com/0bserver07/chimera/issues/128): A6 audit found 9/18 spec items missing.
10. **Benchmark adapter live runs** — [#86–#96](https://github.com/0bserver07/chimera/issues/86): 11 adapters scaffolded; need real-dataset wiring + scoring.
11. **`runs cost --since=Nd`** — telemetry/cost dashboard aggregating `summary.json` across `~/.chimera/eventlog/mink-*/`. Not yet shipped; tracked under HANDOFF "Suggested next session" #6.

## How to use

When a user installs `chimera mink` and runs it from a project that
already contains a `.claude/settings.json`, every GREEN row above is
expected to behave identically to the reference implementation. The
settings file is auto-discovered from the project root (no `--config`
flag is needed — that flag does not exist; see audit L-1). To override
the discovery path set `CHIMERA_MINK_SETTINGS_PATH` or pass `--cwd` to
point at a different project. YELLOW rows degrade gracefully (and emit
a warning where the gap is user-visible). RED rows raise
`NotImplementedError` with a pointer to the follow-up issue.
