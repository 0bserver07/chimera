---
title: "External-Agent Lanes"
description: "Race a real third-party coding-agent CLI as a multiplexer lane, beside Chimera lanes, on one task — isolated worktrees, live telemetry, cohort persistence."
---

# External-agent lanes — race the real agents

Chimera's mission is controlled comparison. The multiplexer already races
several Chimera scaffolds (presets, loops, models) on one task. **External
lanes** close the loop: a lane whose driver spawns a *real third-party
coding-agent CLI* as a subprocess, translated into the same event stream every
Chimera lane emits. Now you can race the actual upstream agents against Chimera
— or against each other — under identical tasks and workspace isolation, with
the same scoreboard, telemetry, and cohort artifact.

```bash
set -a; source .env; set +a          # model credentials for the Chimera lane
# race the Claude Code CLI against glm-5.2, isolated worktree each:
chimera code --tui --models ext:claude,glm-5.2[1m]
```

`ext:<profile>` is a lane like any other: its own git worktree, its own pane,
its own row on the scoreboard. Its file writes show in the Ctrl+R diff and
persist in the cohort — resumable with `--resume` / `/cohorts`.

## The lane spec

An external lane is `ext:<profile-name>`. It takes **no** preset or loop axis
(those are Chimera-scaffold knobs; the external tool runs its own way), so
`ext:claude:minimal` is an error on purpose. Mix freely with Chimera lanes:

```bash
chimera code --tui --models ext:claude,glm-5.2,glm-5.1:explore
#                          └ external ┘ └───── Chimera lanes ─────┘
```

## Profiles

A profile says how to run one CLI. One ships built in — **`claude`**, the
Claude Code CLI (`--print --output-format stream-json`), which this repo
already integrates against. Everything else is your own config, under
`[external_agents.<name>]` tables in `~/.chimera/config.toml` (the same file
every Chimera CLI reads; `$CHIMERA_CONFIG_HOME` is honored):

```toml
[external_agents.myagent]
# {task} (required) is replaced with the task text; {workdir} with the lane
# worktree path. The subprocess always runs with cwd = the lane worktree.
command  = ["myagent", "--prompt", "{task}"]
protocol = "stream-json"        # or "text"; default "stream-json"
env      = ["MYAGENT_API_KEY"]  # optional: subprocess sees ONLY these (+ PATH,
                                # HOME, TERM, …). Omit to inherit your full env.
timeout  = 600                  # seconds; default 900. On expiry: SIGTERM.
```

Profile names are your data — name them whatever you like. Reference one with
`ext:myagent`. An unknown or malformed profile fails loudly at launch, naming
the known profiles.

## Protocols

| Protocol | Input | Telemetry |
|----------|-------|-----------|
| `stream-json` | newline-delimited JSON events (the Claude Code CLI's `stream-json` vocabulary) | **real** — cost, tokens, and step count parsed from the final `result` line |
| `text` | plain stdout | **honest zeros** — no cost/step data exists; the lane says so with a system note. Wall-clock time is still real. |

### stream-json event mapping

Each JSON line maps to loop events the pane renders:

| stream-json line | Becomes |
|------------------|---------|
| `system` / `init` | a "external agent ready" note |
| `assistant` msg — `thinking` block | `thinking_chunk` (collapsed reasoning) |
| `assistant` msg — `text` block | `assistant_chunk` (streamed prose) + a committing `assistant` event carrying that step's token usage |
| `assistant` msg — `tool_use` block | `tool_use` (a real tool call in the pane + sidebar) |
| `user` msg — `tool_result` block | `tool_result` paired with its call |
| `result` | folded into the lane's single terminal `result` (cost, usage, steps, reason) |
| non-JSON line | surfaced verbatim as a dim system note (never silently dropped) |

## What external lanes expose — and the honest limits

- **Isolation is identical.** External lanes get a git worktree from the same
  base commit as every other lane, so their diffs are clean and comparable,
  and nothing they write touches your real tree.
- **Telemetry varies by protocol.** `stream-json` gives real cost/tokens/steps;
  `text` gives honest zeros (with a note) and real wall-clock. Chimera never
  fabricates a number it did not observe.
- **No steering, no follow-up queue.** A mid-run message cannot reach a
  `--print`-style CLI. Steering an external lane emits a polite system note
  ("steering is not supported — message not delivered") instead of pretending
  or crashing.
- **Conversation memory is the tool's own.** Each turn is a fresh CLI
  invocation. Chimera reconstructs a minimal user/assistant history for the
  cohort artifact and resume display; it does not replay prior turns into the
  external tool unless that tool persists its own session.
- **Cost is real money.** An external lane runs the real agent under your own
  login/credentials. Keep race tasks small.

## Persistence and resume

An external lane persists exactly like a Chimera lane: the manifest records its
profile (as model `ext:<profile>`, preset `external`) and telemetry; its
transcript, produced diff, and minimal history are written to the cohort
directory. Resuming (`--resume <id>`, `/resume`, or the `/cohorts` picker)
rebuilds the lane from its profile on a fresh worktree with the saved diff
re-applied, restores its telemetry so the scoreboard keeps accumulating, and
lets it run another turn.

## Programmatic use

The driver is usable without the TUI — it satisfies the same
`chimera.assembly.driver.DriverProtocol` the multiplexer drives:

```python
import asyncio
from chimera.assembly.external_driver import ExternalAgentDriver, resolve_external_profile
from chimera.core.loop_events import LoopEventType

async def main():
    driver = ExternalAgentDriver(resolve_external_profile("claude"), workdir="/tmp/lane-a")
    async for ev in driver.send("create hello.txt containing hi"):
        if ev.type is LoopEventType.result:
            print(ev.data.reason, ev.data.cost_usd, ev.data.turn_count)

asyncio.run(main())
```

`send()` streams `LoopEvent`s ending in exactly one `result`; `cancel()`
terminates the process group cleanly (SIGTERM first, kill only after a grace
window). See the multiplexer guide (`docs/guides/tui.md`) for the full racing
surface.
