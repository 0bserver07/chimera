# Spike report: native-scrollback hybrid transcript renderer

**Status:** spike complete — **GO (conditional)** for the one-lane multiplexer
as an opt-in inline mode (`tui.inline`), gated on multiplexer detection.
**Date:** 2026-07-10 · **Spec hook:** `docs/specs/tui-ux-refinements.md`
§8 R-VIEW-5 (exploratory, "spike first") · **Prototype:**
`scripts/spikes/scrollback_hybrid.py` · **Tests:**
`tests/spikes/test_scrollback_hybrid.py` (39, TTY-free, byte-exact).

Sources are the reference corpus only (R1's scroll-region hybrid is the
template; R6's fully-inline diffed line-array is the alternative it was chosen
over). No reference names appear here per house rules.

## 1. What was spiked

The shipped TUIs render transcripts inside a full-screen widget app: the
terminal's own selection, copy, and wheel-scroll are dead, and the transcript
dies with the app. The hybrid inverts this for the single-agent surface:

- **Committed transcript lines** are written *once* into the terminal's
  normal buffer and scrolled up into **native scrollback** — mouse selection,
  copy, wheel-scroll, and after-exit persistence all work because the
  terminal owns those rows.
- **A reserved bottom band** (separator / composer / status — 3 rows in the
  prototype) repaints in place with plain cursor addressing and never
  scrolls.
- **No alternate screen, no mouse capture** — the two choices that make
  native selection possible are the absence of two escape sequences.

The prototype plays a scripted agent stream (markdown prose, a fenced code
block, tool-result lines, CJK text, a long URL) through a newline-gated block
splitter (it soft-imports `chimera.tui.markdown_stream.split_complete_blocks`
— the Phase-1 R-REN-6 helper — with a blank-line fallback), while the band
ticks a spinner/elapsed/count status and echoes real keystrokes into a fake
composer. `--rows N` sets the committed-line target; `--crash-at N` proves
crash restoration; `--debug-log` records band-position transitions for
headless verification.

## 2. The mechanism (exact sequences and why)

All 1-based rows; `CSI` = `ESC [`. Emission is factored into pure functions
(file:line refer to `scripts/spikes/scrollback_hybrid.py`).

| Sequence | Name | Used for |
|---|---|---|
| `CSI {top};{bottom} r` | DECSTBM | Confine scrolling to a region. Set *transiently* around each operation, never left active. Homes the cursor as a side effect — hence DECSC/DECRC around it. Regions are ≥ 2 rows (a 1-row history region cannot scroll; `Geometry.fit` keeps ≥ 2 history rows). |
| `CSI r` | DECSTBM reset | Release the region (also homes the cursor). |
| `ESC 7` / `ESC 8` | DECSC/DECRC | Save/restore cursor + SGR around region ops, so a commit batch is cursor-neutral. |
| `ESC M` | Reverse index | At the *top* of a region, scrolls the region *down* — used to glide a mid-screen band toward the bottom, freeing rows above it. |
| `\r\n` at region bottom | — | The engine: each linefeed on the region's bottom margin scrolls the region up one row; the row evicted at screen top enters the terminal's scrollback. |
| `CSI ?2026 h/l` | Synchronized output | Wraps every commit batch and band repaint so frames composite atomically (no tearing). Ignored where unsupported. |
| `CSI K` / `CSI 0J` | EL / ED | Clear-line before each committed line (defensive); erase the band on clean exit. |
| `CSI ?25 l/h` | Cursor hide/show | Hidden during band repaints; parked at the composer's insertion point after. |
| `CSI 6n` → `CSI {r};{c} R` | DSR/CPR | Ask where the shell left the cursor at startup, so the band starts *there* (never touching prior shell output) and glides down as content commits. 250 ms timeout → assume bottom. |

The three verbs:

1. **Commit** (`commit_lines`, :215): sync-begin · DECSC ·
   `region_setup(1, band_top-1)` · CUP to the last committed row · per line
   `\r\n` + EL + line + SGR reset · region reset · DECRC · sync-end.
   Lines are **pre-wrapped to the width** (rich does word-wrap and CJK cell
   arithmetic, `render_ansi_lines` :416) so each line costs exactly one row —
   an over-wide line would auto-wrap and scroll more rows than counted.
2. **Make room** (`make_room`, :255): when the band is not yet glued to the
   bottom — region `band_top..rows`, cursor at region *top*, one `ESC M` per
   freed row (clamped to space below), band_top += n. R1's exact maneuver.
3. **Band repaint** (`band_paint`, :315): CUP + EL + content per band row,
   inside sync markers, cursor hidden then parked. Never scrolls, never
   touches the history region.

Lifecycle: enter = CPR + `initial_band_position` (:292 — if the cursor sits
too low, whole-screen linefeeds first, which push *shell* history into
scrollback, never erase it). Clean exit = `exit_seq` (:365): region reset,
erase the band (chrome is not transcript), SGR reset, show cursor — the shell
prompt resumes directly under the last committed line. Crash =
`emergency_restore_seq` (:382) via **sys.excepthook (restores *before* the
traceback prints), SIGTERM handler, and atexit backstop**; deliberately
non-destructive (no erase, no cursor moves) so the traceback stays readable.
Resize = SIGWINCH flag → next tick re-probes size, re-glues the band to the
new bottom (`resize_reglue`, :342), repaints.

## 3. What was proven (empirical runs)

Headless, but against real VT implementations. `script`(1) provided a bare
pty for byte-stream verification; **tmux 3.5a, GNU screen 4.00, and Zellij
0.43.1 served as terminal-emulator oracles** (they implement the VT state
machine and expose their history for inspection).

| Run | Result |
|---|---|
| Bare pty (`script -q file /bin/sh -c "stty rows 24 cols 90; …"`) | 11 commit batches `CSI 1;21 r`, region sets ≤ resets, 24 balanced sync pairs, exact exit epilogue, content order monotone. CPR unanswered → bottom fallback, as designed. |
| **tmux, 80-line stream on a 24-row screen** | **94 lines in `capture-pane -S -` — rows evicted from the partial region ARE retained in tmux history.** Pre-existing shell output preserved above the transcript; probe order fully monotone; **zero band chrome leaked into history**; prompt usable after exit. |
| tmux, CPR + glide | Debug log: `start cpr_row=8` → make-room events walk band_top 8→9→12→13→17→…→22, then none — the reverse-index glide works and terminates exactly when glued. |
| tmux, resize mid-run (90×24 → 70×18 via `resize-window`) | SIGWINCH applied on next tick: band re-glued at rows 16–18, stream continued to 154 lines at the new width, clean exit. Pre-resize history rows re-wrapped by tmux itself (terminal-owned; see §5). |
| tmux, crash (`--crash-at 20`, deliberately *not* wrapped in try/finally) | excepthook restored the terminal **before** the traceback printed; `EXIT-CODE=1`; a subsequent `seq 1 30` scrolled the full screen normally (region truly reset) with the cursor at the true bottom row; transcript retained. |
| tmux, interactivity | Typed text echoed live into the composer (`❯ hello band`) while commits streamed above; cursor parked at the exact insertion column; `q` quit cleanly. |
| GNU screen 4.00 (2006) | Works: CPR glide 4→22, 61 lines committed, transcript in `hardcopy -h` history, order monotone, clean exit. (Hardcopy shows `·` as `�` — screen's own dump encoding, cosmetic.) |
| **Zellij 0.43.1** | **Live rendering correct, but rows evicted from a partial scroll region NEVER enter Zellij's scrollback** — `dump-screen --full` mid-run held only the viewport; committed lines beyond one screenful are *lost*. Control: plain `seq 1 100` in the same pane was fully retained, so Zellij scrollback itself works. Pre-wrapping does **not** avoid it. This reproduces exactly why R1 ships a dedicated Zellij fallback (write raw through the terminal + reserve blank viewport rows instead of region-scrolling). |
| Non-TTY pipe | Graceful: 24×80 fallback, full emission, clean exit — CI-safe. |
| Degenerate ptys | A pty with no emulator behind it reports a **0×0 winsize**; trusting it produced a 1-row layout. Fixed: `< 3` rows / `< 10` cols → fall back to 24×80. |

## 4. Coexistence with the widget framework

Three options were analyzed; the hybrid decides against driving the band with
the framework at all:

1. **Full-screen app + hybrid transcript — impossible.** The framework's
   driver enters the alternate screen and captures the mouse; both are the
   exact opposites of this design. Nothing to salvage.
2. **Framework inline mode driving the band — not viable today.** Inline
   mode does keep the normal buffer, but the compositor owns its bottom
   region and exposes **no API for writing above it**; injecting
   scroll-region escapes underneath a live compositor that tracks its region
   position independently is undefined behavior we'd have to maintain
   against driver internals. The §10 note in `tui-ux-refinements.md`
   ("R-VIEW-5 maps to inline mode — spike its interaction") is hereby
   answered: **model R-VIEW-5 on the R1 hybrid, not on framework inline
   mode.**
3. **Hand-rolled band + rich renderables — what the spike proves.** The band
   is 3–10 rows repainted by ~40 lines of plumbing; everything visual is
   already a rich renderable in `chimera/tui/render.py` (which is
   framework-free by design). The transcript path reuses
   `markdown_stream.split_complete_blocks` unchanged — the spike already
   soft-imports it. **The hybrid needs no widget framework anywhere.**

The N-lane multiplexer keeps the full-screen app (tiled panes genuinely want
a compositor); the hybrid is the *single-agent* surface, exactly as R-VIEW-5
scopes it. Overlays (pickers, pager): prefer swapping content *into the band*
(R-OVER-1's in-flow preference); a full-screen pager may enter the alternate
screen *temporarily* and restore — R1 does precisely this.

## 5. What breaks / known limits

- **Zellij: committed lines beyond one screenful are lost** (proven, §3).
  Integration must detect (`ZELLIJ` env var) and fall back — options:
  R1-style raw append mode, or refuse `tui.inline` with a message. Same
  caution applies to any unprobed multiplexer.
- **Resize cannot reflow committed rows.** They are terminal-owned; emulators
  re-wrap (tmux) or truncate them per their own rules. R1 re-derives wrapped
  history from source on resize; that is an [L] follow-up, not v1. The band
  itself re-glues cleanly (proven).
- **Editing history is impossible by construction.** Committed = immutable.
  Progressive commitment must therefore be conservative — which is exactly
  the contract `split_complete_blocks` already implements (R-REN-6), and the
  ephemera-never-persist rule (R-VIEW-3) must hold absolutely, since a
  spinner that scrolls into scrollback is there forever. The spike's band
  chrome provably never leaked (§3).
- **Scroll-while-streaming:** wheel-scrolling into scrollback while commits
  continue is native terminal behavior (view may jump on output, emulator
  dependent). No mitigation planned; it is the same UX as `tail -f`.
- **Windows:** ConPTY translates DECSTBM, but scrollback semantics for
  partial-region evictions were not testable here — gate v1 to POSIX.
- **Degenerate terminals** (< 5 rows): the band shrinks, then commits skip
  the screen (still counted); tiny-pty winsize lies handled (§3).

Not tested headlessly (honest unknowns): mainstream GUI emulators
(Terminal.app, iTerm2, kitty, WezTerm, Alacritty, VTE) — tmux + GNU screen
passing is strong evidence for the classic top-margin-1 eviction behavior,
and the reference agent ships this mechanism to those emulators in the field,
but our own matrix run on real GUI emulators should precede default-on;
`CSI ?2026` visual atomicity (correctness does not depend on it); IME
composition in the composer; bracketed paste (not implemented in the spike).

## 6. GO/NO-GO and integration sketch

**GO — conditional.** The mechanism is proven end-to-end on real VT
implementations with exact-byte unit coverage; the one hard failure (Zellij)
is detectable and has a known fallback shape. Conditions: opt-in flag first
(`tui.inline: false` default, already reserved in the spec's §11 config
table); Zellij detection + fallback/refusal; POSIX-only v1; a manual smoke on
the two GUI emulators we actually use before flipping any default.

Sketch (target: the one-lane mux, `chimera/tui/multiplex.py:983`
`run_single_agent`):

1. `chimera/tui/scrollback.py` — lift the spike's pure builders + a
   `HybridScreen` hardened for production (bracketed paste, Ctrl+C two-stage
   per R-IN-4). ~400 lines, tests are largely written (port the 39).
2. `chimera/tui/inline_frontend.py` — an AgentDriver loop: driver events →
   `render.py` renderables → `split_complete_blocks` gating → commit; band =
   separator + composer (reuse `prompt.py`'s pure completion logic, not its
   widgets) + status line strings shared with the mux. ~250–350 lines.
3. `run_single_agent(inline=True)` / `tui.inline` config: choose the
   frontend before the app constructs; everything else (cohort persistence,
   history codec) is frontend-agnostic already.
4. Fallback mode for detected multiplexer breakage: plain append-only
   printing (no band, status inline) — ~50 lines, and it doubles as the
   `--no-tty` CI mode.

Effort: **M** (2–3 days incl. tests), consistent with the dossier's estimate
for the corpus template. Follow-ups filed as [L]: resize re-derive from
source; GUI-emulator matrix; Windows.
