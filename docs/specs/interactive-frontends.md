# Interactive Frontends: Single-Agent TUI & Multi-Agent Multiplexer

**Status:** design spec (implementation-independent).
**Scope:** the interactive presentation layer for `chimera code` — how a user
drives one or more coding agents from a terminal.
**Relationship to existing code:** *purely additive.* The line-oriented REPL
that ships today is unchanged. The single-agent TUI (Phase 1) and the
multiplexer (Phase 2) are **new frontends that sit beside it**, all three
speaking to the same agent driver through the same contract. Nothing here
removes, replaces, or gates the REPL.

This document deliberately names **no rendering technology**. It specifies
behavior, contracts, state, and layout in the abstract so the design outlives
any particular toolkit. Implementation notes that reference concrete
libraries live with the code, not here.

---

## 1. Motivation & Goals

The REPL is a line-at-a-time transcript: excellent for scripting and simple
sessions, limited for expressive interaction (live regions, panes, mid-run control,
comparison). Two additive frontends close that gap:

- **Phase 1 — Single-agent TUI.** A full-screen interactive frontend for one
  agent: a scrolling transcript with live streaming and tool-call rendering, a
  persistent status region, a capable input region, and mid-run control (cancel,
  steer). Feature parity of *intent* with the REPL, richer presentation.
- **Phase 2 — Multiplexer.** *N* independent agent sessions ("lanes") running
  **concurrently, side-by-side**, most often the same task across different
  models / loops / presets. This is the comparison mission rendered as an
  interface: watch several agents attack one problem and compare cost, tokens,
  time, and outcome in real time.

### 1.1 Goals

1. **One contract, many frontends.** REPL, single-agent TUI, and multiplexer
   are interchangeable clients of the same driver. Adding a frontend never
   requires changing agent, loop, tool, or provider code.
2. **Presentation-only frontends.** A frontend owns widgets, layout, input,
   and focus — never agent state, tool dispatch, provider plumbing, or
   persistence logic. Those live behind the driver.
3. **Live, faithful streaming.** Assistant text, reasoning, tool invocations,
   tool progress, and results render as they happen, in order, without flicker
   or loss, and without misrepresenting completion.
4. **Mid-run control.** The user can cancel, and can inject guidance into a
   running turn ("steer") or queue it for after ("follow-up"), from any
   frontend.
5. **Comparison as a first-class capability** (Phase 2): aligned inputs,
   per-lane telemetry, and side-by-side outcomes.
6. **Graceful degradation.** Narrow terminals, no-color terminals, missing
   optional capabilities, and non-interactive contexts all degrade to a usable
   subset rather than failing.

### 1.2 Non-goals

- Replacing or deprecating the REPL.
- A graphical or web frontend (the contract is designed to *allow* one later;
  this spec covers terminal frontends only).
- Editing agent behavior, tool semantics, or provider logic.
- A general window manager. The multiplexer arranges agent lanes, not
  arbitrary programs.

---

## 2. Principles

- **P1 — Additive.** Every capability here is opt-in and side-by-side with the
  REPL. Absence of the optional presentation capability degrades to the REPL,
  never to an error at import time.
- **P2 — Driver is the boundary.** A frontend interacts with an agent *only*
  through the driver contract (§3). It never reaches past it.
- **P3 — Events are the truth.** The transcript is a rendering of an ordered
  event stream. The frontend derives all display state from events; it never
  infers agent state by other means.
- **P4 — Deterministic rendering.** Given the same ordered event stream and
  terminal size, a frontend produces the same visible result. This makes the
  UI testable with a scripted driver and a headless surface.
- **P5 — No blocking on the UI thread.** Agent turns run asynchronously; the
  input and render loop stay responsive throughout.
- **P6 — Isolation by default (multiplex).** Concurrent lanes must not corrupt
  each other's workspace or state (§6.2).

---

## 3. The Shared Contract (the Driver)

Every frontend drives an agent through a single object, referred to here as
**the Driver**. The Driver is the sole integration seam; it already exists in
the codebase and is reused unchanged.

### 3.1 Event stream

A turn is initiated with a user message and produces an **ordered, asynchronous
stream of typed events**. A frontend consumes the stream and renders each
event. The vocabulary (names are conceptual; the codebase realizes them as a
single event type with a discriminator):

| Event | Payload | Meaning | Rendering obligation |
|-------|---------|---------|----------------------|
| `turn.begin` *(implicit)* | — | the driver accepted a turn | mark the lane "running", start any live region |
| `assistant.delta` | text fragment | streaming assistant text | append to the live/uncommitted region |
| `assistant.message` | full assistant text | the assistant block is complete | commit the accumulated text; clear the live region |
| `tool.invoked` | tool name + arguments | the agent decided to call a tool (emitted **before** execution) | render the call (name + argument preview) |
| `tool.progress` | partial output | in-flight tool output | *ephemeral*: may show live, **must not** be persisted as a result |
| `tool.result` | tool name + result (output, success, metadata) | a tool finished | render the outcome (and any diff/structured payload) |
| `context.compacted` | boundary marker | history was summarized to fit the window | a subtle, non-intrusive marker |
| `error` | message / classification | a recoverable or surfaced error | render as an error; see §5.8 for recoverable withholding |
| `turn.result` | reason, step count, cost, usage, final messages | the turn ended | render a compact footer; update telemetry; mark lane idle |
| `system` | text | out-of-band notice (e.g., a local command result) | render as a system line |

**Ordering guarantee.** Events for a turn arrive in causal order: a tool's
`tool.invoked` precedes its `tool.result`; `assistant.delta`s precede the
`assistant.message` that commits them. Frontends rely on this and never
reorder.

**Ephemerality guarantee.** `tool.progress` is display-only and is never part
of the persisted transcript or the model's context. Results are.

**Termination guarantee.** Every accepted turn ends with exactly one
`turn.result` (including on cancellation and on unrecoverable error), so a
frontend can always return to idle.

### 3.2 Control surface

| Operation | Effect |
|-----------|--------|
| `send(text)` | begin a new turn; yields the event stream |
| `steer(text)` | inject a message into the **currently running** turn, delivered between tool boundaries |
| `follow_up(text)` | queue a message delivered **after** the current turn would otherwise stop |
| `cancel()` | cooperatively abort the running turn (takes effect at the next safe step); still yields a terminal `turn.result` |
| `clear()` | forget conversation history; the next `send` starts fresh |

### 3.3 State surface

Read-only properties a frontend renders in status regions:

- **model** (wire id), **context window** (tokens), **tool set** (names/count),
- **cumulative cost**, **turn count**, **conversation length**,
- **liveness** (idle / running), derived by the frontend from turn boundaries.

### 3.4 Driver identity in the multiplexer

Each multiplexer lane owns **its own independent Driver instance** (its own
model, loop, preset, workspace, history, cost). Lanes share nothing mutable.
This is what makes side-by-side comparison sound.

---

## 4. Frontends Overview (the additivity map)

```
                         ┌───────────────────────────┐
                         │        the Driver         │   (unchanged; §3)
                         │  send · steer · cancel     │
                         │  event stream · state      │
                         └─────────────┬─────────────┘
             one driver instance ......│...... N driver instances
        ┌──────────────┬───────────────┼───────────────┐
        ▼              ▼               ▼                ▼
   ┌─────────┐   ┌──────────┐   ┌───────────┐   ┌──────────────────────┐
   │  REPL   │   │ Phase 1  │   │  (future  │   │      Phase 2         │
   │ (ships  │   │  TUI     │   │  non-tty  │   │   Multiplexer        │
   │  today) │   │ 1 driver │   │  frontend)│   │  N lanes, N drivers  │
   └─────────┘   └──────────┘   └───────────┘   └──────────────────────┘
```

All frontends share: the Driver contract, the event vocabulary, session
persistence, model/credential/workspace resolution, and — as far as possible —
the slash-command vocabulary (§5.5). A capability added to the Driver (e.g., a
new event type) is available to every frontend for free.

**Selection.** The frontend is chosen at launch by additive flags (§7). With
no flag, the REPL runs exactly as today.

---

## 5. Phase 1 — Single-Agent TUI

A full-screen frontend bound to exactly one Driver.

### 5.1 Layout regions

Four stacked regions on the alternate screen (a fifth, the sidebar, is
optional and deferred to a later phase):

```
┌────────────────────────────────────────────────────────────────┐
│ STATUS   model · N tools · $cost · <idle|running> · ctx window   │  1 row
├────────────────────────────────────────────────────────────────┤
│ TRANSCRIPT (scrollable, sticky-to-bottom)                        │
│   › user message                                                 │
│   assistant text …                                               │
│   ⚙ tool_name(arg preview)                                       │  flexible
│   result / diff                                                  │  height
│   · N steps · $turn · $total                                     │
│   [LIVE region: uncommitted streaming text for the in-flight turn]│
├────────────────────────────────────────────────────────────────┤
│ INPUT   ▸ multi-line entry, slash hints                          │  1..K rows
├────────────────────────────────────────────────────────────────┤
│ FOOTER  key hints (contextual)                                   │  1 row
└────────────────────────────────────────────────────────────────┘
```

**Region contracts**

- **Status** — single line; reflects the Driver's state surface (§3.3) and
  liveness; updates on `turn.begin` / `turn.result` and whenever cost changes.
- **Transcript** — an append-only log of *committed items* (§5.2) plus one
  transient **live region** pinned to the bottom that holds the in-flight
  turn's uncommitted content. Sticky-scroll: new content keeps the view at the
  bottom unless the user has scrolled up (then the position is preserved and a
  "new content below" affordance is shown).
- **Input** — a focusable, editable region supporting multi-line entry.
- **Footer** — contextual key hints; content depends on mode (idle vs running).

### 5.2 Transcript model

The transcript is a list of **committed items**, each of one type:

| Item | Source event(s) | Notes |
|------|-----------------|-------|
| user | user submission | echoed immediately on submit |
| assistant | `assistant.delta*` → `assistant.message` | text accumulated live, committed once |
| tool-call | `tool.invoked` | name + truncated argument preview |
| tool-result | `tool.result` | output (truncated past a threshold), success styling, optional structured/diff payload |
| notice | `system`, `context.compacted` | subtle, dim styling |
| error | `error` | prominent styling |
| turn-footer | `turn.result` | steps · turn cost · cumulative cost |

**Commit semantics (the anti-flicker rule).** Streaming assistant text lives in
the **live region** as it arrives (`assistant.delta`). When the block completes
(`assistant.message`, or the turn ends), it is **committed** as a single
assistant item and the live region is cleared. This yields exactly one
permanent rendering of each block and no re-flow of already-committed content.
Tool calls and results commit immediately (they are discrete), giving the
"work is happening" feel while long assistant prose streams smoothly.

**Content safety.** Arbitrary agent/tool text may contain markup-significant
characters. The frontend MUST render dynamic content literally (escaped), and
reserve any styling markup for its own fixed labels.

**Truncation.** Tool output beyond a configurable size is rendered head+tail
with an elision marker; the full result remains available to the agent (it is
the Driver's concern, not the frontend's).

### 5.3 Streaming & tool-call lifecycle

A tool call has a visible lifecycle:

1. `tool.invoked` → render `⚙ name(preview)` immediately.
2. `tool.progress` (optional, repeated) → update an ephemeral progress line
   beneath the call; never persisted.
3. `tool.result` → replace/append the outcome: success or failure styling,
   output (truncated), and any structured payload (e.g., a diff) rendered in a
   dedicated form.

Reasoning/thinking content (if the Driver surfaces it) renders as a
collapsible, dim block, defaulting to collapsed in a "hide" mode.

### 5.4 Input model & the input router

The input region is always editable; **the meaning of a submission depends on
liveness.** A single routing function classifies each submission:

```
classify(text, running) →
    text == ""                         → NOOP
    text starts with command prefix    → LOCAL_COMMAND
    running                            → STEER            (inject into the running turn)
    not running                        → NEW_TURN
```

- **NEW_TURN** — echo the user item, begin `send(text)`, enter running mode.
- **STEER** — call `steer(text)`; render a distinct "steer" marker in the
  transcript so the user sees it was injected, not started anew.
- **LOCAL_COMMAND** — handled entirely in the frontend (§5.5); no turn.
- A **follow-up** variant (queue-for-after) is available via a distinct submit
  gesture (e.g., a modifier+submit) and calls `follow_up(text)`.

**Multi-line.** A newline gesture (distinct from submit) inserts a line break;
submit sends. History navigation recalls prior submissions when the cursor is
at a boundary. Optional slash **autocomplete** filters the command catalog as
the user types the prefix.

### 5.5 Slash commands

Local, no-agent-call commands. The catalog SHOULD align with the REPL's where
meaning is shared, so muscle memory transfers.

| Command | Effect |
|---------|--------|
| `/help` | list commands + key hints |
| `/model` | show model + context window |
| `/cost` | show cumulative cost |
| `/tools` | list available tools |
| `/history` | show conversation length / summary |
| `/clear` | `clear()` the conversation and the transcript |
| `/exit`, `/quit` | leave the frontend (agent state persisted per session rules) |

Unknown commands render an error line and are otherwise inert. The catalog is
extensible; commands that only affect presentation (theme, detail level, scroll)
are frontend-local and need no Driver support.

### 5.6 Keybinding semantics

Bindings are specified by **meaning**, not by specific keys (keys are a
configuration detail):

| Meaning | Behavior | Availability |
|---------|----------|--------------|
| submit | route the current input (§5.4) | always |
| newline | insert a line break in the input | always |
| cancel | if running → `cancel()`; if idle → quit | always (priority) |
| quit | leave the frontend | always |
| clear | `clear()` + clear transcript | always |
| scroll up/down, page up/down | move the transcript viewport | always |
| focus cycle | move focus between regions | always |
| toggle detail / collapse | show/hide reasoning or verbose tool output | idle or running |

`cancel` MUST be a priority binding so it interrupts a running turn even while
the surface is busy rendering.

### 5.7 Turn lifecycle & cancellation

```
idle ──submit(new)──▶ running ──event stream──▶ (renders) ──turn.result──▶ idle
  ▲                      │
  │                      ├─ submit(text)   → steer()      (stays running)
  │                      ├─ submit(mod)    → follow_up()  (stays running)
  │                      └─ cancel         → cancel()      → terminal turn.result → idle
  └───────────────────────────────────────────────────────────────────────────────┘
```

- Exactly one turn is in flight per single-agent frontend.
- Cancellation is cooperative: the frontend requests it, shows a "cancel
  requested" marker, and returns to idle on the guaranteed terminal
  `turn.result`.
- The input region remains editable while running (to allow steer/follow-up);
  submissions are routed per §5.4.

### 5.8 Errors, recovery, edge cases

- **Recoverable errors withheld by the Driver** (e.g., context-too-long,
  output-cap) are handled below the frontend; the frontend only renders an
  `error` event if recovery ultimately fails. It never surfaces a
  transient/withheld condition as a user-facing failure.
- **Driver/exception during a turn** → render an error item; guarantee return
  to idle (the terminal `turn.result` or a synthesized terminal state).
- **Empty submit** → no-op.
- **Command error** → error line; no turn.
- **Cancel with no running turn** → quit (per §5.6).
- **Very long single line / very wide content** → wrap or horizontally clip per
  region; never break layout.
- **Terminal resize** → re-layout; preserve scroll position and committed
  content.

### 5.9 Persistence, cost, model/context

- Conversation memory across turns is the Driver's responsibility; the frontend
  renders it and offers `/clear`.
- Session save/resume reuses the existing session subsystem; the TUI is a
  client of it, identical to the REPL.
- Cost accrues in the Driver; the frontend reads cumulative cost for status and
  per-turn cost from `turn.result`.
- Model, context window, credentials, and workspace are resolved by the same
  launch path as the REPL (including environment/`.env` resolution), then
  handed to the Driver.

### 5.10 Concurrency & responsiveness

The turn runs as an asynchronous task; the input/render loop is never blocked.
Exactly one turn task exists at a time (single-agent). Rendering updates are
applied on the frontend's own loop, coalesced to avoid excessive redraws under
rapid streaming.

### 5.11 Accessibility & degradation

- **No color / limited terminal** → semantic styling degrades to plain text
  with plain-text markers (e.g., prefixes for user/assistant/tool/error).
- **Narrow terminal** → the optional sidebar (later phase) auto-hides; the
  transcript remains full-width.
- **Non-interactive stdout** (piped, no tty) → the full-screen frontend is not
  launched; the caller falls back to the REPL or the non-interactive print
  path. Selecting the TUI in a non-tty context is a clear, early error, not a
  crash.

---

## 6. Phase 2 — Multiplexer

A full-screen frontend hosting **N lanes**, each an independent single-agent
session, rendered concurrently.

### 6.1 Concept & terminology

- **Lane** — one agent session: its own Driver instance, model, loop, preset,
  workspace, history, cost, and transcript. A lane is, essentially, a Phase-1
  TUI reduced to a pane.
- **Cohort** — the set of lanes in a multiplexer instance, typically launched
  together to run the **same task** under **controlled variables** (this is the
  comparison use case).
- **Focused lane** — the lane receiving targeted input and key events.

### 6.2 Lane model & isolation (critical)

Because lanes may run coding agents that **write files and run commands**, they
MUST NOT share a mutable workspace, or they will corrupt each other's results
and invalidate the comparison.

Isolation requirements:

- **R-ISO-1** Each lane operates in its **own workspace** — a private working
  directory (a copy, a per-lane clone, or an overlay). Read-only source may be
  shared; anything a tool can mutate MUST be per-lane.
- **R-ISO-2** Each lane has its **own Driver, history, cost, and event stream**.
- **R-ISO-3** A lane's cancellation, error, or completion never affects another
  lane.
- **R-ISO-4** Credentials/config are resolved per lane (lanes may target
  different providers).
- **R-ISO-5** The cohort records enough metadata (per-lane model/loop/preset,
  workspace path, seed inputs) to reproduce and to attribute results.

Workspace provisioning strategy is pluggable (copy, isolated clone, container,
or ephemeral sandbox); the spec requires *isolation*, not a specific mechanism.

### 6.3 Layout

```
┌───────────────────────────────────────────────────────────────────────┐
│ GLOBAL STATUS   task: "…"   lanes: 3   done: 1/3   Σcost $…   elapsed …  │
├───────────────────────┬───────────────────────┬───────────────────────┤
│ LANE A                │ LANE B                │ LANE C                 │
│ model · $ · running   │ model · $ · running   │ model · $ · done       │  per-lane
│ transcript…           │ transcript…           │ transcript…            │  status +
│ ⚙ edit_file(…)        │ ⚙ bash(pytest)        │ · 6 steps · $0.01      │  transcript
│ …                     │ …                     │ ✔ 2 passed             │
├───────────────────────┴───────────────────────┴───────────────────────┤
│ INPUT   ▸ (broadcast) or ▸@A (targeted)         [broadcast|target: A]   │
├───────────────────────────────────────────────────────────────────────┤
│ FOOTER  focus ⇄ · broadcast toggle · cancel(lane|all) · key hints       │
└───────────────────────────────────────────────────────────────────────┘
```

- **Arrangement** — lanes tile as columns (wide terminals), rows, or a grid;
  the arrangement is responsive to terminal size and lane count.
- **Per-lane region** — a compact status header (model · cost · liveness · step
  count) over that lane's transcript (same item model as §5.2, scaled to the
  pane).
- **Global status** — cohort-level: the shared task, lane count, completion
  progress, aggregate cost, elapsed wall-clock, and a "first to finish" marker.
- **Degradation** — below a width threshold, lanes collapse from side-by-side
  tiles to a **tabbed** arrangement (one visible, quick-switch between them),
  preserving each lane's live state off-screen.

### 6.4 Input routing

Input has a **routing mode**, shown in the input region:

- **Broadcast** (default for a comparison cohort) — a submission is sent to
  **all lanes** simultaneously (`send` per lane). This is the "race the same
  task" action.
- **Targeted** — a submission goes only to the **focused lane**
  (`send`/`steer` on that lane), for per-lane follow-ups or corrections.

Routing rules extend §5.4 per addressed lane(s): for each target lane, if that
lane is running the submission steers it; if idle it starts a new turn. A
modifier submits as follow-up. The user toggles broadcast/targeted and moves
focus with dedicated bindings.

### 6.5 Aggregate & comparison affordances (the mission)

Phase 2's reason to exist is comparison. It SHALL provide:

- **Aligned inputs** — the cohort records the exact shared task and each lane's
  controlled variables (model/loop/preset), so differences are attributable.
- **Live telemetry per lane** — cost, token usage, step/turn count, elapsed
  time, liveness, and terminal reason (completed / cancelled / error /
  loop-detected / budget-hit).
- **Cohort summary** — an at-a-glance panel or footer: who finished, in what
  order, at what cost, with what outcome.
- **Outcome comparison (optional, later)** — a diff/side-by-side of each lane's
  produced changes or final answer, and an **export** of the cohort run (inputs,
  per-lane telemetry, transcripts, produced artifacts) as a portable comparison
  record.

These affordances reuse the project's existing comparison/telemetry concepts;
the multiplexer is their live, interactive front.

### 6.6 Lifecycle

```
launch(cohort spec) → provision N isolated workspaces → construct N lanes(idle)
      │
      ├─ broadcast submit(task) → all lanes: send(task) → each streams independently
      │
      ├─ per-lane events render into that lane's pane (isolated)
      │
      ├─ cancel(lane)  → that lane only
      ├─ cancel(all)   → every running lane
      │
      └─ each lane ends with its own turn.result → global "done k/N" advances
            → when all idle: cohort summary available; user may broadcast again
```

- Lanes are launched idle; the first broadcast starts the race.
- Lanes run **fully concurrently**; one lane's latency never stalls another's
  rendering.
- Completion is tracked per lane; the cohort is "done" when all lanes are idle.

### 6.7 Concurrency, resource limits, backpressure

- **R-CON-1** Up to N lanes run concurrent turns; N is bounded by a configurable
  cap to protect CPU, memory, network, and provider rate limits.
- **R-CON-2** Rendering is per-lane and coalesced; a chatty lane cannot starve
  others' updates.
- **R-CON-3** If provisioning or a provider fails for a lane, that lane enters a
  clearly-marked error state; the cohort continues.
- **R-CON-4** A per-lane and cohort-wide budget (cost / tokens / steps / wall
  clock) MAY bound the race; exceeding it ends the lane with a distinct terminal
  reason.

### 6.8 Persistence & export

- Each lane persists as an ordinary session (reusing the session subsystem).
- The cohort persists a manifest binding the lanes, the shared task, and the
  controlled variables, enabling resume and reproducibility.
- Export produces a self-contained comparison artifact (§6.5).

---

## 7. CLI Surface (additive)

All additions are new, optional flags/subcommands; existing invocations behave
exactly as today.

- **Single-agent TUI** — an opt-in flag on the existing interactive entry
  selects the full-screen frontend instead of the line REPL, reusing all
  existing model/workspace/credential resolution. No flag ⇒ REPL.
- **Multiplexer** — an opt-in flag plus a **cohort specification**: a list of
  lane definitions (each = model and/or loop and/or preset), a shared task
  (optional; can be entered interactively), and a workspace-isolation strategy.
  The comparison-oriented "multi" entry point is the natural host.
- Selecting a full-screen frontend in a non-interactive context is a clear
  early error (§5.11), never a crash.

Flag *names* are an implementation detail and intentionally unspecified here.

---

## 8. Data Model (conceptual)

```
Frontend            selects a driver-configuration and renders its events
  └─ TranscriptItem { kind, content, style, timestamp }      // §5.2
LaneState (multiplex)
  ├─ id, label
  ├─ driver               // independent instance
  ├─ config               // model, loop, preset, workspace, credentials
  ├─ transcript: [TranscriptItem]
  ├─ telemetry            // cost, tokens, steps, elapsed, liveness, terminal reason
  └─ workspace            // isolated path/handle
Cohort (multiplex)
  ├─ task                 // shared seed input
  ├─ lanes: [LaneState]
  ├─ routing              // broadcast | targeted(focus)
  └─ manifest             // controlled variables, reproducibility metadata
```

All of `driver`, `config`, `telemetry`, transcripts, and sessions reuse
existing project concepts; the data model above is the *frontend's* view.

---

## 9. Testing Strategy

- **Deterministic driver double.** A scripted stand-in emitting a fixed,
  ordered event stream drives the frontend under a **headless surface** (no real
  terminal). Assertions check the rendered transcript, region contents,
  liveness transitions, and input routing — no network, no real agent.
- **Golden rendering.** For a given event script + surface size, the visible
  output is stable and snapshot-comparable (P4).
- **Input-router unit tests.** `classify()` (§5.4) and the multiplex routing
  extension (§6.4) are pure and tested exhaustively.
- **Isolation tests (multiplex).** Two lanes writing the "same" relative path
  must not observe each other's writes (R-ISO-1).
- **Live smoke.** A single real turn through the frontend against a real
  provider, via the headless surface, confirming end-to-end wiring (kept small
  and rare).
- **Degradation tests.** No-tty, narrow width, and no-color paths.

---

## 10. Security & Safety

- Frontends inherit the agent's permission/approval model unchanged; a
  full-screen surface presents approval prompts as modal interactions but grants
  no new authority.
- Multiplex isolation (§6.2) is a safety property, not only correctness: N
  agents running shell/file tools in a shared tree is a footgun; per-lane
  workspaces contain the blast radius.
- Secrets in transcripts follow existing redaction rules; the frontend renders
  already-redacted content.

---

## 11. Open Decisions

1. **Workspace isolation mechanism** for lanes (copy vs isolated clone vs
   container vs ephemeral sandbox) — default and override policy.
2. **Default routing mode** for a fresh cohort (broadcast vs targeted).
3. **Lane cap** default and how to signal over-cap.
4. **Reasoning display** default (collapsed vs hidden vs shown).
5. **Outcome-diff** scope for §6.5 (produced file changes, final message, or
   both) and its export format (may reuse an existing trajectory/report format).
6. **Cohort budget** semantics — per-lane, aggregate, or both; default off.
7. **Sidebar** content and trigger (single-agent Phase 3).

---

## 12. Phasing

| Phase | Status | Deliverable | Depends on |
|-------|--------|-------------|-----------|
| **1** | ✅ shipped | Single-agent full-screen TUI: regions §5.1, transcript/commit §5.2–5.3, input router §5.4, slash cmds §5.5, keys §5.6, lifecycle/cancel §5.7, errors §5.8 | the Driver (exists) |
| **2** | ✅ shipped | Multiplexer: lanes + isolation §6.2, layout §6.3, routing §6.4, telemetry/summary §6.5, lifecycle §6.6, limits §6.7 | Phase 1 + isolation strategy |
| **3** | 🟡 in progress | Polish & depth — detailed in §13 | Phases 1–2 |

The REPL remains a first-class, unchanged frontend throughout all phases.

---

## 13. Phase 3 — detailed scope

Phase 3 is the polish-and-depth lap. The items are independent and land
additively — none changes the Phase 1–2 contract — so they can ship one at a
time. Priority is by user-visible value.

### P1 — comparison, made visible

**13.1 In-UI cohort comparison view** (§6.5) — *the flagship.* Today the
multiplexer's payoff (what each model actually produced) lives in
`~/.chimera/cohorts/<id>/lane-*.diff`; Phase 3 surfaces it inside the TUI. On
cohort completion — and on demand via a key / `/results` — a full-screen overlay
renders the ranked scoreboard (label · model · outcome · cost · tokens · steps ·
time · finish order) over a per-lane **diff viewer** (cycle lanes to read each
lane's produced changes).
*Acceptance:* real diffs from each lane's workspace; keyboard-navigable across
2–N lanes; a clear placeholder for empty/no-diff lanes; escapes back to the live
panes without losing state.

**13.2 Resumable per-lane sessions** (§6.8) — ✅ **shipped.** Closes the
persistence gap. `persist()` writes each lane's faithful conversation history
(`lane-<id>.history.json`, round-tripping tool calls so the next request stays
valid). `chimera code --tui --resume <cohort-id>` (ids via `--list-cohorts`)
reconstructs each lane: a fresh workspace from the recorded base commit with the
saved diff re-applied, a driver seeded via `load_history`, and restored
telemetry. Lanes start idle; the next broadcast continues the race.
*Verified live (GLM-5.2):* full history + produced file restored, then a second
turn built on it coherently.

### P2 — depth

**13.3 Heterogeneous agent backends per lane** (§6.1) — ✅ **shipped** (partial).
The cohort spec is now `model[:preset[:loop]]`: each lane varies model, preset
(tool set + prompt), and a **loop posture** (`plan` = plan-first, `tdd` =
test-first) applied as a system-prompt augmentation *within* `AgentLoop`.
`LaneConfig.loop` is recorded in the manifest, and unknown presets/loops are
rejected with a clear error. Full reasoning-loop swaps (plan-execute / reflexion
/ tree-of-thought) are deferred: only `AgentLoop` emits the `LoopEvent`s the TUI
renders, so those loops need an event-emitting adapter first.
*Verified live (GLM-5.2):* act-first vs plan-first lanes race one task; the
manifest records each lane's loop.

**13.4 Reasoning display** (§5.3) — when the driver surfaces reasoning/thinking,
render it as a collapsible, dim block, default collapsed, with a toggle key.

### P3 — polish

- **13.5 Multi-line input** (§5.4) — a Textual `TextArea`; distinct newline vs
  submit gestures; history recall at input boundaries.
- **13.6 Slash autocomplete** (§5.4) — filter the command catalog as the `/`
  prefix is typed.
- **13.7 Sidebar** (§5.11/§6.3) — per-lane tool-call timeline / file tree;
  auto-hides on narrow terminals.
- **13.8 Richer diff forms** (§6.5) — split/unified rendering with syntax-aware
  styling, in the comparison view and single-agent tool results.
```
