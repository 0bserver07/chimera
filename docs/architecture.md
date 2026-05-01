# Chimera Architecture

Chimera is composed from eight phases of agent infrastructure. Each phase is
a self-contained set of modules with a defined public surface, and each of
the seven coding-agent CLIs (`mink`, `otter`, `ferret`, `weasel`, `shrew`,
`stoat`, `badger`) is assembled by picking from those phase primitives.
Phases are independent enough to use on their own, but they were designed
so that the dependency edges flow in one direction: a higher phase may
reach into a lower one, never the reverse.

This page is the live phase-to-code map. If you are looking for the older
"8-layer stack" view (CLI / Workflows / Synthesis / Evaluation / Agent /
Provider / Infrastructure / Environment), the eight phases are an
orthogonal slicing of the same codebase along the agent-loop axis rather
than the synthesis-pipeline axis. The two views are complementary, not
competing — see the [old layer view](#appendix-the-8-layer-stack) at the
bottom of this page.

## Why phases at all

The seven coding-agent CLIs share one library. Earlier versions of
Chimera tried to keep everything as horizontal "layers" — one giant
loop, one giant context manager, one giant permission system. That
worked while there was one CLI. Once `mink` and `otter` shipped side
by side, the seams started to show: features that needed to go into
the loop AND the session AND the permission system kept landing in
three different layers at once. The phases collect those features by
their *cross-cutting concern* (loop turns, sub-agent isolation,
persistence, gating, prompting, lifecycle hooks, command surface,
production wiring, snapshotting), so a feature lands in one place and
every CLI inherits it.

If you are reading this to figure out where to add code, the rule of
thumb is: pick the phase whose contract your change touches, not the
CLI it shows up in first.

## Phase overview

```
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 7: Command + Skill surface                          │
    │   slash commands, skills, flow workflows                  │
    └──────────────────────────────────────────────────────────┘
                             │
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 6: Hook system                                      │
    │   PreToolUse / PostToolUse / lifecycle / async registry   │
    └──────────────────────────────────────────────────────────┘
                             │
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 5: System prompt + context assembly                 │
    │   layered prompts, cache-safe params, tool deferral       │
    └──────────────────────────────────────────────────────────┘
                             │
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 4: Permission system                                │
    │   modes, rules, denial tracking, sandbox adapter          │
    └──────────────────────────────────────────────────────────┘
                             │
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 3: State + persistence                              │
    │   eventlog, snapshots, content replacement, file cache    │
    └──────────────────────────────────────────────────────────┘
                             │
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 2: Sub-agent architecture                           │
    │   isolation tiers, spawner, task manager, builtin agents  │
    └──────────────────────────────────────────────────────────┘
                             │
    ┌──────────────────────────────────────────────────────────┐
    │ Phase 1: Core loop                                        │
    │   AgentLoop async generator, streaming executor, recovery │
    └──────────────────────────────────────────────────────────┘

    Phase 8: Production infrastructure   ── threads through all phases ──
      auth, secrets, streaming, feature flags, MCP / LSP / ACP, plugins

    Phase 9: Snapshot / format / patch   ── plugs into phases 1, 3, 4 ──
      undo, auto-formatter, structured multi-file patching
```

The dependency edges are real. Phase 2 needs Phase 1's `AbortSignal`
to give sub-agents linked-but-cancellable children. Phase 4's permission
checks fire from inside Phase 1's tool-execution phase. Phase 5's
prompt builder feeds Phase 1's `AgentLoop.run()`. And so on. Phases 8
and 9 are cross-cutting: Phase 8 wires production concerns (auth,
analytics, MCP lifecycle) into every other phase, and Phase 9 plugs
snapshot/format/patch into the loop turn boundary, the persistence
layer, and the permission gate.

---

## Phase 1 — Core Loop

**Purpose.** Drive a tool-augmented LLM agent with streaming, concurrent
tool execution, multi-stage error recovery, and cooperative cancellation.
This is the engine every other phase plugs into.

**Status.** Shipped as the default loop in `CodingAgent`. The legacy
synchronous `ReAct` loop is preserved alongside for back-compat with
callers that still use `Agent.run()` directly.

**Key modules.**
- `chimera/core/agent_loop.py` — `AgentLoop`, the async-generator core loop
- `chimera/core/loop.py` — legacy synchronous `ReAct` loop (back-compat)
- `chimera/core/loop_events.py` — `LoopEvent`, `LoopEventType`, `LoopResult`
- `chimera/core/loop_state.py` — `LoopState`, `QuerySource`, `RETRY_POLICIES`
- `chimera/core/loop_deps.py` — `LoopDeps`, `production_deps()`
- `chimera/core/streaming_executor.py` — `StreamingToolExecutor` with concurrency flags
- `chimera/core/recovery.py` — `ErrorRecovery`, `WithheldError`, multi-stage recovery
- `chimera/core/abort.py` — `AbortSignal` with linked children
- `chimera/core/tool_executor.py` — shared tool-execution path with permission/event/audit hooks
- `chimera/core/tool.py` — `BaseTool` plus the `is_concurrency_safe` / `is_read_only` / `is_destructive` flags

**Public API.**
```python
from chimera.core.agent_loop import AgentLoop
from chimera.core.abort import AbortSignal
from chimera.core.loop_state import QuerySource

loop = AgentLoop()
async for event in loop.run(
    messages=[...], tools=[...], provider=..., system_prompt=...,
    abort_signal=AbortSignal(), query_source=QuerySource.FOREGROUND,
):
    handle(event)
```

**Notes.** Tools default to `is_concurrency_safe = False` (fail-closed).
Read-only tools (`read`, `glob`, `grep`, `list_files`) opt in. The recovery
ladder is `context-collapse → reactive-compact → escalate-output → multi-turn`.

---

## Phase 2 — Sub-Agent Architecture

**Purpose.** Spawn sub-agents with three-tier context isolation so a child
agent cannot corrupt parent state, but infrastructure (background tasks,
hooks) can still reach the root session. Every multi-agent workflow lives
on top of this.

**Status.** Shipped. `composition/` (Pipeline, Ensemble, Supervisor)
re-implements its primitives via `AgentSpawner`. Built-in agents
(`general-purpose`, `explore`, `plan`) are registered out of the box.

**Key modules.**
- `chimera/core/agent_context.py` — `AgentContext`, `IsolationLevel`
- `chimera/core/agent_spawner.py` — `AgentSpawner.spawn()`
- `chimera/core/agent_definition.py` — `AgentDefinition` and the on-disk loader
- `chimera/core/builtin_agents.py` — `BUILTIN_AGENTS`
- `chimera/core/task_manager.py` — `BackgroundTask`, `TaskManager`
- `chimera/core/auto_background.py` — auto-promote long-running foreground agents
- `chimera/agents/` — `AgentConfig`, `AgentRegistry`, `AgentLoader`, presets
- `chimera/agents/dispatch/` — request classifier, trigger router, force-routes
- `chimera/composition/` — Pipeline / Ensemble / Supervisor on top of the spawner
- `chimera/tools/agent_tool.py` — the model-facing `agent` tool
- `chimera/tools/task_tools.py` — `TaskCreate`, `TaskGet`, `TaskList`, `TaskStop`, `TaskOutput`

**Public API.**
```python
from chimera.core.agent_context import AgentContext, IsolationLevel
from chimera.core.agent_spawner import AgentSpawner

spawner = AgentSpawner(...)
async for event in spawner.spawn(definition, prompt, parent_ctx,
                                 isolation=IsolationLevel.FULL):
    ...
```

**Notes.** Sub-agents get a per-agent denial counter (so "deny three
times → stop asking" works inside a sub-agent's own scope). The
`set_app_state_for_tasks` callback bypasses isolation so background bash
tasks can register with the root task manager.

---

## Phase 3 — State + Persistence

**Purpose.** Long conversations don't blow up the context window, sessions
resume losslessly, and prompt-cache hit rates stay high. Solved with the
content-replacement state machine (large tool results spill to disk, model
sees a preview), file-state caching for read deduplication, JSONL transcripts
for sub-agent sidechains, and an event-sourced session store with snapshots.

**Status.** Shipped. The default `EventSourcedSession` backend has both a
JSONL-on-disk implementation and a side-car SQLite store with snapshot-driven
fast resume.

**Key modules.**
- `chimera/sessions/eventlog/` — append-only event log with file locking, gap detection, crash recovery
- `chimera/sessions/eventlog/session.py` — `EventSourcedSession`, snapshot integration, `with_sqlite()` helper
- `chimera/events/sourcing/sqlite_store.py` — `SqliteEventStore` (SQLite + WAL + per-aggregate seq + snapshot table)
- `chimera/events/sourcing/projector.py` — projector registry and replay
- `chimera/sessions/session.py` — `Session` (chat / iter_chat / fork / save / resume / steer / queue / cancel)
- `chimera/sessions/tree.py` — `SessionTree` with in-place branching via `parent_id`
- `chimera/sessions/storage/` — Memory / File / SQLite backends
- `chimera/core/content_replacement.py` — `ContentReplacementState`, persisted-vs-inline decisions
- `chimera/core/file_state_cache.py` — file-state LRU for read deduplication
- `chimera/core/file_tracker.py` — `FileTracker` across compaction boundaries
- `chimera/core/tool_result_persister.py` — disk persistence for oversized tool results
- `chimera/compaction/` — `SummaryCompaction`, prune / counter / composite, threshold-based atomic compaction

**Public API.**
```python
from chimera.sessions.eventlog.session import EventSourcedSession

session = EventSourcedSession.with_sqlite(
    sqlite_path="~/.chimera/eventlog/run.sqlite",
    snapshot_every_n_events=50,
)
```

**Notes.** Once a `tool_use_id` is replaced (persisted-to-disk), the
decision is frozen — this is critical for prompt-cache stability across
turns. Snapshots let resume start from `snapshot.seq + 1` instead of
seq=1; the side-car mode keeps the journal authoritative and the snapshot
purely for fast-forward.

---

## Phase 4 — Permission System

**Purpose.** Gate tool execution with multi-source rules, five permission
modes, denial tracking with fallback-to-prompting, and a sandbox adapter
for shell execution. Bypass-immune safety checks make sure that even
`bypass_permissions` mode can't run truly destructive operations
unannounced.

**Status.** Shipped. All seven CLIs honor `~/.claude/settings.json` rules;
ferret adds an OS-level sandbox layer on top via `--sandbox` × `--approval`
preset composition.

**Key modules.**
- `chimera/permissions/base.py` — `ApprovalPolicy`, `PermissionPolicy`
- `chimera/permissions/modes.py` — `PermissionMode` enum (default / plan / accept_edits / bypass / dont_ask / auto)
- `chimera/permissions/rule.py`, `rules.py`, `patterns.py` — `PermissionRuleValue`, `ToolName(content)` parsing
- `chimera/permissions/checker.py` — decision algorithm
- `chimera/permissions/loader.py` — multi-source rule loading with precedence
- `chimera/permissions/denial_tracking.py` — per-agent denial counter
- `chimera/permissions/presets.py` — read-only / auto / full (ferret-style)
- `chimera/permissions/sandbox.py` — sandbox adapter
- `chimera/permissions/risk.py` — risk classifier for bash patterns
- `chimera/permissions/audit.py` — `AuditEntry`, `AuditLog`
- `chimera/permissions/interactive.py` + `chimera/permissions/prompt_handler.py` — interactive approval handlers
- `chimera/cli/permission_prompt.py` — REPL prompt UI

**Public API.**
```python
from chimera.permissions import (
    PermissionMode, PermissionRuleValue, AllowList, AlwaysDeny,
)
rule = PermissionRuleValue.from_string("bash(git *)")
policy = AllowList([rule])
```

**Notes.** Settings discovery is project-root → user-global → built-in.
Ferret's two-flag composition is implemented as the cross product of
`PermissionMode` (policy gate) and a sandbox adapter (OS gate); a tool
call has to pass both.

---

## Phase 5 — System Prompt + Context

**Purpose.** Layered prompt construction so the cache prefix stays stable
across turns, agent-specific overrides for sub-agents, API-based token
estimation with fallback, and tool deferral (lazy-load tools by name) to
keep the initial prompt small even when the registry is large.

**Status.** Shipped. The `Prompt` template class is preserved for back-compat
callers; new code uses `SystemPromptBuilder`.

**Key modules.**
- `chimera/core/prompt.py` — `Prompt` class with `{{variable}}` template substitution (legacy)
- `chimera/core/system_prompt.py` — `PromptLayer`, `SystemPrompt`, `SystemPromptBuilder` (cache-segmented layers)
- `chimera/core/prompt_template.py` — additional template helpers
- `chimera/core/context_assembler.py` — assemble project-context layer (git status, project rules)
- `chimera/core/cache_safe_params.py` — frozen request params for forked agents
- `chimera/core/tool_deferral.py` — `ToolSearchTool` lazy registry
- `chimera/core/token_estimator.py` — API-first, fallback-on-error token counting
- `chimera/core/token_budget.py` — budget enforcement
- `chimera/core/context.py` — conversation history manager
- `chimera/core/memory.py` — `CLAUDE.md` / `AGENTS.md` ingestion (per-CLI rules)

**Public API.**
```python
from chimera.core.system_prompt import SystemPromptBuilder

prompt = (SystemPromptBuilder()
    .add_layer("default", DEFAULT_SYSTEM, cacheable=True)
    .add_layer("agent_override", AGENT_DESC, cacheable=True)
    .add_layer("user_append", USER_RULES, cacheable=False)
    .build())
```

**Notes.** Each CLI ingests its own rule files (`~/.claude/CLAUDE.md`,
`AGENTS.md`, `.codex/AGENTS.md`, etc.) into the user-append layer; the
default and agent-override layers stay cacheable so the cache prefix
doesn't move when a user edits a project rule file mid-session.

---

## Phase 6 — Hook System

**Purpose.** Event-driven lifecycle hooks — pre/post tool, permission
events, session start/end, sub-agent start/stop, compaction boundaries,
notifications — that can be implemented as shell commands, LLM prompts,
or function callbacks. Hooks load from settings, plugins, and skill
directories with priority ordering.

**Status.** Shipped with five built-in hooks plus a fully-async registry.

**Key modules.**
- `chimera/hooks/events.py` — `HookEvent` enum (27 lifecycle events)
- `chimera/hooks/emitter.py` — fire-and-collect hook event dispatcher
- `chimera/hooks/executor.py` — three executors (shell / prompt / function)
- `chimera/hooks/async_registry.py` — async hook registry with timeout / progress
- `chimera/hooks/loader.py` — multi-source hook loading
- `chimera/hooks/session_hooks.py` — session-scoped hooks
- `chimera/hooks/hook_types.py` — typed inputs and response schemas
- `chimera/hooks/hooks.json` — bundled defaults
- `chimera/hooks/auto_test.py`, `auto_lint.py`, `validate_path.py`, `security_scan.py`, `verify_done.py`, `loop_detector.py`, `file_watcher.py` — bundled hook scripts
- `chimera/core/middleware.py` — `MiddlewareChain` (before_model / after_model / after_agent) for in-process hooks

**Public API.**
```python
from chimera.hooks.events import HookEvent
from chimera.hooks.async_registry import AsyncHookRegistry

reg = AsyncHookRegistry()
reg.register(HookEvent.POST_TOOL_USE, my_callback)
```

**Notes.** Hooks support `updatedInput` mutation: a `PreToolUse` hook can
rewrite the tool input before execution. Audit-trail and cwd / env
inheritance landed in M2-B.

---

## Phase 7 — Command + Skill System

**Purpose.** Slash commands for the user (REPL palette), skills (markdown
files with YAML frontmatter that the model can invoke), and a SkillTool
bridge so the model can call commands directly. Skills support inline
context (run in current loop) or forked context (spawn sub-agent).

**Status.** Shipped. The shared registry is in `chimera/cli/slash_commands.py`;
each CLI selects which subset to expose. Skills auto-discover from
`<project>/skills/`, `~/.chimera/skills/`, and bundled defaults.

**Key modules.**
- `chimera/skills/discovery.py` — `discover_skills()`, walks SKILL.md files
- `chimera/skills/loader.py` — `SkillLoader`
- `chimera/skills/definition.py` — `SkillDefinition`
- `chimera/skills/bundled.py` — bundled built-ins
- `chimera/skills/flow.py`, `flow_parser.py`, `flow_executor.py` — Mermaid flowchart workflows
- `chimera/cli/slash_commands.py` — shared REPL command registry
- `chimera/commands/` — `CommandBase`, `PromptCommand`, `LocalCommand`, registry, processor
- `chimera/cli/code.py`, `chimera/mink/cli.py`, `chimera/otter/cli.py`, etc. — per-CLI command surfaces

**Public API.**
```python
from chimera.commands.registry import CommandRegistry
from chimera.skills.discovery import discover_skills

skills = discover_skills(["~/.chimera/skills/", "skills/"])
```

**Notes.** Each CLI shows only its slash subset (mink: 19, otter: 26,
weasel: 7 on purpose, badger: shared + `/parity` + `/rerun`, stoat:
shared + `/shell`). Skills can register hooks at invocation time so a
skill becomes a small bundle of "command + hooks + system prompt
override" in one file.

---

## Phase 8 — Production Infrastructure

**Purpose.** The cross-cutting plumbing that turns a prototype into a
production tool: feature flags for build-time elimination, analytics
with PII redaction, MCP / LSP / ACP server lifecycle, IDE bridge,
auth (API key, OAuth device, OAuth browser, credential store with
0o600 perms), secret detection and redaction, streaming output, plugin
system with marketplace, and coordinator mode for multi-agent
orchestration.

**Status.** Shipped piece by piece. MCP, LSP, ACP, plugins, auth, and
secrets are all wired into the default `CodingAgent` build.

**Key modules.**
- `chimera/auth/` — `manager.py`, `api_key.py`, `oauth.py`, `oauth_device.py`, `store.py` (file-based, 0o600)
- `chimera/secrets/` — `registry.py` (env-var loading), `detector.py` (10 built-in patterns), `redactor.py` (event bus middleware)
- `chimera/security/` — `risk.py`, `analyzer.py` (rule / LLM / composite), `policy.py` (`ConfirmationPolicy` variants)
- `chimera/streaming/` — `base.py`, `handlers.py`, `loop.py`, `protocol.py`
- `chimera/core/feature_flags.py` — `FeatureFlags.enabled()`, env-var overrides
- `chimera/analytics/` — analytics with PII redaction
- `chimera/mcp/` — MCP client (stdio / HTTP / SSE / WS), OAuth, lifecycle
- `chimera/lsp/` — LSP client, diagnostics, completion, rename
- `chimera/acp/` — Agent Client Protocol (JSON-RPC 2.0 over stdio)
- `chimera/bridge/` — IDE bridge protocol
- `chimera/plugins/` — `BasePlugin`, `PluginManager`, `PluginExtensionRegistry`, `DirectoryPluginLoader`, marketplace
- `chimera/coordinator/` — multi-agent coordinator mode
- `chimera/server/` — HTTP+SSE server (drives `otter serve --port` and `ferret serve --http`)
- `chimera/wire/` — `WireMessage`, request/response, approval requests, status updates
- `chimera/rpc/` — JSON-RPC server (stdin/stdout) and command/response/event types
- `chimera/providers/cost.py` + `cost_tracker.py` — per-model pricing, granular token tracking
- `chimera/learning/` — observation store (SQLite + FTS5), confidence tracking, error feedback

**Public API.** This phase has no single entry point — it's the
collection of cross-cutting modules every other phase reaches into.
The clearest seam is `chimera/assembly/coding_agent.CodingAgent`,
which wires production infrastructure into a single buildable
agent: `CodingAgent(model=..., preset='claude_code')`.

**Notes.** Feature flags read `CHIMERA_FEATURE_<NAME>=1` from the
environment. Secrets pre-empt the event bus before logs / transcripts
are persisted. The plugin marketplace is a directory loader plus an
HTTP-fetched index.

---

## Phase 9 — Snapshot, Auto-Format, Structured Patch

**Purpose.** Three safety / quality features that span phases 1, 3, and 4:
turn-boundary file snapshots so users can undo agent edits to any prior
turn, auto-formatter integration that runs `prettier` / `ruff` / `gofmt`
after every file write, and a structured multi-file patch format with
fuzzy matching that handles multi-file edits in one tool call.

**Status.** Shipped. Snapshots integrate with the JSONL transcript so a
session can be reverted and resumed cleanly. Auto-format runs as a
`PostToolUse` hook on file-write tools. The structured patch parser
ships alongside the legacy edit formats (`whole-file`, `search-replace`,
`diff`, `udiff`) for callers that want the GPT-family-emitted format.

**Key modules.**
- `chimera/core/snapshot.py` — `SnapshotManager`, `Snapshot`, shadow-git plumbing with file-copy fallback
- `chimera/checkpoints.py` — high-level `CheckpointManager` (create / restore_by_name / restore_by_id / undo / list)
- `chimera/checkpoints_ghost.py` — ghost-checkpoint helpers
- `chimera/core/auto_format.py` — `AutoFormatter`, formatter detection (`prettier`, `ruff`, `gofmt`, `rustfmt`, …)
- `chimera/core/patch_parser.py` — `PatchParser`, `FilePatch`, `PatchHunk`, fuzzy-match passes
- `chimera/core/proposed_edit.py` — `ProposedEdit` for review-before-apply flows
- `chimera/core/file_mutation_queue.py` — serialize file-mutation tools
- `chimera/tools/edit_formats.py` — Aider-style edit formats
- `chimera/transactions.py` — file-transaction coordinator

**Public API.**
```python
from chimera.core.snapshot import SnapshotManager
from chimera.checkpoints import CheckpointManager

snap = SnapshotManager(project_dir=Path.cwd())
await snap.take(turn=N, modified_files=[...])
await snap.revert(to_turn=N - 2)
```

**Notes.** Snapshots use shadow git (a separate `.git`-style directory
outside the user's repo) when git is available, falling back to direct
file copies. Auto-format runs after the file write completes but before
the `POST_TOOL_USE` hook fires, so downstream hooks see the formatted
file.

---

## How the seven CLIs compose from these phases

Each CLI is a thin argparse + small per-CLI defaults file over the
shared library. The CLI picks which phase primitives to enable by
default, which to disable, and which to override.

```
chimera/<cli>/cli.py            ← argparse + per-CLI defaults
                ↓
chimera/assembly/coding_agent.CodingAgent
                ↓
chimera/core/agent_loop.AgentLoop  +  chimera/core/tool_group.create_default_tools(...)
                ↓
chimera/providers/factory.create_provider(...)
                ↓
chimera/sessions/eventlog        (file locking, gap detection, crash recovery)
                ↓
~/.chimera/eventlog/<cli>-<utc>-<uuid>/
```

| CLI | Phase 1 (loop) | Phase 2 (sub-agents) | Phase 3 (sessions) | Phase 4 (permissions) | Phase 5 (prompt rules) | Phase 6 (hooks) | Phase 7 (commands) | Phase 8 (production) | Phase 9 (snapshot) |
|---|---|---|---|---|---|---|---|---|---|
| **mink** | `AgentLoop`, `max_steps=50` | full (Task tool, agent-teams) | eventlog + sqlite | `~/.claude/settings.json` rules | `~/.claude/CLAUDE.md` ingest | full hook registry | 19 slash commands | full | snapshot + auto-format |
| **otter** | `AgentLoop`, `max_steps=50` | full | eventlog + sqlite | `~/.claude/settings.json` rules | rules + share | full | 26 slash commands | + HTTP+SSE server, ACP serve | snapshot + auto-format |
| **ferret** | `AgentLoop`, `max_steps=50` | full | eventlog + sqlite | sandbox × approval presets | `.codex/AGENTS.md` ingest | full + sandbox hooks | otter + `/sandbox`, `/approval`, `/bridge` | + sandbox adapter, cloud bridge | snapshot + auto-format |
| **weasel** | `AgentLoop`, `max_steps=50` | by design **off** | eventlog + sqlite | host-defined (no presets) | `.weasel/extensions/` | restricted | 7 slash commands (sparse) | + RPC mode, embedded SDK | snapshot + auto-format |
| **shrew** | `AgentLoop`, `max_steps=30` | inherited from weasel | eventlog + sqlite | weasel + restricted-tool default | `chimera/shrew/skills/` + `~/.shrew/skills/` | restricted | weasel parity | + bench harness | snapshot + auto-format |
| **stoat** | `AgentLoop`, `max_steps=50` | full | eventlog + sqlite | `~/.claude/settings.json` | rules + Kimi tuning | full | shared + `/shell` (mode toggle) | + Moonshot provider chain | snapshot + auto-format |
| **badger** | `AgentLoop`, `max_steps=25` | full | eventlog + sqlite | shared + harness-tight defaults | shared | full + parity hook | shared + `/parity`, `/rerun` | + parity-tracker subcommand | snapshot + auto-format |

The takeaway: every CLI inherits all nine phases. The differences are in
defaults (step budget, tool restriction, model chain), which slash
commands are exposed, and which Phase-8 transports the CLI fronts
(HTTP+SSE for otter, ACP-default for ferret, RPC + SDK for weasel,
shell-mode toggle for stoat, parity tracker for badger).

For the per-CLI quickstart and parity row, see each CLI's docs:

- [Mink quickstart](mink/quickstart.md) — TUI-first
- [Otter quickstart](otter/quickstart.md) — server-first / multi-client
- [Ferret quickstart](ferret/quickstart.md) — IDE-flagship sandbox-first
- [Weasel quickstart](weasel/quickstart.md) — minimal harness, four modes
- [Shrew quickstart](shrew/quickstart.md) — small-local-model
- [Stoat quickstart](stoat/quickstart.md) — shell-mode toggle
- [Badger quickstart](badger/quickstart.md) — harness-rewrite discipline

For the side-by-side surface comparison and provider-chain matrix, see
[Coding Agents Overview](coding-agents.md).

---

## Appendix: the 8-layer stack

For callers used to the older Chimera mental model — eight horizontal
layers (CLI / Workflows / Synthesis / Evaluation / Agent / Provider /
Infrastructure / Environment) — the relationship between the two views
is:

- **Layer 1 (Environment)** maps roughly to Phase 8's environment
  abstractions (`chimera/env/`), but environments are referenced by
  every phase that touches files or shells.
- **Layer 2 (Infrastructure)** is split across Phases 3, 4, 6, and 8
  depending on which cross-cutting concern the module addresses.
- **Layer 3 (Provider)** is Phase 8 (production wiring), referenced by
  Phase 1 (the loop calls `provider.stream()`).
- **Layer 4 (Agent)** is the union of Phases 1, 2, and 5.
- **Layer 5 (Evaluation)** is its own thing: `chimera/eval/`,
  `chimera/benchmarks/`, used by training-side workflows. Sits next to
  the phase stack rather than inside it.
- **Layer 6 (Synthesis)** is the training pipeline (`chimera/training/`,
  `chimera/synthesize.py`). Also sits next to the phase stack.
- **Layer 7 (Workflows)** uses the phase stack from outside —
  `CIFixWorkflow`, `ReviewOrchestrator`, `Researcher`, `MigrationPlanner`,
  `DocGenerator`, `TestGenerator` are all built on top of Phase 1's
  `AgentLoop` plus their own domain-specific parsing.
- **Layer 8 (CLI)** is the seven coding-agent CLIs above, plus the
  top-level `chimera synthesize / eval / bench / code / review / ci-fix /
  research / docs / testgen / migrate / plugins` subcommands.

When in doubt, prefer the phase view for changes that touch the agent
loop / tool-execution path, and prefer the layer view for changes that
touch the synthesis or evaluation pipelines.

## See also

- [Coding Agents Overview](coding-agents.md) — comparative tour of the seven CLIs
- [Build Your Own Agent](playbooks/08-building-agents.md) — full developer library guide
- [All Playbooks](playbooks/) — 13 end-to-end recipes
- [Benchmarks](benchmarks/README.md) — transparency framework, raw data
