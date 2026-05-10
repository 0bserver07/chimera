---
title: "Hook Events Reference"
description: "Complete reference for the 27 hook events Chimera fires, their payload schemas, emit sites, and filtering examples."
---

Chimera's hook system lets external code react to lifecycle events that fire during an agent run. Events are dispatched by `HookEmitter`, executed by `HookExecutor`, and matched against `HookMatcher` records loaded from `.claude/settings.json` (or `~/.claude/settings.json`).

This page is the **full event reference** for every wired event. The 27 events are organised below by phase: tool, session, sub-agent, compaction, permission, task, CLI, and filesystem.

## Tool events (3)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `PreToolUse` | Before each tool dispatch. May mutate input or deny. | `chimera/core/tool_executor.py` (3 executors) | `tool_name`, `tool_input` |
| `PostToolUse` | After a tool returns successfully. | `chimera/core/tool_executor.py` (3 executors) | `tool_name`, `tool_input`, `tool_output` |
| `PostToolUseFailure` | After a tool raises or returns `success=False`. | `chimera/core/tool_executor.py` (3 executors) | `tool_name`, `tool_input`, `tool_error` |

## Session events (5)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `UserPromptSubmit` | First loop step when context carries a user message. | `chimera/core/loop.py` (sync + async); `chimera/commands/processor.py` for `/`-commands | `user_prompt` |
| `SessionStart` | First instruction of `iter_steps` / `async_iter_steps`. | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | — |
| `SessionEnd` | Last instruction on every termination path of the loop. | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | — |
| `Stop` | Loop exited cleanly (no further tool calls). | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | — |
| `StopFailure` | Loop terminated abnormally (cost limit, max steps, loop break, cancellation). | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | `tool_error` |
| `Notification` | Fires alongside `Stop` carrying the agent's final text. | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | `tool_output` |

## Sub-agent events (3)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `SubagentStart` | Just before a sub-agent's first turn. | `chimera/core/agent_spawner.py` | `tool_name` (subagent name) |
| `SubagentStop` | After a sub-agent finishes. | `chimera/core/agent_spawner.py` | `tool_name`, `tool_output` |
| `TeammateIdle` | When a sub-agent goes idle awaiting input. | `chimera/core/agent_spawner.py` | `tool_name` |

## Compaction events (2)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `PreCompact` | Before context compaction runs. | `chimera/core/compaction_integration.py` | — |
| `PostCompact` | After context compaction completes. | `chimera/core/compaction_integration.py` | — |

## Permission events (4)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `PermissionRequest` | When the permission checker asks for a decision. | `chimera/permissions/checker.py` | `tool_name`, `tool_input` |
| `PermissionDenied` | When the permission checker denies a tool. | `chimera/permissions/checker.py` | `tool_name`, `tool_input` |
| `Elicitation` | Before an interactive permission prompt. | `chimera/permissions/prompt_handler.py` | — |
| `ElicitationResult` | After an interactive permission prompt resolves. | `chimera/permissions/prompt_handler.py` | — |

## Task events (2)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `TaskCreated` | When the task manager creates a new task. | `chimera/core/task_manager.py` | `tool_name` (task subject) |
| `TaskCompleted` | When the task manager marks a task done. | `chimera/core/task_manager.py` | `tool_name` (task subject) |

## CLI / config events (2)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `Setup` | Once at CLI startup, after settings are loaded. | `chimera/cli/main.py` | `tool_name` (subcommand) |
| `ConfigChange` | When `~/.claude/settings.json` is written through the mink CLI. | `chimera/mink/settings.py` | — |

## Worktree events (2)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `WorktreeCreate` | After `EnterWorktree` succeeds. | `chimera/tools/worktree_tool.py` | `tool_input` (path, branch) |
| `WorktreeRemove` | After `ExitWorktree` succeeds (or fails). | `chimera/tools/worktree_tool.py` | `tool_input` |

## Filesystem / context events (3)

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `InstructionsLoaded` | When `CLAUDE.md` / `AGENTS.md` files are picked up. | `chimera/context/agent_memory.py`; `chimera/otter/rules.py` | `messages` (loaded files) |
| `CwdChanged` | When the file watcher detects a working-directory change. | `chimera/hooks/file_watcher.py` | — |
| `FileChanged` | When the file watcher detects a tracked-file edit. | `chimera/hooks/file_watcher.py` | `tool_input` (path) |

## Configuring hooks

Hooks are declared per-project in `.claude/settings.json` under the `hooks` key. Each event maps to a list of matchers; each matcher carries a list of command, prompt, or function hooks.

### Filter by tool name

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "tool_name == 'bash'",
        "hooks": [
          {"type": "command", "command": "scripts/audit-bash.sh"}
        ]
      }
    ]
  }
}
```

### Match every event of a kind

```json
{
  "hooks": {
    "Stop": [
      {"hooks": [{"type": "command", "command": "scripts/notify-done.sh"}]}
    ]
  }
}
```

### Filter on payload fields

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "tool_name == 'write_file' and 'production' in tool_input.get('path', '')",
        "hooks": [{"type": "command", "command": "scripts/audit-prod-write.sh"}]
      }
    ]
  }
}
```

The matcher string is evaluated against a small Python expression context with `tool_name`, `tool_input`, `tool_output`, `tool_error`, `user_prompt`, and `messages` bound from the `HookInput`.

## Settings discovery

The mink CLI parses these files in order and merges them into the `HookEmitter` carried on `LoopConfig.hook_emitter`:

1. `~/.claude/settings.json`
2. `<cwd>/.claude/settings.json`
3. `<cwd>/.claude/settings.local.json`
4. `<cwd>/.chimera/settings.json`

Later sources override earlier ones key-by-key.

## Adding a new hook event

1. Add the value to `HookEvent` in [`chimera/hooks/events.py`](https://github.com/0bserver07/chimera/blob/master/chimera/hooks/events.py).
2. Wire an `emitter.emit(...)` call (or `emit_sync(...)` for sync code paths) at the canonical trigger point. For loop-lifecycle events, call `_fire_loop_hook` / `_fire_loop_hook_async` from `chimera/core/loop.py` so failures never break the loop.
3. Add a row to one of the tables above documenting the trigger and payload.
4. Add a recording-emitter test under `tests/events/test_hook_events.py` that fires a minimal agent flow and asserts the new event appears.

## Prerequisites

- Chimera installed: `pip install chimera-run`
- A `.claude/settings.json` (or one of the other discovery paths) containing your `hooks` map
- For command-type hooks: an executable on the path or a script with the right shebang

## See also

- [Permission modes](./permission-modes.md) — the events that fire on every approval decision.
- [File undo](./file-undo.md) — `FileChanged` is what feeds the otter `/undo` snapshot store.
