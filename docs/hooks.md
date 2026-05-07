# Hooks

Chimera's hook system lets external code react to lifecycle events that
fire during an agent run. Hooks are dispatched by
[`HookEmitter`](../chimera/hooks/emitter.py), executed by
[`HookExecutor`](../chimera/hooks/executor.py), and matched against
[`HookMatcher`](../chimera/hooks/hook_types.py) records loaded from
`.claude/settings.json` (or the user-global `~/.claude/settings.json`).

## Event reference

The table below mirrors the Claude Code `HookEvent` surface that
Chimera fires today. Every row is **wired** — i.e. running an agent
will reach the listed source line at least once on the listed
trigger. The "Payload" column lists the
[`HookInput`](../chimera/hooks/hook_types.py) attributes the emitter
populates.

| Event | Trigger | Wired in | Payload |
|---|---|---|---|
| `PreToolUse` | Before each tool dispatch. May mutate input or deny. | `chimera/core/tool_executor.py` (3 executors) | `tool_name`, `tool_input` |
| `PostToolUse` | After a tool returns successfully. | `chimera/core/tool_executor.py` (3 executors) | `tool_name`, `tool_input`, `tool_output` |
| `PostToolUseFailure` | After a tool raises or returns `success=False`. | `chimera/core/tool_executor.py` (3 executors) | `tool_name`, `tool_input`, `tool_error` |
| `UserPromptSubmit` | First loop step when context carries a user message. | `chimera/core/loop.py` (sync + async); `chimera/commands/processor.py` for `/`-commands | `user_prompt` |
| `SessionStart` | First instruction of `iter_steps` / `async_iter_steps`. | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | — |
| `SessionEnd` | Last instruction on every termination path of the loop. | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | — |
| `Stop` | Loop exited cleanly (no further tool calls). | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | — |
| `StopFailure` | Loop terminated abnormally (cost limit, max steps, loop break, cancellation). | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | `tool_error` |
| `Notification` | Fires alongside `Stop` carrying the agent's final text. | `chimera/core/loop.py`; `chimera/core/agent_loop.py` | `tool_output` |
| `SubagentStart` | Just before a sub-agent's first turn. | `chimera/core/agent_spawner.py` | `tool_name` (subagent name) |
| `SubagentStop` | After a sub-agent finishes. | `chimera/core/agent_spawner.py` | `tool_name`, `tool_output` |
| `TeammateIdle` | When a sub-agent goes idle awaiting input. | `chimera/core/agent_spawner.py` | `tool_name` |
| `PreCompact` | Before context compaction runs. | `chimera/core/compaction_integration.py` | — |
| `PostCompact` | After context compaction completes. | `chimera/core/compaction_integration.py` | — |
| `PermissionRequest` | When the permission checker asks for a decision. | `chimera/permissions/checker.py` | `tool_name`, `tool_input` |
| `PermissionDenied` | When the permission checker denies a tool. | `chimera/permissions/checker.py` | `tool_name`, `tool_input` |
| `Elicitation` | Before an interactive permission prompt. | `chimera/permissions/prompt_handler.py` | — |
| `ElicitationResult` | After an interactive permission prompt resolves. | `chimera/permissions/prompt_handler.py` | — |
| `TaskCreated` | When the task manager creates a new task. | `chimera/core/task_manager.py` | `tool_name` (task subject) |
| `TaskCompleted` | When the task manager marks a task done. | `chimera/core/task_manager.py` | `tool_name` (task subject) |
| `Setup` | Once at CLI startup, after settings are loaded. | `chimera/cli/main.py` | `tool_name` (subcommand) |
| `ConfigChange` | When `~/.claude/settings.json` is written through the mink CLI. | `chimera/mink/settings.py` | — |
| `WorktreeCreate` | After `EnterWorktree` succeeds. | `chimera/tools/worktree_tool.py` | `tool_input` (path, branch) |
| `WorktreeRemove` | After `ExitWorktree` succeeds (or fails). | `chimera/tools/worktree_tool.py` | `tool_input` |
| `InstructionsLoaded` | When `CLAUDE.md` / `AGENTS.md` files are picked up. | `chimera/context/agent_memory.py`; `chimera/otter/rules.py` | `messages` (loaded files) |
| `CwdChanged` | When the file watcher detects a working-directory change. | `chimera/hooks/file_watcher.py` | — |
| `FileChanged` | When the file watcher detects a tracked-file edit. | `chimera/hooks/file_watcher.py` | `tool_input` (path) |

## Configuring hooks

Hooks are declared per-project in `.claude/settings.json` under the
`hooks` key. Each event maps to a list of matchers; each matcher
carries a list of command, prompt, or function hooks. Example:

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
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "scripts/notify-done.sh"}]}
    ]
  }
}
```

The `mink` CLI parses `~/.claude/settings.json` →
`<cwd>/.claude/settings.json` → `<cwd>/.claude/settings.local.json` →
`<cwd>/.chimera/settings.json` and merges them into the
`HookEmitter` carried on `LoopConfig.hook_emitter`.

## Adding a new hook event

1. Add the value to `HookEvent` in
   [`chimera/hooks/events.py`](../chimera/hooks/events.py).
2. Wire an `emitter.emit(...)` call (or `emit_sync(...)` for sync code
   paths) at the canonical trigger point. For loop-lifecycle events,
   call `_fire_loop_hook` / `_fire_loop_hook_async` from
   `chimera/core/loop.py` so failures never break the loop.
3. Add a row to the table above documenting the trigger and payload.
4. Add a recording-emitter test under `tests/events/test_hook_events.py`
   that fires a minimal agent flow and asserts the new event appears.
