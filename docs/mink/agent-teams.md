---
title: "Mink Agent Teams"
description: "Coordinate multiple coding agents (Codex, OpenCode, internal Chimera) as teammates on a shared file-locked task list via MCP."
---

# Agent Teams

Experimental coordination for multiple coding agents working together on a
shared task list. The lead seeds tasks; teammates — Codex CLI, OpenCode,
internal Chimera agents, or any MCP-capable host — claim, work, and
complete them. Coordination state lives on disk under
`~/.chimera/teams/<name>/`; communication is via per-agent mailboxes.

> **Status: experimental.** Gated behind
> `CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1`. Designed to be **agent-agnostic** —
> anything that speaks MCP can be a teammate. The orchestration layer
> doesn't care which model or runtime is on the other end of the wire.

## Architecture

Three roles, one disk state:

```
LEAD (your interactive session — chimera, your shell, anything that can write to disk)
  │
  │  adds tasks, watches status, addresses teammates by name
  ▼
~/.chimera/teams/<name>/              ← disk state (fcntl file locks)
  config.json                         ← team config, members
  task_list.jsonl                     ← append-only queue (atomic claim)
  mailbox/<agent>.jsonl               ← per-agent inbox
  ▲
  │  claim / work / complete / send-message / recv-messages
  │
TEAMMATES (one process per agent; any MCP host: Codex, OpenCode, Chimera, …)
```

The lead is just a regular interactive session — there's no separate
daemon to run. Teammates are external agents driven by
`chimera-team-run`, which polls the task list and spawns the configured
agent command per task.

## CLI surface

```bash
export CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1

# Lifecycle
chimera team create <name> [--model kimi-k2.6]
chimera team join <name> <agent_id>
chimera team rm <name> [--force]
chimera team ls [--json]

# Tasks
chimera team task add <name> "<description>" [--by <id>] [--depends-on <task_id> ...]
chimera team task list <name>

# Status & dashboard
chimera team status <name>
chimera team watch <name> [--interval 1.0]   # live TTY dashboard

# Discoverable roles (.md frontmatter team_role:)
chimera team roles    # lists executor / planner / reviewer / researcher + any user-defined
```

## Wire layer: `chimera-team-mcp`

The MCP server that exposes the team state to any MCP-capable agent.
Each running instance represents **one teammate identity**; the host
(Codex / OpenCode / your own MCP client) spawns it as a stdio subprocess.

```bash
chimera-team-mcp --team <name> --agent <agent_id> [--role lead|teammate]

# Or via env (preferred when the runner injects identity):
CHIMERA_TEAM=<name> CHIMERA_AGENT=<id> CHIMERA_ROLE=lead chimera-team-mcp
```

### Tools exposed

| Tool | Purpose | Lead-only |
|---|---|---|
| `team_init` | Create / open a team directory (idempotent) | — |
| `team_join` | Add an agent id to a team (idempotent) | — |
| `team_list_members` | List members | — |
| `team_add_task` | Append a task (with optional `depends_on`) | **yes** when role = teammate |
| `team_list_tasks` | Filter `all` / `open` / `open_all` / `blocked` / `claimed` / `completed` | — |
| `team_claim_task` | Claim by id, or auto-claim the next unblocked open task | — |
| `team_release_task` | Release a claim back to the pool | — |
| `team_complete_task` | Mark a claimed task done | — |
| `team_send_message` | DM another teammate's mailbox | — |
| `team_recv_messages` | Drain own mailbox | — |
| `team_propose_plan` | Propose an implementation plan for a claimed task (needed when the task has `requires_plan`) | — |
| `team_approve_plan` | Approve / reject a teammate's proposed plan | **yes** |

### Role gating

`--role teammate` (default) blocks `team_add_task` — only the lead
creates work. The role can also come from `CHIMERA_ROLE`.
`team_approve_plan` is lead-only as well.

### Task dependencies

`team_add_task` accepts `depends_on: [<task_id>, …]`. A task with
unsatisfied deps is **blocked**:

- `team_claim_task` skips blocked tasks during auto-claim.
- Specific-id claim of a blocked task returns
  `{"claimed": false, "reason": "blocked by deps"}`.
- `team_list_tasks filter=blocked` lists open-but-blocked tasks.
- The default `team_list_tasks filter=open` **excludes** blocked tasks;
  use `filter=open_all` to see everything.

On the CLI, pass `--depends-on <task_id>` (repeat for multiple) to
`chimera team task add`.

### Plan approval

`team_add_task` accepts `requires_plan: true`. A task created with it
cannot be completed until its plan is approved:

- The claiming agent proposes via `team_propose_plan`.
- The lead approves or rejects via `team_approve_plan` (rejection
  feedback lands on the task as `plan_feedback`), or a human runs the
  interactive loop: `chimera team approvals <name>`.
- `team_complete_task` on an unapproved plan returns
  `{"completed": false, "reason": "plan requires approval"}`.
- `CHIMERA_AUTO_APPROVE_PLANS=1` auto-approves at propose time
  (headless runs).

## Runner layer: `chimera-team-run`

Polls the team's task list and spawns the configured external agent
command for each open task. When the subprocess exits, the runner checks
team state and loops. Exits cleanly after `--idle-timeout` seconds of no
team-state progress (covers both "no open tasks" and "agent keeps
failing to make progress").

```bash
chimera-team-run --team <name> --agent <agent_id> \
    --cmd '<external agent command with {prompt} or {prompt_file}>' \
    [--idle-timeout 60] [--task-timeout 600] [--max-nudges 1] \
    [--no-push] [--push-interval 0.25]
```

The runner injects `CHIMERA_TEAM`, `CHIMERA_AGENT`, and
`CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1` into the subprocess environment so
the MCP server picks up identity without per-invocation config edits.

### Idle-nudge for stuck claims

If the external agent claims a task but exits without completing it, the
runner sends a nudge message to the agent's mailbox:

> *"You claimed task X ('description…') but did not complete it.
> Either call team_complete_task or team_release_task."*

After `--max-nudges` consecutive no-progress iterations on the same stuck
task, the runner force-releases the claim so another teammate can pick
it up. Nudge counters reset when the task transitions out of `claimed`
(completion, voluntary release, or runner-initiated release).

### Real-time mail push

By default, mail is delivered by **pull**: a teammate sees its mailbox
when it calls `team_recv_messages`. With spawn-per-task that means "at
the start of the next task", so a mid-run *"stop, requirements changed"*
never lands.

When a teammate has a **live session** (`--reuse-session --runtime acp`),
the runner also watches that teammate's mailbox and pushes new messages
straight into the running session:

```text
chimera-team-run: watching mailbox for opencode-1; new team mail is
pushed into the live session.
...
chimera-team-run: pushed 2 message(s) into the live session.
```

The pushed message arrives as one steering message that names its
senders:

```text
[team mail] 1 new message(s) for 'opencode-1' — read them before continuing:
- from lead: scope changed, only touch auth.py
```

**Why a filesystem watch and not a socket.** The team substrate is
deliberately daemonless: coordination is JSONL under
`~/.chimera/teams/<name>/` guarded by `fcntl` locks, so any MCP host in
any language can join by reading and writing files. A notify socket
would add a transport only Chimera-side processes speak, plus a broker
to keep alive. Watching the mailbox file keeps one source of truth, adds
no dependency (stdlib `os.stat`), and is inert when nobody is listening.

**Delivery semantics, precisely:**

| Situation | What happens |
|---|---|
| Teammate idle between tasks | Delivered within ~`--push-interval` (default 0.25s), well inside a second |
| Teammate mid-turn | Delivered at the next turn boundary — ACP's `session/sendMessage` is turn-scoped, so a push is the next message in the same session, not a preemption |
| Spawn-per-task (no live session) | Nothing is pushed; mail waits for `team_recv_messages`, exactly as before |
| Push fails (session died, crash mid-delivery) | The message is **not** acked, so it stays in the mailbox and the pull path delivers it |
| `--no-push` | Watcher never starts; pull-only |

Messages carry a stable `id`, and the watcher acknowledges exactly the
ids it delivered (`TeamMailbox.consume`). Mail that arrives between the
watcher's read and its ack survives, and a delivered message is never
re-delivered by `team_recv_messages`. **Push can only ever be faster
than pull — it cannot lose mail.**

Mid-run delivery reuses the existing steering seam rather than a second
channel: `AgentDriver`, `Session`, and `CodingAgent` all satisfy the
`TeammateSink` protocol (`steer(text) -> None`) as-is, so an in-process
Chimera teammate can be wired to the same `MailboxWatcher`:

```python
from chimera.cli.agent_teams import Team, TeamMailbox
from chimera.mcp_servers.team_push import MailboxWatcher

mailbox = TeamMailbox(Team("review-pr"), "chimera-1")
with MailboxWatcher(mailbox, driver):   # driver: AgentDriver
    ...                                 # mail lands via driver.steer()
```

Drivers that *cannot* steer (a subprocess-backed external lane, which
only notes that steering is unsupported) must not be wrapped — leave the
push path unconfigured so mail flows through the pull path.

### Session reuse (ACP)

For agents that speak Agent Client Protocol (ACP) over stdio, pass
`--reuse-session --runtime acp` to keep a **single subprocess alive
across N tasks**. The runner spawns the external agent once via
`chimera.acp.client.ACPClient`, then sends one `session/sendMessage`
per task instead of paying the cold-start cost (binary load, auth,
context, MCP server reinit) on every iteration.

```bash
chimera-team-run --team review-pr --agent opencode-1 \
    --runtime acp --reuse-session \
    --cmd 'opencode acp'
```

What changes vs. spawn-per-task:

| Concern | Spawn-per-task (default) | `--reuse-session --runtime acp` |
|---|---|---|
| Subprocess lifecycle | One per task | One for the whole `chimera-team-run` lifetime |
| Per-task overhead | Full cold-start (binary load, auth, MCP reinit) | One `session/sendMessage` JSON-RPC call |
| Prompt delivery | `{prompt}` or `{prompt_file}` substituted into `--cmd` | Sent via ACP `session/sendMessage` (placeholders not required) |
| Agent crash mid-task | Subprocess just exits; runner spawns a new one for the next task | Runner tears down the dead client and respawns on the next iteration |
| Cleanup on shutdown | Nothing to clean up (subprocesses already exited) | Runner calls `client.stop()` so the persistent process exits cleanly |
| Stuck-claim nudges | Apply | Apply (same on-disk mailbox / task-list path) |

Crash recovery composes with the existing stuck-claim mechanism: if the
agent's ACP subprocess dies mid-task with a claim still held, the
runner respawns the client *and* the on-disk claim is still owned by
this agent id — so the nudge → force-release flow runs as usual.

`--reuse-session` requires `--runtime acp`. Passing it with the default
`--runtime spawn` downgrades the flag with a warning rather than
failing, so a misconfigured invocation still makes progress:

```text
chimera-team-run: --reuse-session requires --runtime acp
(got runtime='spawn'); falling back to spawn-per-task.
```

Run-time logging shows which path is active, so it's easy to confirm
the optimization is engaged:

```text
chimera-team-run: started persistent ACP session.
chimera-team-run: 3 open task(s); sending prompt via persistent ACP session.
chimera-team-run: agent exited rc=0; team state changed.
... (N more iterations) ...
chimera-team-run: no progress for 60s (3 tasks completed by opencode-1); exiting.
chimera-team-run: stopped persistent ACP session.
```

## External-agent integration

`chimera-team-mcp` is agent-agnostic. Anything that speaks MCP can be a
teammate. Three runtimes are wired today, with copy-pasteable configs in
[`examples/agent_teams/`](https://github.com/0bserver07/chimera/tree/master/examples/agent_teams).

### Codex CLI (verified end-to-end)

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.chimera-team]
command = "/path/to/.venv/bin/chimera-team-mcp"
env = { CHIMERA_EXPERIMENTAL_AGENT_TEAMS = "1" }
```

Or pass inline (no config file edit):

```bash
codex exec \
    --skip-git-repo-check \
    -s workspace-write --add-dir ~/.chimera/teams \
    -c 'mcp_servers.chimera-team.command="/path/to/chimera-team-mcp"' \
    -c 'mcp_servers.chimera-team.env.CHIMERA_EXPERIMENTAL_AGENT_TEAMS="1"' \
    -c 'mcp_servers.chimera-team.env.CHIMERA_TEAM="<name>"' \
    -c 'mcp_servers.chimera-team.env.CHIMERA_AGENT="codex-1"' \
    "<teammate prompt>"
```

> ⚠️ **Sandbox-write gotcha.** Codex's `read-only` sandbox blocks the
> MCP server from writing to the teams directory, which silently breaks
> every `team_claim_task` / `team_complete_task` etc. as
> `"user cancelled MCP tool call"`. Use
> `-s workspace-write --add-dir <teams-home>` (or put
> `CHIMERA_TEAMS_HOME` inside the workspace). For trusted runs,
> `--dangerously-bypass-approvals-and-sandbox` skips the gate entirely.

### OpenCode

Drop a project-local `opencode.json` (or
`~/.config/opencode/opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "chimera-team": {
      "type": "local",
      "command": ["/path/to/.venv/bin/chimera-team-mcp"],
      "enabled": true,
      "environment": {
        "CHIMERA_EXPERIMENTAL_AGENT_TEAMS": "1",
        "CHIMERA_TEAM": "<name>",
        "CHIMERA_AGENT": "opencode-1"
      }
    }
  }
}
```

Then: `opencode run "<teammate prompt>"`. Requires
`opencode auth login` first. Wired end-to-end; live-verification tracked
in issue [#151](https://github.com/0bserver07/chimera/issues/151).

### Internal Chimera (`chimera code -p`)

`chimera code` already loads MCP servers from `~/.chimera/mcp.json` or
`<workdir>/.mcp.json`. Drop:

```json
{
  "mcpServers": {
    "chimera-team": {
      "command": "/path/to/.venv/bin/chimera-team-mcp",
      "env": {
        "CHIMERA_EXPERIMENTAL_AGENT_TEAMS": "1",
        "CHIMERA_TEAM": "<name>",
        "CHIMERA_AGENT": "chimera-1"
      }
    }
  }
}
```

Then: `chimera code -p "<teammate prompt>"` (the `-p` / `--print`
one-shot mode). Same MCP path as Codex / OpenCode, so the agent sees the
same `team_*` tools.

## End-to-end example

Three terminals: lead seeds the queue, two external teammates drain it
concurrently.

```bash
# Terminal 1 — the lead: create + seed + watch
export CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1
chimera team create review-pr
chimera team task add review-pr "audit auth module for token handling"
chimera team task add review-pr "check test coverage on jwt validator"
chimera team task add review-pr "review error messages for info leaks"
chimera team watch review-pr            # live dashboard

# Terminal 2 — Codex teammate
chimera-team-run --team review-pr --agent codex-1 \
    --cmd 'codex exec -s workspace-write --add-dir ~/.chimera/teams "{prompt}"'

# Terminal 3 — OpenCode teammate
chimera-team-run --team review-pr --agent opencode-1 \
    --cmd 'opencode run "{prompt}"'
```

Both runners poll concurrently; file locking prevents double-claims;
the dashboard reflects state live.

A self-contained smoke test of this exact flow lives at
[`examples/agent_teams/verify_integration.py`](https://github.com/0bserver07/chimera/blob/master/examples/agent_teams/verify_integration.py).
It spawns two `chimera-team-run` subprocesses with a faithful
protocol-mock agent and asserts 6 tasks completed, both agents claim at
least one, no double-claims.

## Caveats / known limits

1. **Experimental** — gated by `CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1`; the
   API may still change.
2. **Push needs a live session.** Mid-run delivery works for teammates
   with a persistent session (`--reuse-session --runtime acp`) and for
   in-process Chimera teammates — see
   [Real-time mail push](#real-time-mail-push). Spawn-per-task teammates
   have no session to push into and stay pull-only, and a push landing
   mid-turn is delivered at the next turn boundary rather than
   preempting the turn.
3. **Cold start per task (default).** By default `chimera-team-run`
   spawns a fresh subprocess per task. For ACP-speaking agents,
   `--reuse-session --runtime acp` keeps one subprocess alive across N
   tasks (see [Session reuse (ACP)](#session-reuse-acp) above). Non-ACP
   agents still pay the cold-start cost per task; persistent stdin-fed
   sessions for other runtimes are still tracked in issue
   [#148](https://github.com/0bserver07/chimera/issues/148).
4. **No plan-approval workflow.** Tasks can't yet require a
   teammate-proposed plan + lead-approve gate. See issue
   [#147](https://github.com/0bserver07/chimera/issues/147).
5. **Permissions are per-runtime.** Each agent's sandbox / approval
   policy is configured in its own config — no unified propagation yet.
   See issue [#150](https://github.com/0bserver07/chimera/issues/150).
6. **Stuck claims** get nudged and force-released; the task isn't lost,
   but the agent's "I claimed it" intent is. Tune `--max-nudges` if
   you're seeing agents that need more time.
7. **Codex sandbox-write requirement.** See the gotcha above.
8. **Live verification.** Codex is verified end-to-end. OpenCode and
   internal Chimera are wired but not yet live-verified — see issue
   [#151](https://github.com/0bserver07/chimera/issues/151).

## Cross-references

- Source: [`chimera/cli/agent_teams.py`](https://github.com/0bserver07/chimera/blob/master/chimera/cli/agent_teams.py)
  (`Team` class, `TeamMailbox`, file-locked primitives, `destroy`, `list_teams`)
- MCP server: [`chimera/mcp_servers/team_server.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/team_server.py)
- Runner: [`chimera/mcp_servers/teammate_runner.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/teammate_runner.py)
- Mail push: [`chimera/mcp_servers/team_push.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/team_push.py)
  (`MailboxWatcher`, `TeammateSink`)
- Live dashboard: [`chimera/mink/team_watch.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mink/team_watch.py)
- Role discovery: [`chimera/agents/team_roles.py`](https://github.com/0bserver07/chimera/blob/master/chimera/agents/team_roles.py)
- Examples + verify: [`examples/agent_teams/`](https://github.com/0bserver07/chimera/tree/master/examples/agent_teams)
- Parity matrix row 11 in [`parity-matrix.md`](./parity-matrix.md)
