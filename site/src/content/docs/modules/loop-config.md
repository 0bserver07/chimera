---
title: "LoopConfig"
description: "LoopConfig"
---

`chimera.core.loop_config.LoopConfig` is the single dataclass that
funnels every cross-cutting loop concern — permissions, detection,
events, audit, checkpoints, git workflow, cancellation, message
queues, file tracking, hooks, learning, discipline, condensation —
into one optional argument. Each loop variant
(`ReAct`, `AgentLoop`, `RetryLoop`, `PlanActLoop`, `LintFeedbackLoop`,
`PlanAndExecute`, `Reflexion`, `TreeOfThought`, `AutonomousLoop`)
accepts a `LoopConfig` and behaves identically when none is supplied.

## Quick Start

```python
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.permissions import PermissionRuleset, Rule, PermissionAction
from chimera.detection.actions import LoopDetector
from chimera.events.base import EventBus

config = LoopConfig(
    permissions=PermissionRuleset(
        rules=[
            Rule(tool_pattern="bash", action=PermissionAction.ASK),
            Rule(tool_pattern="read_file", action=PermissionAction.ALLOW),
        ],
        default=PermissionAction.ASK,
    ),
    detector=LoopDetector(),
    event_bus=EventBus(),
)

loop = ReAct(max_steps=50, config=config)
```

## Safety Defaults

`LoopConfig.__post_init__` installs a safe `Interactive()` permission
policy whenever `permissions` is `None` and `yolo_mode` is `False`. It
also auto-attaches a `RedactionMiddleware` to any provided `event_bus`
so secret-shaped strings never leak into subscribers.

Three opt-outs (all backwards-compatible):

| Opt-out | Effect |
|---------|--------|
| `yolo_mode=True` | Skips the default permission install; tools run freely |
| `permissions=AutoApprove()` | Any explicit policy short-circuits the default |
| `secrets_redaction=False` | Disables auto-wired redaction middleware |
| `CHIMERA_UNSAFE=1` env var | Disables ALL safety defaults process-wide (CI / internal use only) |

`_default_permissions_applied` is set to `True` when the policy was
installed by the dataclass; consumers (audit tooling, tests) may
inspect it but never set it manually.

## Fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `permissions` | `PermissionPolicy \| None` | `None` (auto-installs `Interactive()`) | Gates tool calls; supports declarative `PermissionRuleset` for fine-grained control |
| `detector` | `LoopDetector \| None` | `None` | Detects exact-repeat / pattern-cycle loops and warns or aborts |
| `compaction` | `CompactionStrategy \| None` | `None` | Strategy used when context approaches the budget |
| `handler` | `StreamHandler \| None` | `None` | Stream handler for token-by-token output |
| `event_bus` | `EventBus \| None` | `None` | Pub/sub for the 26+ lifecycle events |
| `auto_compact_threshold` | `float` | `0.8` | Trigger compaction when context tokens exceed this fraction of the model budget |
| `lsp` | `LSPManager \| None` | `None` | Per-language LSP feedback after edits |
| `cost_tracker` | `CostTracker \| None` | `None` | Per-step cache / reasoning / token cost ledger |
| `audit_log` | `AuditLog \| None` | `None` | Permission-decision audit trail |
| `checkpoint_manager` | `CheckpointManager \| None` | `None` | Named env snapshots for `/checkpoint` |
| `git_workflow` | `GitWorkflow \| None` | `None` | Branch-isolation + commit-strategy wrapper |
| `wire` | `Wire \| None` | `None` | Bidirectional `WireMessage` channel for IDEs / RPC clients |
| `middleware` | `list[LoopMiddleware] \| None` | `None` | Run-before-model / after-tool hooks |
| `truncation` | `TruncationConfig \| None` | `None` | Per-result truncation rules |
| `ghost_commits` | `GhostCommitManager \| None` | `None` | **W13-G5 file-undo** stack — auto-snapshots before every write/edit so `/undo` reverts files cleanly |
| `file_tracker` | `FileTracker \| None` | `None` | Records read/modified files across compaction boundaries |
| `cancellation` | `CancellationToken \| None` | `None` | Cooperative cancel signal honoured at safe yield points |
| `message_queues` | `MessageQueues \| None` | `None` | Steering + follow-up queues for mid-turn injection |
| `discipline` | `list[DisciplineGuard] \| None` | `None` | Phased-workflow guards (#118) |
| `instruction_anchor` | `InstructionAnchor \| None` | `None` | Periodically re-injects key instructions |
| `learning` | `LearningStore \| None` | `None` | SQLite+FTS5 observation store (#116) |
| `feedback_tracker` | `FeedbackTracker \| None` | `None` | Confidence + error tracking |
| `learning_injector` | `LearningInjector \| None` | `None` | Inject proven error-fix patterns mid-loop |
| `hook_emitter` | `HookEmitter \| None` | `None` | **Hook integration** — fires `PreToolUse`, mutates `updatedInput`, overrides `permissionDecision` |
| `tool_timeout_s` | `float \| None` | `None` | Per-tool-call timeout; tools that exceed it return a synthetic error so the loop continues |
| `condensation` | `SummaryCompaction \| None` | `None` | LLM-based condensation strategy (M11) |
| `condense_every_n_steps` | `int \| None` | `None` | Trigger `condensation.compact(...)` every N steps |
| `yolo_mode` | `bool` | `False` | Disable safety defaults for sandbox runs |
| `secrets_redaction` | `bool \| None` | `None` | Force-disable redaction middleware |

## Declarative Permission Rules (W13-G6)

Pass a `PermissionRuleset` for ordered, glob-based rule evaluation
(last-match-wins, `.gitignore` style). Rules can target a tool name,
an argument key, or a glob over arguments:

```python
from chimera.permissions import PermissionRuleset, Rule, PermissionAction

rules = PermissionRuleset(
    rules=[
        Rule(tool_pattern="*", action=PermissionAction.ASK),
        Rule(tool_pattern="read_file", action=PermissionAction.ALLOW),
        Rule(tool_pattern="search", action=PermissionAction.ALLOW),
        Rule(
            tool_pattern="bash",
            action=PermissionAction.DENY,
            arg_key="command",
            arg_pattern="rm *",
            description="Block destructive shell commands",
        ),
    ],
    default=PermissionAction.ASK,
)
config = LoopConfig(permissions=rules)
```

See [Permissions](/modules/permissions/) for the full rule schema and
the five preset policies (`AutoApprove`, `AlwaysDeny`, `AllowList`,
`ReadOnly`, `Interactive`).

## File Undo (W13-G5)

`ghost_commits` plugs a `GhostCommitManager` into the loop. The manager
snapshots the workdir before every file-modifying tool call and exposes
a stack-shaped `undo()` API used by the REPL `/undo` command:

```python
from chimera.checkpoints_ghost import GhostCommitManager

ghost = GhostCommitManager(workdir="/path/to/project")
config = LoopConfig(ghost_commits=ghost)

# After the loop runs, restore the last write:
ghost.undo()  # restores files to pre-write state
ghost.depth   # snapshots remaining
ghost.peek()  # most recent snapshot without popping
```

Snapshots cap at `max_snapshots=50` by default; the oldest is evicted
when the limit is reached. Both git repos (commit-on-hidden-ref) and
plain directories (file copies) are supported.

## Hook Event Filter (W13-G4)

The `hook_emitter` wires the **27** lifecycle events emitted by the
loop into shell / prompt / function hooks. Each `HookMatcher` accepts
an `events: list[str] | None` field that constrains which lifecycle
events fire that matcher — `None` matches every event (legacy
behaviour); a list (e.g. `["PreToolUse", "PostToolUse"]`) restricts
the matcher to those events only.

```python
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import HookMatcher, CommandHook

matchers = [
    HookMatcher(
        matcher="bash",
        hooks=[CommandHook(command="echo 'about to bash'")],
        events=["PreToolUse"],  # only fires before bash, not after
    ),
]
emitter = HookEmitter(executor=HookExecutor(), matchers=matchers)
config = LoopConfig(hook_emitter=emitter)
```

The full lifecycle event roster (see
`chimera.hooks.events.HookEvent`):

| Event family | Events |
|--------------|--------|
| Tool dispatch | `PreToolUse`, `PostToolUse`, `PostToolUseFailure` |
| Permissions | `PermissionRequest`, `PermissionDenied` |
| Session | `SessionStart`, `SessionEnd`, `Stop`, `StopFailure`, `Setup` |
| Subagent | `SubagentStart`, `SubagentStop`, `TeammateIdle` |
| User input | `UserPromptSubmit`, `Notification`, `Elicitation`, `ElicitationResult` |
| Compaction | `PreCompact`, `PostCompact` |
| Tasks | `TaskCreated`, `TaskCompleted` |
| Workspace | `WorktreeCreate`, `WorktreeRemove`, `CwdChanged`, `FileChanged` |
| Config | `ConfigChange`, `InstructionsLoaded` |

`HookOutput` from any hook may set
`permission_decision = "allow" | "deny" | "ask" | "defer"` to
override the policy; `updated_input` shallow-merges into tool args
before dispatch.

## Cancellation Wiring

```python
import signal
from chimera.core.cancellation import CancellationToken

token = CancellationToken()
signal.signal(signal.SIGINT, lambda *_: token.cancel())
config = LoopConfig(cancellation=token)
```

The REPL (`chimera code`) wires this automatically so Ctrl+C cancels
the current turn without killing the process.

## Import Reference

```python
from chimera.core.loop_config import LoopConfig, UNSAFE_ENV_VAR
```

## Related

- [Permissions](/modules/permissions/) — policy + ruleset surface
- [Cancellation](/modules/cancellation/) — token wiring
- [Checkpoints](/modules/checkpoints/) — env-snapshot integration
- [Critic](/modules/critic/) — score-and-refine inside the loop
- [File Tracker](/modules/file-tracker/) — survives compaction
- [Loops](/modules/loops/) — concrete loop implementations
