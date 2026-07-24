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
| Ctrl+R | comparison screen (scoreboard + diffs) | |
| Ctrl+L | clear conversation | single-lane |
| Tab / Shift+Tab | complete `/command`, else cycle lanes | |
| Ctrl+B / Ctrl+G / Ctrl+O | broadcast⇄target · cancel lane · clear lane | multi-lane |
| Ctrl+T | tool-call sidebar | |
| Ctrl+J | newline in the composer | |

Mid-turn, typing **steers** the running lane; at idle it starts a new turn.

## Configuration

Two knobs ship today, read from different files (unification is tracked
work — documented as-is):

**Keybindings** — `tui.keybinds` in `~/.chimera/config.toml` (the shared CLI
defaults file; `$CHIMERA_CONFIG_HOME` overrides the directory). Value = key
string, list of keys, or `false` to unbind. Unknown actions, conflicting
keys, and attempts to unbind reserved actions (`cancel_all`, `quit`) are
rejected loudly — the TUI starts on defaults and tells you why:

```toml
[tui.keybinds]
toggle_expand = "ctrl+u"          # rebind
show_results  = ["ctrl+r", "f2"]  # multiple keys
toggle_sidebar = false            # unbind
```

**Status line & title** — `tui.status_line` / `tui.title` in a
`config.{yaml,yml,json}` under any of `~/.config/chimera/`, `~/.chimera/`,
`<project>/.chimera/` (later scopes win, key-by-key):

```yaml
tui:
  status_line: [model, git, context-used, tokens, cost, run-state]
  title: [activity, project]     # terminal title; "off" disables
```

Items hide themselves when their data source is unavailable, and the line
degrades segment-by-segment on narrow terminals instead of wrapping.

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

- `docs/guides/external-lanes.md` — race real third-party agent CLIs as lanes.
- `docs/building-a-tui.md` — drive the same event stream from your own frontend.
- `docs/guides/use-modal-endpoints.md` — put a Modal-served model in a lane.
- `docs/specs/tui-ux-refinements.md` — the design spec these features implement.
