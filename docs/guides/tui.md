---
title: "The Chimera TUI"
description: "Daily driver and multiplexer in one app: streaming with a live tail and reasoning heartbeat, follow-mode scrolling, rebindable keys, a composable status line, opt-in approval modals, and lane comparison."
---

# The Chimera TUI — daily driver and multiplexer

One app, two shapes. Bare `chimera code --tui` is the daily driver: a
**one-lane multiplexer** editing your real tree in place. Add `--models` and
the same app races N isolated lanes on one task:

```bash
set -a; source .env; set +a          # model credentials
chimera code --tui                                    # daily driver (1 lane, inplace)
chimera code --tui --models glm-5.2,glm-5.1           # race 2 lanes (worktree isolation)
chimera code --tui --models "modal-endpoint/zai-org/GLM-5.2-FP8,glm-5.2[1m]"
```

Sessions persist as cohorts under `~/.chimera/cohorts/` — resume with
`--resume <id>`, `/resume [id]`, or the `/cohorts` picker (type to filter by
id, lane, or task; Enter resumes, Esc backs out).

## Reading the screen

- **Transcript**: assistant prose renders as markdown, committed
  block-by-block *while it streams* — completed paragraphs/fences/tables
  appear immediately; nothing half-parsed is ever shown.
- **Live region** (below the transcript): the not-yet-complete tail of the
  current block, capped at ~6 lines; while the model reasons it shows the
  heartbeat — `∴ Thinking ··· 5s · ~1.2k chars · 240 chars/s` (sizes are
  honest chars; providers don't report token counts mid-stream). Everything
  here is display-only chrome: it never enters the session record.
- **Scrolling follows you**: the view pins to the tail while you're at it;
  scroll up (wheel/keys/scrollbar/drag-select) and it freezes where you're
  reading; return to the bottom — or submit input — and it re-pins.
- **Tool output** is elided for display (head + tail with a dim
  `… +N lines …` marker naming the expand key). The session record always
  keeps the full output; press the expand toggle (default **Ctrl+X**) to
  flip elision globally for subsequently rendered results.
- **Reasoning** is collapsed to a one-line trace
  (`∴ thought for 5s (~1.2k chars)`); **Ctrl+E** reveals it.
- **Status line**: e.g. `glm-5.2[1m] · 0% · 3.4k/1M (auto) · $0.0074 · done` —
  model, context used (real provider-reported tokens against the window;
  colored at 70%/90%, `(auto)` = auto-compaction armed), cost, run state.
  `/statusline` lists every available item and the current order.

## Commands and keys

`/help` is generated from the command registry and always shows the
*currently bound* keys. `/keys` prints the effective binding table with each
binding's source (default / user / migrated). Defaults:

| Key | Action | Scope |
|---|---|---|
| Ctrl+C | cancel running turn (at idle: quit) | reserved |
| Ctrl+D | quit | reserved |
| Ctrl+E | toggle reasoning | |
| Ctrl+X | expand/collapse tool output | |
| Ctrl+Y | copy selection to clipboard (OSC 52) | |
| Ctrl+R | comparison screen (scoreboard + diffs) | |
| Ctrl+L | clear conversation | single-lane |
| Tab / Shift+Tab | complete `/command`, else cycle lanes | |
| Ctrl+B / Ctrl+G / Ctrl+O | broadcast⇄target · cancel lane · clear lane | multi-lane |
| Ctrl+T | tool-call sidebar | |
| Ctrl+J | newline in the composer | |

Mid-turn, typing **steers** the running lane; at idle it starts a new turn.

## Selecting and copying text

Drag to select transcript text, then **Ctrl+Y** copies it to the system
clipboard over OSC 52 — so it works even over SSH. Ctrl+C stays the cancel key.

By default the TUI captures the mouse (for Textual's own selection and pane
interactions). If you'd rather use your **terminal's native** click-drag
selection, copy, and scrollback — the way Claude Code feels — launch with
**`--no-mouse`**:

```bash
chimera code --tui --no-mouse
```

That hands the mouse back to the terminal (native select / copy / scroll all
work) at the cost of Textual's in-app mouse features. Ctrl+Y still copies either
way. OSC-52 copy needs a terminal that permits it (iTerm2, kitty, WezTerm, or
tmux with `set -g set-clipboard on`); macOS Terminal.app does not.

## Configuration

All TUI settings live under `tui.*` in **one config chain**, read through a
single loader (`chimera/config/user_config.py`). The canonical file is
`~/.chimera/config.toml` (`$CHIMERA_CONFIG_HOME` overrides the directory);
older `config.{yaml,yml,json}` files in the same scopes still load as a
compatibility shim. Precedence, lowest to highest: `~/.config/chimera/` <
`~/.chimera/` < `<project>/.chimera/`, deep-merged key-by-key (TOML wins a
collision within a scope). A missing or broken config never blocks startup.
The full map is in [Persistence & config model](../notes/persistence-model).

**Keybindings** — `tui.keybinds`. Value = key string, list of keys, or
`false` to unbind. Unknown actions, conflicting keys, and attempts to unbind
reserved actions (`cancel_all`, `quit`) are rejected loudly — the TUI starts
on defaults and tells you why:

```toml
[tui.keybinds]
toggle_expand = "ctrl+u"          # rebind
show_results  = ["ctrl+r", "f2"]  # multiple keys
toggle_sidebar = false            # unbind
```

**Status line & title** — `tui.status_line` / `tui.title`. Items hide
themselves when their data source is unavailable, and the line degrades
segment-by-segment on narrow terminals instead of wrapping:

```toml
[tui]
status_line = ["model", "git", "context-used", "tokens", "cost", "run-state"]
title = ["activity", "project"]   # terminal title; "off" disables
```

**Cohort retention** — `tui.cohorts`. Bare `--tui` sessions persist one
cohort each under `~/.chimera/cohorts/`; a retention policy caps them. **OFF
by default** (nothing is ever pruned without a policy); the cohort being run
or resumed is never deleted:

```toml
[tui.cohorts]
retain = 20            # keep only the newest 20 cohorts
max-age-days = 30      # and/or drop cohorts older than 30 days
```

**Budgets** — `tui.budget`. Default caps applied to every lane, plus a
cohort-aggregate cap under `[tui.budget.cohort]`. **OFF by default**; see
[Budgets](#budgets) below for the full story:

```toml
[tui.budget]                 # per-lane defaults
max-cost = 0.10              # dollars
max-steps = 20               # reason-act cycles (LLM turns)
max-wall-clock = 300         # seconds of active running time

[tui.budget.cohort]          # aggregate across all lanes
max-cost = 1.00
max-wall-clock = 900
```

## Permission approvals (opt-in)

By default the TUI runs tools without prompting (unchanged behavior). Turn
on approval modals with `CHIMERA_TUI_APPROVALS=1` (or
`run_multiplexer(..., approvals=True)` from Python):

```bash
CHIMERA_TUI_APPROVALS=1 chimera code --tui
```

Gated tool calls (writes, bash, git — low-risk reads auto-allow) pop a modal
naming the lane, tool, and arguments: **Allow**, **Allow for session**
(remembered per lane, in memory only), or **Deny** with an optional one-line
reason that is returned to the agent as the denial message. Multiple pending
requests queue one modal at a time. Caveats: presets built with
`permissions=False` (minimal / explore / swebench) have no checker, so the
opt-in is a no-op there; strategy-loop lanes (`:plan-execute` etc.) bypass
permission checks entirely (pre-existing).

## Budgets

Bound a race: a **lane** — and the **cohort** as a whole — can carry a budget
that stops it cleanly with an honest terminal reason instead of letting a
runaway lane burn tokens unbounded. Three dimensions, all optional:

- **cost** — dollars spent (priced from provider usage).
- **steps** — reason-act cycles (LLM turns; the `N st` in a pane header).
- **wall-clock** — seconds of *active* running time (idle time between turns
  does not count; a cohort's wall-clock is the race's real elapsed time).

A lane that trips its budget ends the turn with reason
`budget_exhausted:cost` / `:steps` / `:wall_clock` — shown in the pane header,
the status-line budget meter, the scoreboard, and the persisted manifest. The
unit that tips the budget is allowed to finish; the next one never starts.
When the **cohort** cap trips, every still-running lane is cancelled
cooperatively (the same SIGTERM-style cancel as `Ctrl+C`, never a kill) and
reports `cohort_budget:<dim>`. Lanes that already finished under budget keep
their own outcome. Budgets are **additive** — with none set, behavior is
byte-identical to before.

**Compact grammar.** A budget is a `/`-joined list of clauses, each a number
with a unit: `$0.10` or `0.10usd` (cost), `20steps`/`20st` (steps),
`300s`/`300sec` (wall-clock seconds), `40tc` (tool calls). A bare number is
cost USD. Example: `$0.10/20steps/300s`.

**Setting a budget** (highest precedence first):

```bash
# CLI: one flag for every lane, plus a cohort-aggregate cap
chimera code --tui --models glm-5.2,glm-5.1 \
    --lane-budget '$0.10/20steps' --budget '$1.00/900s'

# Per-lane override in the model spec — a 4th ':' field (empty preset/loop OK)
chimera code --tui --models 'glm-5.2:coding_agent:plan:$0.20,glm-5.1:::$0.05'
```

Config defaults live under `[tui.budget]` / `[tui.budget.cohort]` (see
[Configuration](#configuration)). Embedders pass `lane_budget=` /
`cohort_budget=` (a `BudgetSpec` or a compact string) to `run_multiplexer` /
`run_single_agent`. A resumed cohort restores the budgets recorded in its
manifest.

**`/budget`** inspects live consumption for every lane and the cohort. With an
argument it sets the budget — the focused lane in single-lane mode, the cohort
in multi-lane mode; `/budget off` clears it. The status line carries a
`budget` meter (`$0.04/$0.10 · 3/20 steps …`) that colors yellow then red as
it approaches a cap, and hides entirely when no budget is set.

## Comparing lanes

`Ctrl+R` (or `/results`) opens the ranked scoreboard over a per-lane diff
viewer — `n`/`p` cycles files, `s` toggles side-by-side, and each rebuilt
view lands at the top. `/export` zips the cohort artifact.

## Racing a real external agent

A lane can wrap a *real third-party coding-agent CLI* instead of a Chimera
agent — `--models ext:claude,glm-5.2` races the `claude` CLI against a
Chimera lane on the same task, each in its own worktree. See
`docs/guides/external-lanes.md` for profiles, protocols, and the honest limits
(telemetry varies, no steering).

## See also

- `docs/guides/inline-mode.md` — opt-in single-agent **inline mode**: render the
  transcript into the terminal's native scrollback (mouse selection, copy,
  wheel-scroll, after-exit persistence) with the composer/status band pinned
  below. POSIX-only, off by default.
- `docs/guides/external-lanes.md` — race real third-party agent CLIs as lanes.
- `docs/building-a-tui.md` — drive the same event stream from your own frontend.
- `docs/guides/use-modal-endpoints.md` — put a Modal-served model in a lane.
- `docs/specs/tui-ux-refinements.md` — the design spec these features implement.
