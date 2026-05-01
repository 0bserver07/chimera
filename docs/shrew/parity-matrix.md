---
title: Shrew Parity Matrix
description: Surface-by-surface mapping between chimera shrew and the upstream small-model coding agent — GREEN (at parity / superset), YELLOW (partial), RED (deferred or out of scope).
---

# `chimera shrew` Parity Matrix

**Source baseline:** `research/shrew/SPEC.md` (Apr 2026), upstream
small-model coding agent source-tree walk.
**Updated:** wave-5 ship (S1–S6), refreshed 2026-04-30 by the
wave-6 cross-CLI verification (X1–X3, I-series).
**Legend:** GREEN = shipped / at parity (or superset); YELLOW = partial; RED = deferred or out of scope.

> **Wave-6 verification (2026-04-30).** Live state at handoff:
> `uv run ruff check chimera/shrew/` clean; `uv run mypy
> chimera/shrew/` clean (part of the 36-source-file mypy run that
> covers ferret + weasel + shrew); `uv run pytest tests/shrew/ -q`
> = 193 passed in ~0.4s. Trademark scrub passes on docs and skill
> markdowns; the 6 hits in `chimera/shrew/cli.py` /
> `tests/shrew/test_cli.py` are inside docstring + scrub-policy
> commentary (pre-existing from S1, tracked as `S-FIX-1` in
> `research/shrew/HANDOFF.md`). `chimera shrew 0.5.0` boots,
> `--help` is brand-clean, and the bundled 11 skill markdowns +
> three small-model extensions + Aider Polyglot / GAIA bench
> harness load end-to-end. Shrew is now Tier 1 alongside mink and
> otter.

> **Trademark hygiene.** Throughout this document the upstream
> project is referred to as "the small-model coding agent" or "the
> upstream". Filesystem path mentions like `~/.shrew/skills/` are
> kept because they are facts about directories shrew can read on
> disk, not brand claims. See
> [`security-and-trademarks.md`](security-and-trademarks.md).

## Top-level surfaces

The upstream ships a single coding-agent CLI tuned for small local
models, with three high-leverage moves: a curated skill set, MoE
serving tricks, and a benchmark harness. Shrew mirrors all three on
top of weasel.

| Upstream surface | Shrew status | File | Notes |
|---|---|---|---|
| One-shot CLI (`-p`) | GREEN | `chimera/shrew/cli.py` | Inherits weasel print mode. |
| Interactive REPL | GREEN | `chimera/shrew/repl.py` | Inherits weasel REPL. |
| RPC mode (stdio) | GREEN | `chimera/shrew/cli.py` | Late-binds to weasel's RPC server. |
| SDK | YELLOW | n/a | Embed via `from chimera.weasel.sdk import Agent`; no shrew-specific SDK module yet. |
| `--list-models` | GREEN | `chimera/shrew/cli.py` | Provider-driven catalogue. |
| Sessions list / show | GREEN | `chimera/shrew/sessions.py` | Reads `~/.chimera/eventlog/shrew-*/`. |
| Curated skill set | GREEN | `chimera/shrew/skills/` | 11 markdowns across knowledge / protocols / tools. |
| Small-model extensions | GREEN | `chimera/shrew/extensions/` | `moe_offload`, `scaffold_fit`, `tool_filter`. |
| Local-first provider chain | GREEN | `chimera/shrew/providers.py` | llama.cpp → Ollama → cloud. |
| Benchmark harness | GREEN | `chimera/shrew/benchmarks/` | Aider Polyglot + GAIA wired. |
| Terminal-bench adapter | RED | n/a | Reserved by parser; not yet wired. |

## CLI flags

The upstream's flag surface is small by design. Shrew mirrors it
and adds the small-model-specific knobs (`--vram-gb`,
`--no-skills`).

| Upstream flag | Shrew status | Shrew equivalent | Notes |
|---|---|---|---|
| `-p` / `--print` | GREEN | `-p` / `--print` | Identical; inherits weasel. |
| `--json` | GREEN | `--json` | Single JSON blob on stdout. |
| `--mode <m>` | GREEN | `--mode interactive\|print\|rpc\|sdk` | `sdk` is import-only. |
| `--model` | GREEN | `--model` | Same syntax; default `qwen3.6-35b-a3b`. |
| `--list-models` | GREEN | `--list-models` | Provider-driven. |
| `--cwd` | GREEN | `--cwd` | Same. |
| `--max-steps` | GREEN | `--max-steps` | Default 30 (smaller than weasel's). |
| `--allowed-tools` | GREEN | `--allowed-tools` | Default `Read,Write,Edit,Bash`. |
| `--vram-gb` | GREEN | `--vram-gb` / `$SHREW_VRAM_GB` | Drives `moe_offload`. |
| `--no-skills` | GREEN | `--no-skills` | Skip bundled skill set. |
| `--no-color` | GREEN | `--no-color` | Plain output handler. |
| `--verbose` | GREEN | `--verbose` | Inherits weasel. |
| `--resume <id>` | GREEN | `--resume <id>` | Inherits weasel. |
| `--api-key` | YELLOW | env vars preferred | Inline flag deferred for security. |
| `--login` | RED | n/a | OAuth flow deferred; `chimera auth login` covers it. |

## Provider chain

Shrew **inverts** weasel's hosted-first ordering: a reachable local
server wins over any cloud key. This is the single biggest
divergence from weasel and a deliberate design choice — shrew
exists to prove that small local models are good enough for real
coding work.

| Upstream provider | Shrew status | Notes |
|---|---|---|
| llama.cpp HTTP | GREEN | Default. Probed at `$LLAMACPP_BASE_URL`. |
| Ollama daemon | GREEN | Probed at `$OLLAMA_BASE_URL`; routed via OpenAI-compat shim. |
| OpenRouter (`vendor/name`) | GREEN | Routed via OpenAI-compat against `openrouter.ai`. |
| Anthropic | GREEN | Cloud fallback. |
| OpenAI | GREEN | Cloud fallback. |
| Google | GREEN | Inherits weasel. |
| Modal-hosted | YELLOW | Programmatic only (no auto-detection). |
| Custom | GREEN | `register_provider("name", factory)`. |

Catalog (built-in model ids):

| Model id | Backend | Context | MoE? |
|---|---|---|---|
| `qwen3.6-35b-a3b` | llama.cpp | 32k | yes (35B / 3B-active) |
| `qwen3.5-9b` | llama.cpp | 32k | no (9.7B dense) |
| `qwen3.5:cloud` | Ollama | 262k | no |
| `qwen3.5` | Ollama | 32k | no |

Plus the `MoEModelProfile` catalog in
`chimera/shrew/extensions/moe_offload.py` adds
`deepseek-coder-v2-lite-16b` (16B / A2.4B MoE) for context-window
sizing.

## Skills

The upstream's skill catalogue is the single biggest small-model
adjustment. Shrew ships a curated subset.

| Upstream skill category | Shrew status | Files |
|---|---|---|
| `knowledge/` | GREEN | 4 files: `scaffold_model_fit`, `context_window_discipline`, `escalation_signals`, `python_idioms` |
| `protocols/` | GREEN | 4 files: `edit_before_write`, `error_recovery`, `one_focused_question`, `test_first_python` |
| `tools/` | GREEN | 3 files: `core_tools`, `grep_vs_ls`, `multi_file_edits` |
| Per-language idiom skills (Rust, Go, TS) | YELLOW | Python-only today; pattern is in place to add others. |
| User overlay (`~/.shrew/skills/`) | GREEN | Honored in `discover_shrew_skills(extra_search_paths=...)`. |
| Project overlay (`.shrew/skills/`) | GREEN | Honored when shrew is run from a project that ships them. |
| Frontmatter `triggers` field | GREEN | Parsed; not yet auto-fired (LLM is shown the index, not the trigger phrases). |
| Per-skill kill-switch flag | RED | `--no-skills` is all-or-nothing today. |

## Extensions (small-model adjustments)

The upstream's small-model adjustments map to three named
extensions in shrew. Each is independent and pure-functional.

| Upstream concept | Shrew status | File | Notes |
|---|---|---|---|
| MoE-aware context sizing | GREEN | `extensions/moe_offload.py` | `MoEModelProfile` + `compute_optimal_context_window()`. |
| Scaffold-model-fit prompt wrapping | GREEN | `extensions/scaffold_fit.py` | `wrap_for_small_model()`; threshold 13B. |
| Tool-list filtering for tiny models | GREEN | `extensions/tool_filter.py` | `filter_tools_for_model()`; threshold 9B. |
| `--no-scaffold` flag | RED | n/a | Disable via running a frontier model; flag deferred. |
| Catalogue-driven model size lookup | GREEN | `model_size_billions()` in `tool_filter` + MoE catalog. |

## Benchmarks

The upstream ships a Python harness for several benchmarks. Shrew
ports the two most useful for small-model coding.

| Upstream benchmark | Shrew status | File |
|---|---|---|
| Aider Polyglot | GREEN | `benchmarks/aider_polyglot.py` |
| GAIA | GREEN | `benchmarks/gaia.py` |
| Terminal-bench | RED | Reserved by parser; not yet wired. |
| Harbor / SWE-bench Lite | RED | Out of scope for the small-model focus. |
| Setup-hint on missing dataset | GREEN | Both adapters; exit code 3. |
| Per-language filter (polyglot) | GREEN | `--language python` etc. |
| Per-level filter (GAIA) | GREEN | `--level 1` etc. |
| Built-in scorer (GAIA-style normalisation) | GREEN | Re-implemented locally; stdlib-only. |

## Sessions / eventlog

Shrew reuses Chimera's event-sourced session store directly.

| Capability | Shrew status | Notes |
|---|---|---|
| `~/.chimera/eventlog/shrew-<id>/` directory layout | GREEN | Same shape as `weasel-`, `otter-`, `mink-`, `ferret-`. |
| `summary.json` per run | GREEN | Inherits weasel. |
| `event-*.json` event stream | GREEN | Inherits weasel. |
| `sessions list` | GREEN | `chimera shrew sessions list`. |
| `sessions show <id>` | GREEN | `chimera shrew sessions show <id>`. |
| Session resume | GREEN | `--resume <id>` (inherits weasel). |

## Counts

- **Surfaces:** 9 GREEN, 1 YELLOW, 1 RED of 11.
- **CLI flags:** 13 GREEN, 1 YELLOW, 1 RED of 15.
- **Providers:** 6 GREEN, 1 YELLOW, 0 RED of 7.
- **Skills:** 6 GREEN, 1 YELLOW, 1 RED of 8.
- **Extensions:** 4 GREEN, 0 YELLOW, 1 RED of 5.
- **Benchmarks:** 6 GREEN, 0 YELLOW, 2 RED of 8.
- **Sessions:** 6 GREEN of 6.

> **Wave-6 live verification:** the GREEN counts above were
> spot-checked against `chimera shrew --help`, `chimera shrew
> --list-models`, the 193 passing tests under `tests/shrew/`, the
> 11 skill markdowns under `chimera/shrew/skills/`, and the three
> small-model extensions under `chimera/shrew/extensions/`. No row
> was downgraded by the wave-6 audit; shrew meets every contract
> above at the black-box level.

## Chimera-only capabilities (do not regress)

Shrew inherits Chimera primitives the upstream does not have:

- Cooperative `CancellationToken` (true mid-turn cancel).
- `MessageQueues` for safe mid-turn steering.
- Loop detection (exact + pattern cycle).
- `EventSourcedSession` crash recovery + gap detection.
- `FileAwareCompaction` (file tracking across compaction).
- `RedactionMiddleware` for ten secret patterns.
- `CostTracker` with cache + reasoning-token breakdown.
- 26-event `EventBus` with middleware.
- Multi-CLI sibling stack (mink, otter, ferret, weasel) shares the
  same eventlog format, so a `shrew-*` session can be inspected by
  the same `chimera otter sessions show` tooling.

## Follow-up issues to file

1. `--no-scaffold` flag for `scaffold_fit` opt-out.
2. `--skills <selector>` for per-skill enable / disable.
3. terminal-bench adapter under `benchmarks/`.
4. Per-language idiom skills beyond Python (Rust, Go, TypeScript).
5. Shrew-specific SDK module (`chimera/shrew/sdk.py`) so embedders
   get the small-model defaults without re-deriving them.
6. Auto-fire skill bodies based on the frontmatter `triggers`
   field, instead of relying on the model recalling skills by name.

## How to use this matrix

Shrew is a delta on weasel; if a behaviour isn't called out here,
it's because it's identical to weasel. Read
[`docs/weasel/parity-matrix.md`](../weasel/parity-matrix.md) for
the underlying surface and assume shrew inherits unless this page
says otherwise.

GREEN rows are expected to behave in lockstep with the upstream
small-model coding agent at the black-box level. YELLOW rows
degrade gracefully and emit a hint where the gap is user-visible.
RED rows are not implemented, by design or by deferral; the table
makes the reason explicit.

## See also

- [`quickstart.md`](quickstart.md) — five-minute tour.
- [`small-model-setup.md`](small-model-setup.md) — llama.cpp +
  GGUF + MoE serving incantation.
- [`skills.md`](skills.md) — bundled skill catalogue.
- [`extensions.md`](extensions.md) — `moe_offload`, `scaffold_fit`,
  `tool_filter`.
- [`benchmarks.md`](benchmarks.md) — Aider Polyglot + GAIA.
- [`security-and-trademarks.md`](security-and-trademarks.md) —
  trademark hygiene + security posture.
- [`docs/weasel/parity-matrix.md`](../weasel/parity-matrix.md) —
  the underlying weasel surface shrew layers on.
