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
- **Tool calls** render per class, not through one generic printer — a glyph
  for the class, the tool's own name, and a distilled one-line summary:

  | Row | Class |
  |---|---|
  | `$ bash pytest -q` | shell (bash, test, git, verify) — output framed as a card |
  | `→ read src/app.py:12-40` | read |
  | `← edit src/app.py` | write / edit |
  | `✱ search "TODO" in chimera/` | search / list |
  | `↳ delegate explore · map the repo` | sub-agents — output framed as a card |
  | `⚙ your_tool(a=1, b=2)` | anything unregistered, exactly as before |

  Shell, delegate, and web output is framed with a `│` gutter so a wall of
  command output reads as a payload rather than as prose.
- **Tool output** is elided for display (head + tail with a dim
  `… +N lines …` marker naming the expand key). The session record always
  keeps the full output *and the generic `⚙ name(args)` form*, so persisted
  transcripts are unchanged; press the expand toggle (default **Ctrl+X**) to
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
| Ctrl+F | full transcript overlay (untruncated) | |
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

## Pasting a wall of text

Paste something big — a stack trace, a file, a log — and the composer collapses
it to a chip instead of burying itself:

```
› here's the failure: [Pasted #1 ~420 lines] what broke?
```

The chip is an **atomic edit unit**. Left/right and word-nav hop it whole,
Backspace/Delete remove it whole, and there is no way to land the cursor inside
it. On submit, the chip expands: the agent receives the full text, exactly as
pasted. Up-arrow history recalls the **chip**, not the wall of text — so a
recalled prompt stays readable and still submits the full paste.

Thresholds are configurable (`tui.paste_chip_lines`, `tui.paste_chip_chars` —
see [Configuration](#configuration)); pastes under them are inserted verbatim,
exactly as before.

## The transcript overlay — everything, untruncated

Long tool output is elided in the panes (`… +37 lines … (Ctrl+X expands ·
Ctrl+F full transcript)`). That truncation is **display-only**: the session
record always kept the whole thing. **Ctrl+F** opens the full transcript of the
focused lane as a full-screen pager:

| Key | In the overlay |
|---|---|
| ↑ ↓ PgUp PgDn Home End | scroll |
| `/` | focus the search filter (type to narrow to matching lines) |
| `p` | toggle **plain mode** — no gutter, no color, no padding |
| Esc / `q` | leave the filter, then leave the overlay |

Rich mode prefixes each row with its transcript line number, so a filtered hit
keeps its place in the record. Plain mode is the copy surface: select and paste
and you get exactly the transcript text, with nothing of the frame in it.

Every key here is registry-owned, so `tui.keybinds` rebinds them like any
other — and the elision markers name whatever key is *currently* bound, never a
stale default:

```toml
[tui.keybinds]
show_transcript = "f9"        # the overlay key the markers advertise
plain_mode = "ctrl+p"         # inside the overlay
search = "s"
```

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

**Themes** — `tui.theme` / `tui.theme_mode` / `tui.animations`. Semantic slot
themes with dark/light variants, auto mode detection, and user theme files;
see [Themes](#themes) below:

```toml
[tui]
theme = "chimera"      # default | chimera | mono | <your theme file's stem>
theme_mode = "auto"    # auto | dark | light | lock
animations = true      # false → static spinners (NO_COLOR forces this off)
```

**Paste chips** — `tui.paste_chip_lines` / `tui.paste_chip_chars`. Both caps
bind, whichever hits first; `0` on a cap disables it, and `0` on both means
pastes always land verbatim:

```toml
[tui]
paste_chip_lines = 8      # collapse a paste over 8 lines
paste_chip_chars = 1000   # …or over 1000 characters (one huge line counts)
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

## Themes

A theme maps **semantic slots** — not widget colors. Roughly sixty named
slots in seven families (`base`, `status`, `chrome`, `tool`, `markdown`,
`syntax`, `diff`) plus three opacity knobs; swap a theme and every surface
follows, because nothing addresses a widget.

Three built-ins:

| Theme | What it is |
|---|---|
| `default` | **the default** — terminal palette (your 16 ANSI colors), byte-identical to pre-theme output |
| `chimera` | the house theme: truecolor with full dark/light variants |
| `mono` | no color at all — structure carried by bold/dim/reverse |

```toml
[tui]
theme = "chimera"       # built-in name, or the stem of a theme file
theme_mode = "auto"     # auto | dark | light | lock
animations = true       # false → static spinners and a frozen heartbeat pulse
```

**`/theme`** opens a fuzzy picker that **previews live** as you move the
highlight — Enter keeps the theme, **Esc restores** what you had. `/theme list`
prints the catalog with the active mode and color depth; `/theme <name>`
switches straight away. A `/theme` switch is session-scoped; the message tells
you the config key that makes it permanent.

**Dark/light detection** (`theme_mode = "auto"`, the default) cascades:
`$CHIMERA_THEME_MODE` → `$CHIMERA_TERM_BG` (a `#rrggbb` terminal background,
judged by luminance) → `$COLORFGBG` → dark. `lock` detects once and ignores
later terminal notifications; `dark`/`light` pin it outright.

**Degradation** is automatic (nothing to configure): truecolor is detected
from `$COLORTERM`, 256 colors from `$TERM`, else 16. Hex slot values quantize
to the nearest palette entry; ANSI color *names* pass through untouched at
every depth, which is why the default theme looks native in any terminal.
**`NO_COLOR`** drops color entirely — attributes (bold, dim, reverse) survive,
so the interface still reads as designed — and implies `animations = false`.

**Your own themes** live as files under any config scope's `themes/`
directory — `~/.config/chimera/themes/`, `~/.chimera/themes/`, or
`<project>/.chimera/themes/` — in TOML (canonical), JSON, or YAML. The file
stem is the theme name, and later scopes win. A theme may declare a `vars`
palette that slots reference by `$name`, so a palette swap never touches the
slots:

```toml
# ~/.chimera/themes/midnight.toml
description = "cool dark"

[vars]
ink   = { dark = "#c8d3f5", light = "#2a2f45" }
leaf  = "#7fd88f"

[slots]
base.text  = "$ink"
diff.add   = "$leaf"
tool.name  = "bold $leaf"
"markdown.code" = "cyan"        # ANSI names are fine anywhere

[opacity]
reasoning = 0.6                 # <0.85 renders as the terminal's dim attribute
```

Any value may be a `{ dark = …, light = … }` pair; a missing variant falls
back to the other one. Unknown slot names, circular `$var` chains, and
malformed files are rejected — the TUI starts on the default theme and says
why, rather than failing to launch.

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

Diffs are **word-level**: when a removal run and the addition run after it
have the same length, the lines pair up and only the changed *tokens* are
inverse-highlighted, in both the unified and the split view. Three rules keep
it honest — shared indentation is never highlighted (a re-indent doesn't light
up the line), an unbalanced run (3 removed, 1 added) has no honest pairing and
falls back to plain line colors, and a pair too dissimilar to be an edit of
each other is left plain rather than highlighted end to end.

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
