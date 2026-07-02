# TUI UX Refinements: Rendering, Folding, Status Line, Themes, Keys

**Status:** design spec (implementation-independent).
**Scope:** the presentation *quality* layer of the shipped interactive frontends
(`docs/specs/interactive-frontends.md`, all phases shipped in 0.9.0) — how
transcripts read, how much they show, what the status surfaces say, and what the
user can customize.
**Sources:** a six-reference corpus of mature terminal coding agents, mined
first-hand for mechanisms with file receipts (receipts live in the mining
session, not here). References are cited by architecture, not by name:

| Ref | Architecture |
|-----|--------------|
| R1 | Rust full-screen agent, native-scrollback hybrid, immediate-mode widgets |
| R2 | Component-framework full-screen agent (reactive per-part rendering) |
| R3 | Python chat-scrollback agent (line-editor + rich-text stack) |
| R4 | Rust line-oriented REPL (readline-class editor, minimal cursor control) |
| R5 | Component-framework agent (research notes on its architecture) |
| R6 | Custom differential-line renderer + overlay-stack agent |

This document names no reference brands and no concrete rendering library in
its requirements; implementation notes referencing our shipped Textual code are
confined to §10.

---

## 1. Motivation

0.9.0 shipped the structure (lanes, panes, streams, artifacts). What separates
a *usable* agent terminal from a *lovable* one is a layer of presentation
discipline the reference corpus has converged on independently:

1. **Streaming correctness** — never render half-parsed markup; commit
   completed blocks, keep only a live tail.
2. **Truncation discipline** — cap what's displayed, never what's recorded,
   and always say how to see the rest.
3. **An ephemeral/persistent contract** — progress is display-only; results
   are transcript; the two never mix.
4. **Customization surfaces** — status line, theme, and keys belong to the
   user, driven by one registry each, not scattered hardcodes.

Every requirement below was observed working in at least one reference; where
references disagree, the tradeoff is stated.

## 2. Principles

- **P1 — Commit-only-complete.** A block enters the permanent transcript only
  when it is syntactically complete (markdown block boundary, closed code
  fence, finished table). The live region holds the rest.
- **P2 — Display caps, never data caps.** Truncation applies to pixels, not to
  the session record; every elision names the remainder and the affordance
  ("… +N lines — <key> expands").
- **P3 — Terminal-first color.** Prefer the terminal's own palette (ANSI-named
  colors) for prose and syntax so light/dark terminals both look native; use
  hex only where semantics demand it (diff backgrounds), with light/dark
  variants.
- **P4 — One registry per customization surface.** Status-line items, theme
  slots, keybindings, and slash commands are each a single declarative table
  that drives rendering, help, completion, and user overrides together.
- **P5 — Ephemeral ≠ persistent.** Spinners, progress, heartbeats, toasts:
  display-only, never persisted, never sent to the model. Results, committed
  prose, terminal reasons: transcript. (The 0.9.0 event contract already
  guarantees this for `tool.progress`; this spec extends it to all chrome.)
- **P6 — Degrade, don't break.** Every surface defines its narrow-terminal,
  16-color, no-animation, and non-interactive behavior.

## 3. Transcript rendering & spacing

- **R-REN-1 — Block grammar with a role gutter.** Every transcript block gets a
  one-column gutter that encodes role and state: user echo glyph, assistant
  bullet, tool glyph, error/status coloring (green success, red failure).
  Prose is *not* boxed; boxes are reserved for cards (tool blocks, approvals).
  (R1 gutter prefixes `›`/`•`; R3 bullet columns colored by state; R2
  role-colored left bar.)
- **R-REN-2 — Vertical rhythm: one blank line between blocks, adaptive for
  runs.** Exactly one separator line between committed blocks. Consecutive
  one-line items (inline tool rows) pack tight with zero margin; a row
  following a tall card gets the full gap. (R3 uniform separators; R2
  pre-layout sibling spacing — margins decided by the previous element's
  height.)
- **R-REN-3 — Neutral markdown.** Assistant prose renders with minimal color:
  emphasis styles, heading underlines/rules, code and links colored — body text
  in the terminal's default foreground. Headings may carry a level-scaled rule
  (`═` for H1, `─` for H2+). (R3's neutralized markdown theme is the model.)
- **R-REN-4 — Terminal-palette syntax highlighting.** Code highlights use a
  16-ANSI-color scheme by default so they inherit the user's palette; a
  truecolor theme applies only when the theme system (§6) selects one.
- **R-REN-5 — Per-tool renderers with a distinct icon + verb.** Tool calls
  dispatch to tool-specific renderers, not one generic printer: shell `$`,
  read `→`, write/edit `←`, search `✱`, task/delegate `↳`, each with a
  one-line argument summary. Cheap tools render as one-line inline rows; tools
  with rich output render as block cards. (R2's part-typed dispatch + 13
  bespoke renderers; R5's per-tool UI modules; R4's per-tool result
  formatters.)
- **R-REN-6 — Stream-safe commitment.** Streamed prose is parsed
  incrementally; *completed* top-level blocks flush to the permanent
  transcript, only the trailing (possibly partial) block stays live. Code
  fences track fence char + length so nested/tilde fences don't false-close;
  tables hold back in the live tail until complete (a new row reshapes all
  columns); a partial *closing* fence is trimmed from the live render so the
  block doesn't shrink/relayout when the final backtick arrives. Rendered
  markdown may be cached by (text, width). (R3 incremental commitment; R4
  boundary buffering + fence tracking; R1 stable/tail split + table holdback;
  R6 closing-fence trim + render cache.)
- **R-REN-7 — Nested-fence normalization.** Model output that nests ``` inside
  ``` upgrades the outer fence (more backticks or tildes) before rendering, so
  inner markers become content. (R4; markdown widgets mis-parse this
  identically everywhere.)
- **R-REN-8 — Turn separators that carry a work summary.** A turn that did
  real work closes with a dim rule; long turns inline a summary
  (`─ Worked for 2m 3s · 4 tool calls ─`). Purely conversational turns emit no
  rule. (R1.)
- **R-REN-9 — Wrapping preserves long tokens.** URLs and long identifiers wrap
  as units (no hard splits mid-URL); wrapped content re-measures for height.
  (R1 adaptive wrapping.)
- **R-REN-10 — Diff rendering: word-level inline highlights, bounded
  previews.** Inline diffs pair removal/addition runs; when a run pairs 1↔1,
  word-diff the pair and inverse-highlight only the changed tokens, skipping
  leading whitespace so indentation is never highlighted (and skip entirely
  when similarity is low); previews cap at N hunks × M lines with the §4
  affordance; oversized files fall back to a size summary. (R3 + R6
  word-level diffing; R4 bounded previews; R5 structured hunk model.)

## 4. Folding, collapsing, truncation

- **R-FOLD-1 — Reasoning collapses to a heartbeat, then a trace.** While
  thinking: an animated one-liner with elapsed, token count, and a tok/s pulse
  (`Thinking ··· 5s · 1.2k tokens · 40 tok/s`) — the pulse is what makes
  hidden reasoning feel alive. On completion: a one-line committed trace
  (`Thought for 5s · 1.2k tokens`), expandable to the full text on toggle.
  (R3's two modes; R1's summary-or-nothing with full text in the transcript
  overlay; 0.9.0 already collapses — this adds the heartbeat + trace.)
- **R-FOLD-2 — Head+tail elision with an explicit affordance.** Tool output
  over its cap shows first-N + last-N lines with a dim middle marker naming
  the count and the key: `… +37 lines (<expand-key>)`. Caps are per-tool-class
  (interactive shell higher than search); counts measure *wrapped* visual
  lines. Caps bind on lines *and* chars, whichever hits first. One **global
  expand toggle** flips all tool blocks between collapsed/expanded. (R1
  middle-elision + hint; R4 dual caps; R2 click-to-expand; R6 global expand
  key + last-N-visual-lines.)
- **R-FOLD-3 — Display/persistence split.** The full output always lives in
  the session record and the persisted artifact; truncation is display-only
  and says so. Very large results may spill to a file with a pointer
  (`[Showing lines X–Y of Z — full output: <path>]`). (R4's "full result
  preserved in session" notice; R5's disk-spill with frozen-per-id previews;
  R6's tempfile pointer format.)
- **R-FOLD-4 — Auto-collapse read/search/list tools.** Classify tool calls
  (search / read / list) and collapse those by default once completed —
  a finished turn reads as prose + edits, with tool detail on toggle. (R5's
  collapse classification; R2's completed-tools auto-hide.)
- **R-FOLD-5 — Sub-agent folding.** Nested/delegated agent activity shows a
  live progress line and only the last few tool calls (`N more …`), with the
  full sidechain in its own record, never inline. (R3 last-4 deque; R5
  sidechain transcripts + grouped parallel render — directly applicable to
  lanes.)
- **R-FOLD-6 — Paste collapsing, atomic.** Input pastes over a size threshold
  collapse to a placeholder chip (`[Pasted ~N lines]`) that expands on submit;
  the full text rides with the message. The chip is an **atomic edit unit** —
  cursor movement, word-nav, and delete treat it as one token, never exposing
  its interior. (R2, R1, R3 converge on the chip; R6 on atomicity.)
- **R-FOLD-7 — The transcript overlay is the universal fold target.** One
  full-screen overlay shows the complete, untruncated transcript (pager-style
  navigation); every elision hint points at it. Copy-friendly plain rendering
  is available there (see R-VIEW-4). (R1's pager overlay as the single
  expansion surface.)

## 5. Status line (customizable) & terminal title

- **R-STAT-1 — A status-line item registry.** All status content comes from a
  registry of named items — model, reasoning level, cwd/project, git branch ±
  dirty/ahead/behind, run state, permission mode, context-used %, context
  window, token totals, cost, lane/cohort progress, version — each with an id,
  a renderer, and hide-when-unavailable behavior. **CONFIG:** the status line
  is an ordered list of item ids in the TUI config; an interactive
  `/statusline` picker (multi-select + reorder + live preview) persists it.
  (R1's 27-item ordered registry + picker; R5's user-scriptable status line.)
- **R-STAT-2 — Width degradation per segment.** Each segment checks remaining
  columns and degrades stepwise (model+reasoning → model → nothing; cwd
  truncates from the left with `…`; low-priority badges drop first). (R3's
  toolbar degradation.)
- **R-STAT-3 — Never block the render loop.** Git status and other external
  facts resolve asynchronously with TTL caches; the status line renders what
  it has. Branch detection should watch the repo's HEAD *directory* (atomic
  writes rename over HEAD, changing the inode), debounced. (R3 cached
  background git; R1 async branch/PR resolution; R6 atomic-write-aware watch.)
- **R-STAT-4 — Context meter as ambient truth, threshold-colored.** Context
  usage (`42% · 28.5k/128k`) is always visible somewhere cheap (status item or
  sidebar), not only surfaced on overflow errors — warning color above ~70%,
  error color above ~90%, an `(auto)` marker when auto-compaction is armed. A
  compact stat vocabulary (`↑in ↓out` tokens, cache read/write, `$cost`) keeps
  the line dense but scannable. (R2 sidebar panel + prompt footer; R6
  thresholds + vocabulary; R4's absence of a meter is the anti-pattern.)
- **R-STAT-5 — Composable terminal title.** The terminal title mirrors a
  second ordered item list (activity spinner, project, run state), sanitized
  (control/bidi chars stripped), restoring the prior title on exit.
  **CONFIG:** title item list + on/off. (R1; R2's `app | session` title.)

## 6. Themes

- **R-THEME-1 — Semantic slot themes.** A theme maps ~40–60 named semantic
  slots — base (primary/accent/text/muted/backgrounds/borders/error/warning/
  success/info), markdown family, syntax family, diff family, and opacity
  knobs (e.g. reasoning dimming) — not per-widget colors. A theme may declare
  a `vars` palette that slots reference by name (resolved with circular-ref
  detection), so palettes swap without touching slots. (R2's slot schema and
  R6's schema-validated tokens + vars are the models; R4's hardcoded single
  theme is the anti-pattern.)
- **R-THEME-2 — Dark/light per slot + auto mode detection.** Every slot may
  carry `{dark, light}` variants; with no explicit choice, mode is detected
  via a cascade — color-scheme query where supported, else terminal-background
  luminance, else environment hints, else dark — and mode-change
  notifications are honored where the terminal emits them. **CONFIG:**
  `theme`, `theme_mode` (auto/dark/light/lock). (R2 terminal-derived mode; R1
  luminance default; R6's full detection cascade.)
- **R-THEME-3 — User theme files + live preview.** Themes load from a themes
  directory in the user config (and project config), layered defaults < user;
  a `/theme` picker previews live and restores on cancel. (R2 filesystem
  discovery + hot reload; R1 preview-with-cancel-restore.)
- **R-THEME-4 — Color-depth + motion degradation.** Detect
  truecolor/256/16; quantize gracefully; honor `NO_COLOR`; an `animations`
  toggle turns spinners into static glyphs and disables shimmer/sweep
  effects. **CONFIG:** `animations`. (R1 perceptual quantization +
  reduced-motion gate; none of the corpus honors `NO_COLOR` — we should.)

## 7. Keybindings

- **R-KEY-1 — Declarative binding registry.** One table maps named actions →
  `{default keys, description, context}`; it drives the runtime dispatch, the
  footer hints, `/help`, and the command palette *from the same data*.
  (R2's definitions→command-map; R5's full engine with reserved-shortcut
  guard.)
- **R-KEY-2 — User rebinding via config.** **CONFIG:** a `keybinds` table
  (action → key string, list of keys, or `false` to unbind), validated against
  the registry with clear unknown-action errors, **conflict detection**
  (two actions bound to one key in the same context is reported, not
  silently shadowed), and a legacy-name migration table so renamed actions
  don't break old configs. Contexts (global / composer / pager / approval)
  layer, most-specific wins. (R1 global+per-context tables; R2 leader-key
  strings; R6 conflict detection + migration.)
- **R-KEY-3 — Config-aware hints.** Every on-screen key hint renders the
  *currently bound* key for the action, so hints stay true after rebinding.
  (R5's shortcut-display hooks.)
- **R-KEY-4 — Reserved keys.** A small reserved set (interrupt, quit) cannot
  be unbound. (R5's reserved-shortcuts guard.)

## 8. Input, viewport, ephemera

- **R-IN-1 — One submission classifier (kept).** The shipped pure router
  (new-turn / steer / follow-up / local) stays the single chokepoint; the only
  key-level exception is an immediate-steer binding. Queued messages render
  above the input with an edit-recall affordance. (R3's classifier +
  queue-edit; R1's Tab-queues/Enter-submits.)
- **R-IN-2 — Completion from registries.** `/` completion reads the slash
  registry (aliases + gray argument hints); `@` completion fuzzy-matches
  files (repo-aware listing with cache invalidation). (R2, R3, R5 converge.)
- **R-IN-3 — History with reverse search.** Per-project persistent input
  history (dedup consecutive), up/down recall, and incremental reverse search
  with live preview and cancel-restore. (R3 per-workdir history; R1 merged
  history + reverse search.)
- **R-IN-4 — Interrupt affordance.** Esc/cancel is two-stage while running
  (first primes, second interrupts, footer narrates) or a single priority
  binding with an explicit hint in the working indicator. (R2 double-esc;
  R1's `Esc to interrupt` inline hint.)
- **R-VIEW-1 — Sticky bottom with an escape hatch.** New content pins the view
  to the bottom; scrolling up freezes position and shows a "new content below"
  affordance; message-boundary navigation (prev/next block) supplements
  page/line scroll. (R2 sticky scrollbox + boundary jumps; 0.9.0 partially
  ships this.)
- **R-VIEW-2 — Working indicator: delayed, elapsed, honest.** Progress chrome
  appears only after a short threshold (~2s), shows elapsed + a per-tool
  activity verb, escalates long silent operations with a heartbeat
  (`Ns elapsed · step · phase`), and shows retry countdowns
  (`retrying in 3s · attempt 2/5`) instead of freezing. (R5 delayed reveal +
  per-tool verbs; R4 heartbeat + stall timeout; R2 retry countdown.)
- **R-VIEW-3 — Ephemera never persist.** Spinners, heartbeats, toasts,
  progress lines, retry banners: display-only. The persisted transcript and
  the artifact carry only committed content — enforced at the transcript
  model, not the widget. (R5's hard contract: progress excluded from history
  chains and save/load; already our LaneTranscript posture — extend to all
  chrome.)
- **R-VIEW-4 — Copy-friendly rendering mode.** A raw/plain rendering of any
  transcript (no gutters, no color) is one toggle away, for clean copy/paste
  and for piping. (R1's rich/raw dual render per cell.)
- **R-VIEW-5 — Inline (native-scrollback) mode as an option.** Half the corpus
  deliberately avoids the alternate screen and never captures the mouse: the
  editor + footer pin to the bottom of the *normal* buffer, committed content
  flows up into the terminal's own scrollback, and native selection/copy and
  wheel-scroll keep working for free. The single-agent frontend SHOULD offer
  this as a mode; the multiplexer's tiled panes remain full-screen. When
  inline, the working area repaints only its bottom band and reserves
  idle-status space so the layout never jumps. **CONFIG:** `inline` (or an
  `alt_screen: auto/always/never` tri-state). (R6's defining choice + R4 + R3
  scrollback-native; R1's tri-state config + no-mouse-capture; the tradeoff
  vs full-screen widgets is a real decision, not a default.)
- **R-NOTIFY-1 — Focus-aware attention.** Turn-done / approval-needed /
  question events may ring the terminal bell or send an OS notification,
  gated by terminal focus (default: only when unfocused) and by config.
  **CONFIG:** `notifications` (off/bell/system/auto), `notify_when`
  (unfocused/always), optional per-event sounds. (R1 OSC-9/BEL + focus
  tracking; R2 sound packs + focus gate.)

## 9. Overlays & dialogs

- **R-OVER-1 — A modal stack with priorities.** Overlays (pickers, approvals,
  questions, transcript pager) push onto a z-ordered stack; input routing
  respects the top; esc pops; prior focus restores through nested overlays
  (a pre-focus chain, not a single slot). Overlays position declaratively
  (anchor + offset + %-widths) and may carry a size-dependent visibility
  callback so they hide on narrow terminals. Prefer swapping pickers *into
  the normal flow* (replacing the input slot, saving/restoring its draft)
  over floating — float only when overlap is the point. Approval prompts may
  carry an inline feedback field; multiple questions render as tabs. (R3
  modal priorities + approval/question panels; R2 dialog stack with backdrop;
  R6 z-order/anchors/focus-restore chain + pickers-in-flow.)
- **R-OVER-2 — One fuzzy select to rule them all.** A single fuzzy-select
  dialog component powers the command palette, cohort picker, model picker,
  and theme picker: filter, category grouping, per-row description, current
  pre-selection, footer key hints. (R2's universal dialog-select; our
  `/cohorts` picker is the first instance — generalize it.)

## 10. Mapping to the shipped code (implementation notes)

Confined here so the requirements above stay library-agnostic:

- R-REN-6/7 land in `chimera/tui/render.py` (gate markdown commitment on
  complete blocks; add fence normalization before the markdown renderable).
- R-FOLD-1's heartbeat extends `LaneTranscript`'s thinking accumulator;
  R-FOLD-2 replaces the current fixed 1500-char truncation in `format_event`.
- R-STAT-1's registry replaces the hardcoded strings in `MultiplexApp`
  `_global_status_text` / `LanePane._header_text`; `/statusline` joins the
  slash registry (which already exists as `SLASH_COMMANDS` — extend it to
  R-KEY-1's richer `{keys, description, context}` shape and derive
  autocomplete + /help from it).
- R-THEME-1..3 map to Textual CSS variables generated from a slot dict; theme
  files live under `~/.chimera/themes/` and `.chimera/themes/`.
- R-KEY-2 reads a `keybinds` table from the existing config chain
  (project `.chimera`/global `~/.config/chimera`), applied over `BINDINGS`.
- R-FOLD-7's overlay and R-OVER-2's fuzzy select build on the `ResultsScreen`
  / `/cohorts` picker patterns already in `chimera/tui/`.
- The ephemeral/persist contract (R-VIEW-3) is already structural
  (`Lane.record` vs pane rendering); the audit is to ensure *no* chrome writes
  into `transcript_lines`.
- R-VIEW-5 maps to Textual's inline mode (`App.run(inline=True)`) for the
  single-agent frontend — spike its interaction with RichLog/scrolling before
  committing; the multiplexer keeps the full-screen app.
- Relation to open issues: #170 (budgets) adds status items (R-STAT-1);
  #171 (approvals) lands as an R-OVER-1 modal; #172 (single-app unification)
  should precede heavy investment in `app.py`-side duplicates; #169
  (external-agent lanes) benefits from R-FOLD-5's sub-agent grammar.

## 11. Config surface (consolidated)

All knobs this spec introduces, in one table (names indicative):

| Key | Meaning | Default |
|-----|---------|---------|
| `tui.status_line` | ordered status item ids | `[model, context-used, cost, run-state]` |
| `tui.title` | ordered title item ids, or off | `[activity, project]` |
| `tui.theme` | theme name (built-in or file) | auto by luminance |
| `tui.theme_mode` | auto / dark / light | `auto` |
| `tui.animations` | spinners/shimmer on/off | `true` (respects `NO_COLOR`) |
| `tui.keybinds` | action → key override table | `{}` |
| `tui.reasoning` | collapsed / hidden / shown | `collapsed` |
| `tui.tool_output_lines` | display cap per tool class | `10` (shell), `3` (generic) |
| `tui.notifications` | off / bell / system / auto | `auto` |
| `tui.notify_when` | unfocused / always | `unfocused` |
| `tui.scrollbar` | show scrollbar | `false` |
| `tui.timestamps` | per-message timestamps | `false` |
| `tui.inline` | native-scrollback mode (single-agent) | `false` |

## 12. Phasing

| Phase | Deliverable | Rationale |
|-------|-------------|-----------|
| **1 — Correctness & discipline** | R-REN-6/7/9 (stream-safe commit, fence normalization, wrapping), R-FOLD-2/3 (elision + display/persist split), R-VIEW-2/3 (honest progress, ephemera audit) | Fixes the failure modes every streaming terminal hits; no new config surface |
| **2 — Customization registries** | R-STAT-1..5 (status line + title), R-KEY-1..4 (bindings), R-THEME-1..4 (themes), §11 config | The user-facing asks: statusline customizable, themes, keys |
| **3 — Grammar & polish** | R-REN-1..5/8/10 (block grammar, per-tool renderers, separators, diffs), R-FOLD-1/4/5/6, R-IN-2..4, R-VIEW-1/4/5, R-NOTIFY-1, R-OVER-1/2 | The lovable layer (R-VIEW-5 inline mode is exploratory — spike first) |

Each phase is independently shippable; Phase 1 has no UI-visible config and can
ride a patch release.

## 13. Testing strategy

- Pure functions first: block-boundary detection, fence normalization,
  head+tail elision, status-item degradation, keybind parsing/merging — all
  exhaustively unit-testable without a terminal.
- Golden rendering via the headless harness (deterministic event scripts →
  stable visible output), per the interactive-frontends spec §9.
- Streaming torture corpus: nested fences, half-open tables, giant single
  lines, ANSI-laden tool output, markdown-heavy prose (reuse persisted
  cohort transcripts as fixtures).
- Config matrix: default / fully-customized statusline + keybinds + theme
  file; `NO_COLOR`; narrow terminal; animations off.
- One live smoke per phase on GLM-5.2 (project rule: real model before "done").
