# Agent Teams (M5-B)

Experimental multi-agent coordination. Lets a "lead" agent dispatch work
across one or more teammate `AgentLoop`s that share a task list, a mailbox,
and (optionally) a tmux split-pane TUI.

> Status: experimental. Gated behind `CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1`.

## Modules

- `chimera/cli/agent_teams.py` — top-level orchestrator. Wires the shared
  task list, file-locked claiming, idle notifications, and `SendMessage`
  delivery. See module docstring for the full state machine.
- `chimera/mink/team.py` — `chimera team <subcommand>` CLI surface
  (`create`, `join`, `task add|list`, `status`). Registered as a
  top-level subcommand on the main argparse parser.
- `chimera/tools/task_tool.py` — `Task` tool used inside an agent loop to
  spawn a child agent with a linked `CancellationToken`. Underpins
  teammate dispatch.
- `chimera/agents/dispatch/` — request classifier and trigger router used
  by the lead agent to decide which teammate gets the next task.

## Concepts

| Concept | Where | Notes |
|---------|-------|-------|
| Shared task list | `chimera/cli/agent_teams.py` | File-backed JSON in `.chimera/teams/<id>/tasks.json`; each teammate atomically claims items via `fcntl` lock. |
| Mailbox | `chimera/cli/agent_teams.py` | Per-teammate inbox; the `SendMessage` tool routes through here. |
| Idle notifications | `chimera/cli/agent_teams.py` | A teammate that observes its inbox empty for N seconds emits an `idle` event; the lead picks the next task. |
| TUI split | `chimera/mink/team.py` | Tmux integration is on the planned roadmap; the current CLI is non-interactive. |

## Quickstart

```bash
export CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1
chimera team create reviewer --model kimi-k2.6
chimera team join reviewer agent-001
chimera team task add reviewer "Review the diff in branch feature/x"
chimera team task list reviewer
chimera team status reviewer
```

The lead agent sees `SendMessage(recipient='reviewer', ...)` as an
available tool and will dispatch sub-tasks accordingly. Use
`chimera team status <name>` to inspect a team's task list and member
agents. The full subcommand surface is `create`, `join`, `task add`,
`task list`, `status`. Tmux split-pane integration and a richer
`spawn`/`list`/`kill`/`attach` surface are deferred (see issue tracker).

## Cross-references

- Inline docs: `chimera/cli/agent_teams.py` (orchestrator state machine).
- Inline docs: `chimera/tools/task_tool.py` (child-agent isolation tiers).
- Parity matrix row 11 in [`parity-matrix.md`](./parity-matrix.md).
- CC concept origin: `research/mink/11-cc-subagents.md`.

## Caveats

1. Teammate spawning is sequential by default; concurrent dispatch requires
   `--concurrent N` and a non-shared environment (worktrees recommended).
2. The shared task list does **not** survive a host crash mid-write; use a
   real queue (Redis, NATS) for production workloads.
3. `SendMessage` is best-effort — there is no delivery acknowledgement.
4. Cancellation cascades parent → child via the shared `CancellationToken`,
   but child → parent does not.
