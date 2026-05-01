---
title: Ferret Parity Matrix
description: Surface-by-surface parity status for chimera ferret versus the upstream IDE-first OpenAI-flagship coding agent.
---

# `chimera ferret` Parity Matrix

**Source baseline:** `research/ferret/SPEC.md` and the upstream
source-tree audit recorded under `research/ferret/`.
**Updated:** 2026-04-30, after wave-5 ship (FF1–FF8) and wave-6
cross-CLI verification (X1–X3, I-series). Refreshed at wave-9 close
(F1/F2/F3, O5) — three YELLOW rows promoted to GREEN.
**Legend:** GREEN = shipped / at parity (or superset); YELLOW =
partial; RED = deferred.

> **Wave-9 close (2026-04-30, D1).** Three carry-over items from
> the wave-5 follow-up list landed in this wave:
>
> - **OS-level sandbox hooks.** F1/W9 added Seatbelt
>   (`sandbox-exec`) on macOS and Landlock on Linux as a second
>   line of defence behind the wrapper-only sandbox. New module
>   `chimera/ferret/os_sandbox.py` + `--os-sandbox auto|on|off`
>   flag. Fail-open posture; one stderr warning per process when
>   neither primitive loads.
> - **IDE-friendly notification kinds over HTTP+SSE.** F2/W9
>   lifted the four `code/diff` / `editor/open_file` /
>   `terminal/output` / `progress/step` notification kinds from the
>   ACP stdio transport onto the HTTP+SSE transport so HTTP-bound
>   IDE plugins consume the exact same JSON payloads through
>   `GET /session/<id>/events`.
> - **Mid-session `/sandbox` and `/approval` slash toggles.**
>   F3/W9 wired `MutablePermissionPolicy` and `MutableSandboxMode`
>   so the slash commands actually re-shape the next tool call
>   (lock-guarded swap inside the `LoopConfig.permissions` proxy
>   and the `SandboxedEnvironment.mode_holder`).
>
> Plus the cross-CLI `chimera completion` generator (O5) covers
> ferret too. Reports live under
> `research/ferret/{F1,F2,F3}-W9-REPORT.md` and
> `research/O5-COMPLETION.md`. Live `pytest tests/ferret/` runs
> 375+ passed with the wave-9 additions; trademark scrub still
> exits OK at the codename-aggregate level (`passed: 7`).

> **Wave-6 verification (2026-04-30).** Live state at handoff:
> `uv run ruff check chimera/ferret/` clean; `uv run mypy
> chimera/ferret/` clean (part of the 36-source-file mypy run that
> covers ferret + weasel + shrew); `uv run pytest tests/ferret/ -q`
> = 303 passed in ~8.1s. Trademark scrub passes on all live source
> and 6 of 8 docs (the 2 hits are inside
> `docs/ferret/security-and-trademarks.md` quoting the regex itself
> — pre-existing, tracked as `F-FIX-1` in
> `research/ferret/HANDOFF.md`). `chimera ferret 0.5.0` boots,
> `--help` is brand-clean, and the 8 user docs total 1,729 lines.
> Ferret is now Tier 1 alongside mink and otter.

> **Trademark hygiene.** Throughout this document the upstream
> project is referred to as "the upstream" or "the IDE-first
> OpenAI-flagship coding agent". Live references to filesystem
> paths such as `~/.codex/config.toml` are kept because they are
> facts (the directory `chimera ferret` reads from on disk), not
> brand claims. See [`security-and-trademarks.md`](security-and-trademarks.md).

## Top-level CLI subcommands

The upstream CLI registers about a dozen top-level commands.
Ferret's surface focuses on the agent runtime + sandbox + IDE
bridge; admin / install / account flows are reused from the rest of
Chimera.

| Upstream surface | Ferret status | File | Notes |
|---|---|---|---|
| One-shot `exec` / `run` | GREEN | `chimera/ferret/cli.py` (`-p`/`--print`) | Mirrors the one-shot run flow: prompt arg, `--model`, `--cwd`, `--sandbox`, `--approval`, `--allowed-tools`, `--no-save`. |
| Interactive `code` / TUI | GREEN | `chimera/ferret/repl.py` | Streaming REPL with mid-turn steering and slash-command palette. |
| `serve` (default ACP) | GREEN | `chimera/ferret/ide.py` | Default `serve` transport is ACP over stdio. |
| `serve --http` | GREEN | shared `chimera/otter/server.py` | Opt-in HTTP server with the same surface as otter. |
| `bridge` (cloud) | GREEN | `chimera/ferret/cloud_bridge.py` | HTTPS bridge with bearer auth. |
| `sessions list` / `show` | GREEN | `chimera/ferret/sessions.py` | Read-only over the eventlog. |
| `sessions delete` | YELLOW | covered by `rm -rf <eventlog-dir>` | CLI verb deferred. |
| `agents list` / `show` | GREEN | shared `chimera/cli/code.py` | Honors `~/.codex/agent/*.md` ingest. |
| `agents create` | YELLOW | manual `mkdir + edit` | Interactive scaffolder deferred. |
| `mcp list` / `add` / `auth` | YELLOW | shared MCP runtime | `mcp list` shipped, `add` / `auth` covered by edit-config + restart. |
| `models` | GREEN | `chimera/ferret/providers.py` | `--model provider/model` everywhere, `FERRET_MODEL` env var pinned. |
| `auth login` (provider) | YELLOW | env vars + `chimera.auth` | Device-flow OAuth ridden via `chimera.auth`; per-provider login UX deferred. |
| `upgrade` / `uninstall` | RED | `pip install -U chimera-run` | Use the upstream packaging story. |
| `completion` | GREEN | `chimera/cli/completion.py` (`chimera completion bash\|zsh\|fish`) | Cross-CLI shell-completion generator (`--cli all\|<animal>`); covers ferret too. **Wave-9 close (O5).** |

## `run` (and `chimera ferret -p`) flag map

The upstream one-shot command exposes about twenty flags. Ferret
mirrors the ones that affect agent semantics or sandbox / approval
posture and skips the ones that are upstream-server-specific.

| Upstream flag | Ferret status | Ferret equivalent | Notes |
|---|---|---|---|
| `--model` / `-m` | GREEN | `--model`, `$FERRET_MODEL` | Identical syntax: `provider/model`. |
| `--sandbox` | GREEN | `--sandbox read-only\|workspace-write\|workspace-write-network` | First-class; default `read-only`. |
| `--approval` | GREEN | `--approval read-only\|auto\|full` | Single-flag preset; default `read-only`. |
| `--cwd` / `--dir` | GREEN | `--cwd` | Same semantics. |
| `--allowed-tools` | GREEN | `--allowed-tools Read,Bash,...` | Tool allowlist (composes with sandbox). |
| `--reasoning` / `--effort` | GREEN | `--thinking low\|medium\|high\|max` | Mapped to Chimera's `ThinkingLevel`. |
| `--no-save` | GREEN | `--no-save` | Skip eventlog. |
| `--output-format` | GREEN | `--output-format text\|json\|stream-json` | Adds `stream-json` (one event per line). |
| `--continue` / `-c` | YELLOW | `chimera ferret sessions show <id>` | Live one-shot resume deferred to wave-2. |
| `--session` / `-s` | YELLOW | `chimera ferret sessions show <id>` | Same. |
| `--max-steps` | GREEN | `--max-steps` | Inherited from shared CLI. |
| `--no-color` | GREEN | `--no-color`, `$NO_COLOR` | Inherited. |
| `--profile` | YELLOW | `~/.codex/config.toml` | Profile-switch UX deferred; users edit the config file. |
| `--image` | YELLOW | manual context inclusion | Image-attach plumbing deferred. |
| `--audio` | RED | n/a | Audio input deferred. |
| `--port` (serve) | GREEN | `chimera ferret serve --http --port` | Same. |
| `--remote-url` (bridge) | GREEN | `chimera ferret bridge --remote-url` | First-class. |
| `--auth-token` (bridge) | GREEN | `chimera ferret bridge --auth-token` | First-class. |
| `--insecure` (bridge) | GREEN | `chimera ferret bridge --insecure` | Dev-only HTTP fallback. |

## Slash-command palette (REPL)

The upstream TUI registers about twenty slash commands. Ferret
ships the standard Chimera REPL palette plus three ferret-specific
entries.

| Slash | Ferret status | Handler |
|---|---|---|
| `/help` | GREEN | shared `cmd_help` |
| `/exit` (`/quit`) | GREEN | shared `cmd_exit`, `cmd_quit` |
| `/sessions` | GREEN | `chimera/ferret/slash.py` `cmd_sessions` |
| `/new` (`/clear`) | GREEN | shared |
| `/model` (`/models`) | GREEN | shared |
| `/agent` (`/agents`) | GREEN | shared |
| `/init` | GREEN | shared |
| `/cost` | GREEN | shared |
| `/tools` | GREEN | shared |
| `/yolo` | GREEN | shared |
| `/compact` | GREEN | shared |
| `/status` | GREEN | shared |
| `/config` | GREEN | shared |
| `/mcp` (`/mcps`) | GREEN | shared |
| `/sandbox` | GREEN | **ferret-only**: print or change the current sandbox mode. As of wave-9 close (F3/W9), the toggle re-shapes the *next* tool call live via `MutableSandboxMode` (lock-guarded inside `SandboxedEnvironment.mode_holder`). |
| `/approval` | GREEN | **ferret-only**: print or change the preset. As of wave-9 close (F3/W9), the toggle re-shapes the *next* tool call live via `MutablePermissionPolicy` (lock-guarded swap inside `LoopConfig.permissions`). |
| `/bridge` | GREEN | **ferret-only**: cloud-bridge status. |
| `/edit`, `/undo`, `/redo` | YELLOW | shared stubs (carry-over from otter) |
| `/themes` | YELLOW | shared stub |
| `/workspaces` | RED | n/a (single-cwd model) |
| terminal-title toggle | RED | n/a |

## ACP / IDE schema

Ferret's ACP server is a strict superset of otter's. See
[`ide.md`](ide.md) for the full notification list.

| ACP method | Ferret status | Notes |
|---|---|---|
| `initialize` | GREEN | Advertises `sandbox`, `approval`, `bridge`. |
| `session/new` | GREEN | |
| `session/load` / `session/list` | GREEN | |
| `session/message` | GREEN | Streams `session/update`. |
| `session/cancel` | GREEN | Cooperative cancel. |
| `session/setMode` / `setModel` | GREEN | |
| `session/setApproval` | GREEN | **Ferret extension**. |
| `session/setSandbox` | YELLOW | Read-only (sandbox is fixed at process start). |
| `session/bridgeStatus` | GREEN | **Ferret extension**. |
| `permission/respond` | GREEN | Standard ACP. |

## Notification kinds

| `kind` | Ferret status |
|---|---|
| `agent_message_chunk` | GREEN |
| `tool_call_start` / `_progress` / `_end` | GREEN |
| `permission_request` | GREEN |
| `turn_end` | GREEN |
| `cost_update` | GREEN |
| `file_edit` | GREEN (ferret extension) |
| `file_open` | GREEN (ferret extension) |
| `sandbox_violation` | GREEN (ferret extension) |
| `approval_changed` | GREEN (ferret extension) |
| `notice` | GREEN |
| `code/diff` / `editor/open_file` / `terminal/output` / `progress/step` over HTTP+SSE | GREEN | **Wave-9 close (F2/W9).** Previously ACP-stdio only; the four IDE-friendly notification kinds now ride the HTTP+SSE transport too via `IDENotificationEmitter` so HTTP-bound IDE plugins consume the same payloads. |

## Sandbox modes

| Mode | Ferret status | Notes |
|---|---|---|
| `read-only` | GREEN | Default. Refuses writes + network. |
| `workspace-write` | GREEN | Writes inside cwd; no network. |
| `workspace-write-network` | GREEN | Writes inside cwd + network. |
| `off` | GREEN | Bypass; recommended only inside disposable container. |
| OS-level kernel sandbox (sandbox-exec / bubblewrap) | GREEN | Seatbelt (`sandbox-exec`) on macOS + Landlock on Linux land in `chimera/ferret/os_sandbox.py` behind `--os-sandbox auto\|on\|off`. Fail-open when the platform primitive is unavailable. macOS path is fully wired through `SandboxedEnvironment.run_command`; Linux Landlock requires forked-child invocation (helper exists). **Wave-9 close (F1/W9).** |

## Approval presets

| Preset | Ferret status | Notes |
|---|---|---|
| `read-only` | GREEN | Default. |
| `auto` | GREEN | Standard "let it work" preset. |
| `full` | GREEN | Suppresses asks + hard-deny patterns. |

## Configuration keys (`~/.codex/config.toml`)

The upstream config schema defines several dozen keys. Ferret
ingests the subset that maps onto first-class Chimera primitives
and ignores keys that drive upstream-only chrome.

| Upstream key | Ferret status | Notes |
|---|---|---|
| `model` | GREEN | Read by `chimera/ferret/config.py`. |
| `provider` | YELLOW | Default-chain wiring lives in `chimera/ferret/providers.py`; per-provider overrides honored opportunistically. |
| `sandbox.mode` | GREEN | Pinned default for `--sandbox`. |
| `approval.preset` | GREEN | Pinned default for `--approval`. |
| `mcp` (servers) | GREEN | Forwarded to `chimera.mcp`. |
| `agents` | GREEN | Combined with `~/.codex/agent/*.md`. |
| `commands` | GREEN | Combined with `~/.codex/command/*.md`. |
| `bridge.remote_url` | GREEN | Pinned default for `chimera ferret bridge`. |
| `bridge.auth_token` | RED | Deliberately not honored from config; env-var only. |
| `theme` | RED | TUI chrome only. |
| `keybinds` | RED | Ferret is a CLI; no keybinds. |
| `experimental.*` | RED | Defer until upstream stabilizes. |

## Counts

- **Top-level CLI subcommands:** 9 GREEN, 4 YELLOW, 1 RED of the 14
  reviewed (refreshed at wave-9 close — `completion` promoted to
  GREEN via the cross-CLI generator).
- **`run` flags:** 12 GREEN, 5 YELLOW, 1 RED of the 18 reviewed.
- **Slash commands:** 17 GREEN (3 ferret-only, with `/sandbox` and
  `/approval` now live-wired via wave-9 F3), 4 YELLOW, 2 RED of the
  23 reviewed.
- **ACP methods:** 9 GREEN (2 ferret extensions), 1 YELLOW.
- **Notification kinds:** 11 GREEN (4 ferret extensions, plus the
  HTTP+SSE row added at wave-9 close).
- **Sandbox modes:** 5 GREEN, 0 YELLOW (wave-9 close — OS-level
  kernel sandbox promoted to GREEN via `os_sandbox.py`).
- **Approval presets:** 3 GREEN.
- **Config keys:** 7 GREEN, 1 YELLOW, 4 RED of the 12 reviewed.

> **Wave-6 live verification:** the GREEN counts above were spot-checked
> against `chimera ferret --help`, the 303 passing tests under
> `tests/ferret/`, and the 13 source modules under `chimera/ferret/`.
> No row was downgraded by the wave-6 audit; ferret meets every
> contract above at the black-box level.

## Chimera-only capabilities (do not regress)

Ferret inherits the same Chimera-only superset that mink and otter
already advertise: cooperative `CancellationToken`, mid-turn
`MessageQueues` steering, loop detection, ghost commits, instruction
anchor, learning store, discipline guards, `EventSourcedSession`
crash recovery, `FileAwareCompaction`, `SessionTree` in-place
branching, `RedactionMiddleware`, `CostTracker` with cache /
reasoning breakdown, the 26-event `EventBus`, and the multi-tier
`AgentConfig.from_markdown()` registry.

The four pieces that are unique to ferret in this wave:

1. **Sandbox-first.** Default `read-only` sandbox; mode pinned at
   process start via `--sandbox`.
2. **Single-flag approval.** Three named presets via `--approval`
   instead of fine-grained tool allowlists.
3. **IDE-first ACP.** ACP over stdio is the default `serve`
   transport, with four extension notification kinds for editor
   integration.
4. **Cloud bridge.** `chimera ferret bridge` for driving a local
   session from a remote UI.

## Follow-up issues to file (or already filed)

1. `chimera ferret --continue` / `--session <id>` (one-shot resume).
2. ~~Native OS sandbox hooks (sandbox-exec on macOS, bubblewrap on
   Linux) replacing the heuristic enforcement.~~ Shipped at wave-9
   close (F1/W9 — Seatbelt fully wired, Landlock helper).
3. `chimera ferret agents create` (interactive agent scaffolder).
4. `chimera ferret session delete <id>` (currently `rm -rf` the
   eventlog dir).
5. Image / audio attachment flags for one-shot prompts.
6. Profile switcher (`--profile <name>`) reading
   `~/.codex/config.toml` `[profiles.<name>]` blocks.
7. ~~`chimera ferret completion` (shell-completion script).~~
   Shipped at wave-9 close as the cross-CLI `chimera completion`
   generator (O5; `--cli ferret` filters to ferret-only output).
8. Per-provider `auth login` UX wiring `chimera.auth` device-flow.
9. ~~Mid-session `/sandbox` and `/approval` toggle that re-shapes
   the next tool call live.~~ Shipped at wave-9 close (F3/W9 —
   `MutablePermissionPolicy` + `MutableSandboxMode`).
10. ~~Lift the four IDE-friendly notification kinds onto the
    HTTP+SSE transport.~~ Shipped at wave-9 close (F2/W9 —
    `IDENotificationEmitter`).

## How to use

When a user runs `chimera ferret` from a project that already
contains `~/.codex/config.toml`, every GREEN row above is expected
to behave in lockstep with the upstream IDE-first OpenAI-flagship
coding agent at the black-box level (one-shot prompt, slash
command, MCP server discovery, agent / command ingest, sandbox +
approval defaults). YELLOW rows degrade gracefully and emit a hint
where the gap is user-visible. RED rows are not implemented and
will return a "not yet wired" notice with a pointer to the issue
tracker entry.

## See also

- [`quickstart.md`](quickstart.md)
- [`sandbox.md`](sandbox.md), [`approval.md`](approval.md)
- [`ide.md`](ide.md), [`cloud-bridge.md`](cloud-bridge.md)
- [`security-and-trademarks.md`](security-and-trademarks.md)
- [`../otter/parity-matrix.md`](../otter/parity-matrix.md) — sibling
  parity matrix.
- [`../mink/parity-matrix.md`](../mink/parity-matrix.md) — sibling
  parity matrix.

## Cross-links

- [Chimera architecture (8-phase map)](../architecture.md) — where the rows in this matrix live in the shared library.
