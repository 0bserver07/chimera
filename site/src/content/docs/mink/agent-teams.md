---
title: Mink Agent Teams
description: Coordinate multiple coding agents (Codex, OpenCode, internal Chimera) as teammates on a shared file-locked task list via MCP.
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
chimera team create <name> [--model kimi-k2.6] [--policy read-only|workspace-write|dangerous]
chimera team join <name> <agent_id>
chimera team rm <name> [--force]
chimera team ls [--json]

# Tasks
chimera team task add <name> "<description>" [--by <id>] [--depends-on <task_id> ...]
chimera team task list <name>

# Permissions
chimera team policy <name>                   # show the team's posture
chimera team policy <name> read-only         # set it (or 'none' to clear)
chimera team audit <name> [--agent <id>] [--json]

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

## Permission propagation

Every coding-agent runtime spells "what may this agent touch" its own
way — sandbox flags, a config file's permission block, an in-process
policy object. Configuring each teammate separately means the lead has
no way to say *"this team runs read-only"* and have it hold, and the
failure is silent: a teammate quietly running looser than intended looks
exactly like one running correctly.

A team therefore carries **one posture**, set by the lead, inherited by
every teammate:

```bash
chimera team create review-pr --policy read-only
chimera team policy review-pr workspace-write   # change it later
```

| Policy | Means |
|---|---|
| `read-only` | Only the read tools (`read_file`, `search`, `list_files`, `repo_map`, `import_graph`). Writes and shell are denied. |
| `workspace-write` | Reads and shell allowed; writes must land inside the workspace (plus the teams home). |
| `dangerous` | No restriction — for sandboxes you already trust. |

Unset (the default) means what it always meant: each runtime's own
configuration is in charge, and nothing about your existing teams
changes.

### How the posture reaches a teammate

`chimera-team-run` resolves the posture — explicit `--policy` wins,
otherwise the team's — and propagates it two ways:

1. **`CHIMERA_TEAM_POLICY` in the environment**, alongside the
   `CHIMERA_TEAM` / `CHIMERA_AGENT` identity the runner already exports.
   A **Chimera teammate binds itself** to it: the policy becomes a
   `tool_call` interceptor, which runs *before* hooks and before the
   agent's own permission check, so a teammate cannot out-vote its lead.
2. **Translated flags for an external runtime**, spliced into `--cmd`
   wherever you put `{policy_args}`:

   ```bash
   chimera-team-run --team review-pr --agent ext-1 \
       --cmd 'my-agent {policy_args} run "{prompt}"' \
       --policy workspace-write --workspace ~/project
   ```

Translations are **data**. Chimera's own runtime is built in and needs
no flags at all (the env var does the work). Any other runtime is your
command, so its dialect is your config — declare it once in
`~/.chimera/config.toml`:

```toml
[team_runtimes.my-agent]
read-only = "--sandbox read-only"
workspace-write = "--sandbox write --add-dir {workspace} --add-dir {teams_home}"
dangerous = "--no-sandbox"

[team_runtimes.my-agent.env]
workspace-write = { MY_AGENT_SANDBOX = "write" }
```

`{workspace}` and `{teams_home}` are substituted at spawn time. The
runtime name is matched against the first token of `--cmd`, or set it
explicitly with `--policy-runtime`.

**A posture that cannot be translated stops the teammate.** If a policy
is in force and the runtime has no adapter, `chimera-team-run` exits 2
with the list of known runtimes rather than launching an agent at
permissions nobody chose. Likewise, if the flags exist but `--cmd` has
no `{policy_args}`, the runner says so loudly instead of guessing where
they belong in your command line.

### `team_*` is never blocked

Coordination tools are the substrate a teammate stands on, not the work
it does. A `read-only` teammate that cannot call `team_claim_task` is
not safe, it is broken — and broken in the confusing way, where tools
fail with permission errors that look like the agent misbehaving. Every
posture allows `team_*` unconditionally, and `workspace-write` always
adds the teams home to the writable roots. That is the same class of
footgun as the sandbox-write gotcha below, hidden on purpose.

### Blocked calls are visible

A posture that silently blocks work would look like a lazy agent. Every
denial is recorded to `~/.chimera/teams/<name>/audit.jsonl` and surfaces
in both status and the audit list:

```console
$ chimera team status review-pr
{
  "name": "review-pr",
  "policy": "read-only",
  ...
  "policy_decisions": { "denied": 3 }
}

$ chimera team audit review-pr
denied    codex-1           write_file          team policy 'read-only' does not allow 'write_file' (set by the team lead)
```

### Honest scope

- In-process enforcement is real only where Chimera owns the loop
  (a `chimera code` teammate). For a third-party runtime the
  translation configures **that runtime's** sandbox — enforcement is
  still its own; what the layer removes is the operator having to
  remember three dialects and getting one of them wrong.
- Under `workspace-write`, path confinement inspects the write tools'
  arguments (`path`, resolved through symlinks). **Shell commands are
  allowed and are not path-checked** — confining a subprocess is a
  sandbox's job, not an argument parser's, which is exactly why the
  policy is also translated into the runtime's own sandbox flags.

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
    [--no-push] [--push-interval 0.25] \
    [--policy read-only|workspace-write|dangerous] \
    [--policy-runtime <name>] [--workspace <dir>]
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
one-shot mode). Same MCP path as the other runtimes, so the agent sees
the same `team_*` tools.

Verified live end-to-end — see
[Live verification](#live-verification) below.

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
least one, no double-claims. For real-model runs see
[Live verification](#live-verification).

## Live verification

Three scripts in `examples/agent_teams/`. The first runs on every PR
against a protocol-faithful mock; the other two are **opt-in** — they
drive a real model and spend tokens.

| Script | What it proves | Needs |
|---|---|---|
| `verify_integration.py` | Runner + MCP server + file-locked claims under two concurrent teammates | nothing (mock agent) |
| `verify_chimera_native.py` | A real model claims → works → completes over MCP, and the team policy constrains it | a configured provider |
| `verify_push_live.py` | Mail sent mid-turn reaches a real model and changes what it does | a configured provider |

```bash
set -a; source .env; set +a

# a real teammate does the work
python examples/agent_teams/verify_chimera_native.py --model 'glm-5.2[1m]'

# the lead's posture actually bites
python examples/agent_teams/verify_chimera_native.py --model 'glm-5.2[1m]' \
    --policy read-only --expect-blocked

# mid-turn mail lands
python examples/agent_teams/verify_push_live.py --model 'glm-5.2[1m]'
```

Results on `glm-5.2[1m]` (2026-07-24):

- **No policy** — claimed, wrote the file, completed. Task record shows
  `status: completed`, `claimed_by: chimera-1`.
- **`workspace-write`** — same, with zero denials: the write landed
  inside the workspace, so the posture never had to intervene.
- **`read-only`** — the teammate claimed the task (coordination is
  always allowed), tried `write_file`, `bash`, `edit_file`,
  `replace_in_file`, then **released the task back to the pool and
  messaged the lead** that the policy blocks it. Ten denials in
  `chimera team audit`; no file created. That is the designed failure
  mode: refuse, hand back, say why.
- **Mid-turn push** — the agent wrote `one.txt`, the lead sent
  *"name the third file gamma.txt instead"*, and the run finished with
  `['gamma.txt', 'one.txt', 'two.txt']`. No `three.txt`.

> The `read-only` run is also how the MCP-namespacing bug was found:
> the loop sees `mcp__chimera-team__team_claim_task`, so an allowance
> written against the bare `team_` prefix blocked every coordination
> call. Hermetic tests using bare names all passed. The fix and its
> regression lock live in `chimera/mcp_servers/team_policy.py`
> (`is_coordination_tool`).

**Not verified:** the OpenCode arm. `opencode auth list` reports
`0 credentials` in this environment, so it stays wired-but-unrun — the
same status issue
[#151](https://github.com/0bserver07/chimera/issues/151) recorded.

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
5. **Permission enforcement is in-process only for Chimera teammates.**
   The lead's posture propagates to every runtime (see
   [Permission propagation](#permission-propagation)), but only a
   Chimera teammate is *bound* by it in-process; for a third-party
   runtime the layer configures that runtime's own sandbox. Under
   `workspace-write`, shell commands are not path-checked.
6. **Stuck claims** get nudged and force-released; the task isn't lost,
   but the agent's "I claimed it" intent is. Tune `--max-nudges` if
   you're seeing agents that need more time.
7. **Codex sandbox-write requirement.** See the gotcha above.
8. **Live verification.** Codex and internal Chimera are verified
   end-to-end on a real model (see
   [Live verification](#live-verification)). OpenCode remains wired but
   unrun here: `opencode auth list` reports `0 credentials`.

## Cross-references

- Source: [`chimera/cli/agent_teams.py`](https://github.com/0bserver07/chimera/blob/master/chimera/cli/agent_teams.py)
  (`Team` class, `TeamMailbox`, file-locked primitives, `destroy`, `list_teams`)
- MCP server: [`chimera/mcp_servers/team_server.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/team_server.py)
- Runner: [`chimera/mcp_servers/teammate_runner.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/teammate_runner.py)
- Mail push: [`chimera/mcp_servers/team_push.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/team_push.py)
  (`MailboxWatcher`, `TeammateSink`)
- Permission propagation: [`chimera/mcp_servers/team_policy.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mcp_servers/team_policy.py)
  (`RuntimeAdapter`, `WorkspaceWrite`, `team_policy_interceptor`)
- Live dashboard: [`chimera/mink/team_watch.py`](https://github.com/0bserver07/chimera/blob/master/chimera/mink/team_watch.py)
- Role discovery: [`chimera/agents/team_roles.py`](https://github.com/0bserver07/chimera/blob/master/chimera/agents/team_roles.py)
- Examples + verify: [`examples/agent_teams/`](https://github.com/0bserver07/chimera/tree/master/examples/agent_teams)
- Parity matrix row 11 in [`parity-matrix.md`](./parity-matrix.md)
