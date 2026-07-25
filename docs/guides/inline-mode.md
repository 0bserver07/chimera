---
title: "Inline mode (native terminal scrollback)"
description: "An opt-in single-agent frontend that renders the transcript into the terminal's own scrollback with the composer/status band pinned below — mouse selection, copy, wheel-scroll, and after-exit persistence all work. POSIX-only, off by default, with a multiplexer fallback."
---

# Inline mode — the transcript lives in your terminal's scrollback

The full-screen TUI owns the whole screen (the alternate screen buffer) and
captures the mouse. That is what makes tiled multiplexer panes possible, but it
also means the terminal's *own* selection, copy, and wheel-scroll are dead
while the app runs, and the transcript vanishes when it exits.

**Inline mode** inverts this for the single-agent daily driver. Finished
transcript lines are written once into the terminal's **normal** buffer and
scrolled up into its **native scrollback**; only a small band at the bottom
(a separator, the composer, and a status line) repaints in place. There is no
alternate screen and no mouse capture, so:

- **select and copy** transcript text with the mouse, as in any shell output;
- **wheel-scroll** back through history using the terminal's own scrollback;
- the **transcript persists after exit** — the shell prompt returns directly
  under the last line, with everything above it still in scrollback.

It is **off by default** and **opt-in** — a deliberate tradeoff against the
full-screen widgets, not a new default (spec `tui-ux-refinements.md` R-VIEW-5).
The multiplexer (`--models a,b,…`) always stays full-screen; inline is only for
the one-lane daily driver.

## Turning it on

Two equivalent switches; the CLI flag wins over config:

```bash
# CLI flag — single-agent --tui only
chimera code --tui --inline
```

```toml
# ~/.chimera/config.toml  (or any scope in the config chain)
[tui]
inline = true            # default: false
```

With `--models` (the multiplexer) the flag is ignored — tiled panes need the
compositor. Inline applies to bare `chimera code --tui` (one lane, editing your
real tree in place), and the session still persists as a one-lane cohort under
`~/.chimera/cohorts/`, exactly like a full-screen run.

## Reading the screen

Everything above the band is real terminal scrollback:

- **Transcript** (scrollback): assistant prose renders as markdown, committed
  block-by-block *as it streams* — a completed paragraph, fence, or table is
  pushed up the moment it finishes (nothing half-parsed is ever committed,
  since committed rows are immutable). Tool calls and results, the user echo
  (`› …`), and the dim per-turn result line all live here.
- **Band** (pinned, repaints in place):
  - a dim separator, `╌ native scrollback ↑ ───…`, marking the live edge;
  - the composer, `❯ your text`, with the cursor parked at the insertion point;
  - a status line — `● ready · <model> · $<cost> · <steps> steps · <n> lines`
    when idle, and `⠹ working · <elapsed> · $<cost> · <steps> steps · ∴ thinking
    <size>` while a turn streams.

Keys: type and press **Enter** to send; **Backspace** / **Ctrl+U** edit the
line; **Ctrl+C** cancels a running turn (press it again — or when idle — to
quit); **Ctrl+D** on an empty line quits. Slash commands: `/help`, `/clear`,
`/cost`, `/model`, `/tools`, `/exit`.

## The fallback — scrollback is never silently lost

Inline mode is *requested* by the flag/config, then *gated* by a capability
check before it runs. When any condition fails, Chimera falls back to the
full-screen frontend and prints a one-line note (`inline mode unavailable
(<reason>); using the full-screen TUI.`) — it never silently drops into a mode
that would lose your transcript:

| Condition | Reason token | Behavior |
|---|---|---|
| Not requested | `disabled` | Full-screen (silent — this is the default) |
| Windows / non-POSIX | `non-posix` | Full-screen + note |
| stdin or stdout is not a TTY (piped) | `not-a-tty` | Full-screen + note |
| A terminal multiplexer that drops partial-scroll-region evictions from its scrollback (detected by its `$ZELLIJ` session variable) | `multiplexer:ZELLIJ` | Full-screen + note |

The multiplexer case is the one hard, empirically proven failure: that
multiplexer retains a plain full-screen scroll in its history, but rows evicted
from a *partial* scroll region — exactly what the hybrid uses to keep the band
pinned — are discarded, so committed lines beyond one screenful would be lost.
Chimera detects its session environment variable and refuses rather than lose
transcript. (Other classic multiplexers retain partial-region evictions and
work.)

## Shell integration — jump turn to turn (opt-in)

Because the transcript lives in the normal buffer, your terminal can treat each
turn the way it treats a shell command — *if* something marks the zones. Turn
that on and Chimera emits the standard OSC 133 marks around every committed
turn:

```toml
[tui]
shell_integration = true      # default: false
```

What you get in a terminal that implements the protocol: jump to the previous /
next prompt, select "the output of that turn", and fold long output — all with
the terminal's own keybindings, on the rows it already owns.

The marks are four zero-width escape sequences: prompt-start and input-start
ride the echoed `› your prompt` row, output-start rides the first row the turn
produces, and command-end (with an exit status) is queued for the next
committed row — the same `D` then `A` pairing a shell emits at its next prompt.

Safety, since this writes raw escapes into the same stream as the transcript:

- A terminal that does not implement OSC 133 **consumes the sequence and draws
  nothing** — there is no fallback rendering to go wrong.
- The marks are zero-width, so the hybrid's "every committed line fits the
  screen" accounting is untouched; a mark can never push a row into a wrap.
- They ride as the *prefix of a committed row*, inside the same scroll-region
  batch, so a mark attaches to the transcript row it describes and never to the
  pinned band (where the cursor actually rests).
- Off by default, and off means **byte-identical** output — the frontend writes
  exactly what it wrote before the feature existed.
- Full-screen mode emits nothing: it runs in the alternate screen, where there
  is no scrollback to navigate.

## Scope and known limitations (v1)

Honest boundaries, documented rather than surprising:

- **POSIX only.** Windows falls back to full-screen (partial-region scrollback
  semantics under ConPTY are untested).
- **The live streaming tail is summarized, not previewed.** A paragraph appears
  in scrollback when it *completes*; while it streams, the status line shows its
  growing size (`writing ~1.2k chars`) rather than a live preview above the
  band. This keeps the band a fixed height so the layout never jumps.
- **The composer is inert mid-turn.** Type after the turn ends; Ctrl+C cancels.
  (No mid-turn steering or follow-up queue in inline v1.)
- **Resize does not reflow already-committed rows.** They are owned by the
  terminal; your emulator re-wraps or truncates them per its own rules. The
  band itself re-glues cleanly to the new bottom.
- **No in-TUI cohort resume.** `/cohorts` and `/resume` are full-screen
  affordances; the session still persists and is resumable via `--resume`.

## Manual verification checklist (before any default flip)

Inline mode is validated headlessly for its byte sequences, capability gate,
and state restoration, but the *visual* behavior on real GUI terminals can only
be signed off by a human. **The default stays OFF until this checklist passes**
on the emulators you actually use. Run it in each of Terminal.app, iTerm2 (and
any daily driver — kitty / WezTerm / Alacritty / GNOME Terminal), plus tmux:

1. **Launch mid-screen.** From a shell with a few lines of prior output already
   on screen, run `chimera code --tui --inline`. The band should appear at the
   bottom without erasing the prior shell output above it.
2. **Stream a turn.** Ask for a multi-paragraph answer with a code block. Each
   paragraph/fence should appear in the transcript as it completes; the band
   should stay pinned and never flicker or duplicate.
3. **Native selection & copy.** With the turn done, select a few transcript
   lines with the mouse and copy them. Selection must work and the copied text
   must be the visible text (no escape sequences).
4. **Wheel-scroll.** Scroll up into history with the wheel/trackpad, then back
   down. Prior shell output and the whole transcript must be reachable.
5. **Resize.** Resize the window narrower and wider mid-session. The band must
   re-glue to the new bottom and stay usable (committed rows re-wrap per the
   emulator — that is expected).
6. **Cancel.** Start a long turn and press **Ctrl+C**. It should cancel the
   turn (not kill the app); the band returns to `● ready`.
7. **Clean exit.** `/exit` (or Ctrl+C when idle). The band must be erased and
   the shell prompt must resume directly under the last transcript line, which
   remains in scrollback. Run `seq 1 50` afterwards — it must scroll the whole
   screen normally (proving the scroll region was fully released).
8. **Crash safety.** (Optional, developer check.) If the process dies
   unexpectedly, the terminal must be left usable — cursor visible, no stuck
   scroll region — with the traceback readable.
9. **Multiplexer refusal.** Inside that one multiplexer, `chimera code --tui
   --inline` must print the fallback note and run full-screen, not lose
   scrollback.

Only after all of the above pass on your target emulators should a change flip
the default (or a per-project `[tui] inline = true` be recommended broadly).

## How it works (pointers)

- `chimera/tui/scrollback.py` — the stdlib-only escape-sequence builders
  (DECSTBM scroll region, reverse-index band glide, synchronized output), the
  `HybridScreen` terminal runtime with its state-restoration hooks, and the
  `inline_capability()` gate.
- `chimera/tui/inline_frontend.py` — the async frontend that drives an
  `AgentDriver`'s event stream through `HybridScreen`, reusing the shared
  `LaneTranscript` renderer so committed prose matches the full-screen frontend.
- `chimera/tui/shell_marks.py` — the OSC 133 vocabulary and the queue that
  drains marks onto the next committed row (stdlib-only, off by default).
- Design and evidence: the spike report `docs/specs/tui-scrollback-hybrid.md`
  (the mechanism, the terminal-emulator failure matrix, and the GO conditions).
