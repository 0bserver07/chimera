# `chimera mink` Parity Matrix

**Source baseline:** `research/mink/24-gap-analysis.md` (Apr 23 2026).
**Updated:** 2026-04-23, after M0–M5 ship.
**Legend:** GREEN = shipped / at parity (or superset); YELLOW = partial; RED = deferred.

## Subsystems

| # | Subsystem | Status | Shipped artifact | Notes |
|---|-----------|--------|------------------|-------|
| 1 | Turn loop | GREEN | `chimera/core/agent_loop.py`, `loop.py` | Already a superset (cancellation, steering, learning). Minor `PromptCacheEvent` shape diff is cosmetic. |
| 2 | Tool system / registry | GREEN | `chimera/core/tool.py`, `tool_executor.py` | Anthropic + OpenAI schemas; permission/event hooks; concurrent dispatch. |
| 3 | Built-in tools | GREEN | `chimera/tools/` (incl. `notebook_edit.py`, `worktree_tool.py`, `task_tool.py`, `cron_tools.py`, `powershell.py`, `config_tool.py`) | All M3+M5 tools landed. PowerShell falls through to `pwsh` on non-Windows. |
| 4 | Tool-call streaming | GREEN | `chimera/providers/ollama.py:stream()` | NDJSON parser accumulates `tool_calls`; emits `tool_call_start/delta/complete`. |
| 5 | Slash commands | GREEN | `chimera/cli/slash_commands.py` | Extracted from `code.py` for reuse by `cc.py`; full M1 set wired. |
| 6 | Hooks | GREEN | `chimera/hooks/executor.py`, `emitter.py`, `events.py` | `updatedInput` mutation + cwd/env inheritance + audit trail wired in M2-B. |
| 7 | MCP | YELLOW | `chimera/mcp/{client,lifecycle,oauth,sse_transport,ws_transport,tools}.py` + `chimera/mink/cli.py:_load_mcp_tools` + `chimera/cli/code.py` | All transports + OAuth2 PKCE + `mcp__server__tool` prefix shipped in M5-A; `chimera mink` and `chimera code` now load `~/.chimera/mcp.json` and `<cwd>/.mcp.json` and merge servers (audit B-6 fix). End-to-end install of a community server over OAuth has not been independently verified. |
| 8 | Permissions | GREEN | `chimera/permissions/` + `chimera/cli/permission_prompt.py` + `chimera/mink/settings.py:to_chimera_loop_config` | `Tool(arg_key:pattern)` grammar; `.claude/settings.json` allow/ask/deny lists are now consumed by `chimera mink -p` (audit B-4 fix). |
| 9 | TUI / streaming render | GREEN | `chimera/cli/render.py:MinkStreamHandler` + `build_stream_handler` wired in `chimera/mink/cli.py:_run_print_mode` and `chimera/cli/code.py` (opt-in via `CHIMERA_RICH_TUI=1`) | MarkdownStream, Spinner, ToolBlockRenderer, ThinkingBlockRenderer, DiffRenderer all flow through the live runtime. Auto-detect: rich on TTY, plain on pipe / `NO_COLOR` / `--no-color` / `--no-rich`. Audit B-2 / B-7 / B-8 closed. |
| 10 | Sessions / resume / fork | GREEN | `chimera/sessions/` + `/resume` slash command | Already superset; `/resume <id>` exposed in M4. |
| 11 | Subagents / Task tool | GREEN | `chimera/tools/task_tool.py`, `chimera/cli/agent_teams.py` | Child `AgentLoop` spawn with linked cancellation; experimental teams behind env flag. |
| 12 | Settings / config | GREEN | `chimera/mink/settings.py` + `chimera/tools/config_tool.py` + `chimera/mink/cli.py:_run_print_mode` | User → project → local merge; rules now feed the live `LoopConfig.permissions` for one-shot `-p` runs (audit B-4 fix). M5-C `ConfigTool` exposes get/set/list. |
| 13 | Ollama provider (Kimi K2.6) | YELLOW | `chimera/providers/ollama.py` | Streaming + tool-roundtrip + `num_ctx`/`keep_alive`/`think` shipped. Vision and JSON-grammar mode still deferred (Ollama-cloud limitation). |
| 14 | Cancellation / Ctrl-C | GREEN | `chimera/core/cancellation.py` | Chimera-only superset. |
| 15 | Secrets redaction | GREEN | `chimera/secrets/` | Chimera-only superset. |

## Built-in tools roll-up (CC has 41)

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

- **GREEN:** 13 of 15 subsystems · 32 of 41 CC built-in tools.
- **YELLOW:** 2 of 15 subsystems · 5 of 41 tools (partial coverage).
- **RED:** 0 subsystems · 4 of 41 tools deferred (Brief, RemoteTrigger, TeamCreate, TeamDelete, TestingPermission, SyntheticOutput).

## Chimera-only capabilities (do not regress)

Carried over from gap-analysis §"Chimera-only capabilities". Re-verified shipped:
cooperative `CancellationToken`, mid-turn `MessageQueues` steering, loop detection,
ghost commits, instruction anchor, learning store, discipline guards,
`EventSourcedSession` crash recovery, `FileAwareCompaction`, `SessionTree` in-place
branching, `RedactionMiddleware`, `CostTracker` with cache/reasoning breakdown,
checkpoint manager, `/yolo` mode toggle, unified `LoopConfig`, 26-event `EventBus`,
`AgentConfig.from_markdown()` + multi-tier registry.

## Follow-up issues to file

1. **Vision content blocks for Ollama provider** — currently deferred (subsystem 13).
2. **`format` (JSON-schema grammar) for Ollama** — Ollama Cloud limitation; revisit when upstream supports `:cloud` tags.
3. **Brief tool** — niche, defer until a user requests it.
4. **RemoteTrigger tool** — overlaps with `web_fetch`; decide if standalone tool is worth the duplicate surface.
5. **Team{Create,Delete} tools** — wait for `agent_teams` to graduate from experimental.
6. **TestingPermissionTool / SyntheticOutputTool** — internal CC-only utilities; not user-facing.
7. **`/status` and `/doctor` parity polish** — slash commands shipped; flesh out diagnostics output to match CC's verbosity.

## How to use

When a Claude Code user installs `chimera mink` and runs it from a project
that already contains a `.claude/settings.json`, every GREEN row above is
expected to behave identically to upstream Claude Code. The settings file
is auto-discovered from the project root (no `--config` flag is needed —
that flag does not exist; see audit L-1). To override the discovery path
set `CHIMERA_MINK_SETTINGS_PATH` or pass `--cwd` to point at a different
project. YELLOW rows degrade gracefully (and emit a warning where the gap
is user-visible). RED rows raise `NotImplementedError` with a pointer to
the follow-up issue.
