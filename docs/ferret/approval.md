---
title: Ferret Approval Presets
description: Ferret's three approval presets — read-only, auto, full — and how to toggle the active preset mid-session via /approval.
---

# Approval — when ferret asks before acting

Ferret ships **single-flag approval presets**. Where mink lets you
hand-pick an `--allowed-tools` set and otter wires fine-grained
permission rules, ferret picks one of three policies and stops
asking. The flag is `--approval`. The mid-session slash command is
`/approval`.

This page covers the three presets, the matrix of what each allows,
and how to switch between them in a live session. Approval composes
with the sandbox — see [`sandbox.md`](sandbox.md) — so a tool call
has to clear both layers.

## The three presets

| Preset | Reads | Writes | Bash (low risk) | Bash (write/net) | Network tools |
|---|---|---|---|---|---|
| `read-only` (default) | allow | **deny** | allow | **deny** | **deny** |
| `auto` | allow | allow | allow | allow | allow |
| `full` | allow | allow | allow | allow | allow |

Read-only and auto differ in *what they allow*. Auto and full differ
in *whether they ever ask*. Full also overrides any per-tool
`ask` policy that might be set elsewhere — it's the "stop bothering
me" escape hatch.

## `read-only` (default)

Pairs with the `read-only` sandbox. The agent can inspect the repo
freely; any write or network attempt is denied with a structured
error the model can react to. There is no interactive ask in this
preset — denials are immediate, so non-interactive runs (CI,
scripted use, evals) work without a TTY.

**Typical use:**

```bash
chimera ferret -p "audit this repo and write the findings to stdout"
```

The agent will read, summarize, and reply through assistant text;
file edits will fail at the policy layer.

## `auto`

The everyday "let the agent work" preset. Writes and bash commands
are allowed without an interactive prompt. The sandbox still
constrains where writes land and whether the network is reachable —
this preset only opens the policy layer, not the OS layer.

**Typical use:**

```bash
chimera ferret --sandbox workspace-write --approval auto \
               -p "fix the failing test"
```

What `auto` does **not** do:

- It does not bypass the sandbox. `--sandbox read-only --approval auto`
  is still read-only.
- It does not bypass `chimera.permissions` policies set explicitly in
  config (e.g., a `.codex/config.toml` rule that hard-denies a
  specific bash pattern).
- It does not bypass `chimera/permissions/risk.py` denials for
  obviously destructive patterns (`rm -rf /`, `git push --force`,
  `git reset --hard origin/main` while uncommitted changes exist).

## `full`

The "I know what I'm doing" preset. Every tool is allowed, every
ask is suppressed, and even patterns that would normally hit the
hard-deny list (destructive bash patterns) are auto-confirmed.

This is the right preset for evals harnesses, CI bots, and
sandbox-isolated containers where the agent owns the whole machine.
It is the wrong preset for an interactive session against your
checkout of `chimera/`.

**Typical use:**

```bash
# Inside a disposable Docker container.
chimera ferret --sandbox workspace-write-network --approval full \
               -p "run the full eval suite"
```

The CLI prints a banner whenever `full` is active so the user is
never surprised:

```text
[ferret] sandbox=workspace-write-network approval=full model=gpt-5
[ferret] WARNING: --approval full disables every ask. Use only in
         disposable environments.
```

## Mid-session toggling — `/approval`

Inside the REPL, `/approval` shows the current preset and accepts a
new value:

```text
> /approval
sandbox=workspace-write approval=read-only

> /approval auto
[ferret] approval preset changed: read-only -> auto

> /approval read-only
[ferret] approval preset changed: auto -> read-only
```

Switching the preset takes effect on the **next tool call**.
In-flight tool calls finish under the policy that was active when
they started.

The sandbox is **not** togglable from inside the REPL. Sandbox modes
control kernel-level guards that have to be wired at process start.
To widen the sandbox, exit and restart with a new `--sandbox` flag.
This asymmetry is intentional: approval is policy, sandbox is OS.

## Composition with the sandbox

The two layers compose in series. A tool call goes:

```
agent -> approval check -> sandbox check -> Operations layer
```

The full matrix:

| Sandbox | Approval | Behavior |
|---|---|---|
| `read-only` | `read-only` | Reads only. Quiet. |
| `read-only` | `auto` | Reads only. Approval is moot. |
| `read-only` | `full` | Reads only. Approval is moot. |
| `workspace-write` | `read-only` | **Reads only** — approval blocks writes. |
| `workspace-write` | `auto` | Reads + writes inside cwd. |
| `workspace-write` | `full` | Reads + writes inside cwd, hard-deny patterns suppressed. |
| `workspace-write-network` | `read-only` | Reads only. |
| `workspace-write-network` | `auto` | Reads + writes inside cwd + network. |
| `workspace-write-network` | `full` | Same as auto, hard-deny patterns suppressed. |

The most common useful combinations are
`read-only`/`read-only` (auditing), `workspace-write`/`auto` (everyday
edits), and `workspace-write-network`/`auto` (let the agent install
a dep and use it).

## How presets are implemented

The preset selector lives in `chimera/ferret/approval.py`. It maps
the CLI flag to a `ConfirmationPolicy` from
`chimera.security.policy`:

| Preset | Policy |
|---|---|
| `read-only` | `AlwaysDeny` for write/exec; `NeverConfirm` for read. |
| `auto` | `NeverConfirm` for everything the sandbox allows. |
| `full` | `NeverConfirm` plus a `RiskClassifier` override that suppresses hard-deny patterns. |

The policy hooks into the standard
`chimera/permissions/audit.py` audit log, so even `full` runs are
reconstructable after the fact — every tool call is logged with the
preset that was active at decision time.

## Defaults from `~/.codex/config.toml`

```toml
[approval]
preset = "auto"
```

CLI `--approval` wins over config; config wins over env
(`FERRET_APPROVAL`); env wins over the built-in default
(`read-only`).

## When you reach for which preset

- **Auditing an unfamiliar repo / production tree.** `read-only`
  sandbox + `read-only` approval. Default; nothing extra needed.
- **Editing your own project.** `workspace-write` sandbox + `auto`
  approval. The 80% case.
- **Installing a dep, running tests, pushing a branch.**
  `workspace-write-network` + `auto`.
- **Eval harness in a container.** `workspace-write-network` +
  `full`. Make sure the container is disposable.

## See also

- [`sandbox.md`](sandbox.md) — sandbox modes, the OS-layer twin of
  approval.
- [`quickstart.md`](quickstart.md) — first-run flag examples.
- `chimera/ferret/approval.py` — preset implementation.
- `chimera/security/policy.py` — underlying `ConfirmationPolicy`.
- `chimera/permissions/audit.py` — audit log shared with mink/otter.
