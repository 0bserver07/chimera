# Changelog

## 0.8.1 — 2026-06-30 — `chimera code` becomes a real daily driver

The assembly/`CodingAgent` stack behind `chimera code` is now a usable
interactive coding agent: conversation memory, streaming with tool-call
rendering, a clean driver API for TUIs, unlimited runs with a loop-detector
safety net, and zero-config credential loading. Verified live on GLM-5.2.

### Added

- **`AgentDriver`** (`chimera/assembly/driver.py`): the control surface a REPL
  or TUI drives — `send()` streams typed events, plus `steer()` /
  `queue_follow_up()` / `cancel()` / `clear()`, and model / tools / cost /
  history / context-window state, with a `render_event()` helper. See
  `docs/building-a-tui.md`.
- **Unlimited runs**: `max_turns=None` and `chimera code --max-turns 0` run
  until the task completes; auto-compaction now tracks the provider's real
  context window instead of a fixed 100K budget.
- **Loop-detector safety net**: `AgentLoop` accepts a `loop_detector`; a stuck
  agent stops with reason `loop_detected` instead of spinning.
- **`.env` auto-load**: `chimera code` reads a project `.env` and
  `~/.config/chimera/env` at startup (existing shell vars always win), so
  GLM / Anthropic creds and model selection work without shell exports.
- **`tool_use` loop events** emitted before execution, so a UI can render a
  tool call ahead of its result.

### Changed

- **Conversation memory**: `CodingAgent` carries history across `run()` calls —
  the REPL is no longer amnesiac between turns.
- **`[1m]` model suffix** (e.g. `glm-5.2[1m]`) is stripped from the wire id
  (z.ai rejects the suffix) and honored as the context-window declaration, so a
  1M-token model is no longer compacted at the 200K default.
- **Model-aware default `max_tokens`**: GLM / Kimi / Qwen get 32768, Claude
  8192 (was a flat 4096) — no more truncated long edits. Callers can override.
- **`--workdir` correctness**: the file, shell, `test`, `replace_in_file`, and
  `apply_patch` tools now operate in the target directory, not the process CWD.
- **Sharpened `coding_agent` system prompt**: tighter, action-first, CLI-tuned.
- **Rebuilt `chimera code` REPL** on `AgentDriver`: fixed double-printing (REPL
  and `-p`), renders tool calls, real slash commands, per-turn cost; interactive
  turns disable the autonomous nudges that made plain questions ramble.

### Fixed

- Test isolation: the startup `.env` load no longer leaks credentials into
  `os.environ` across the test suite.

## 0.8.0 — 2026-06-11 — The comparative-methodology release

The mission deliverable ships: controlled comparative matrices with
uniform budgets, the Harbor/DeepSWE task format, ATIF v1.7 trajectory
interop with Pier, and the Field Guide. First live matrix published.

### Added

- **Uniform run budgets** (`chimera/core/budget.py`): `BudgetSpec` /
  `BudgetTally` / `BudgetEnforcer` / `BudgetedProvider`. The universal
  unit is completed tool calls, enforced once at the shared tool
  executor and audited across all four loop types (ReAct,
  PlanAndExecute, Reflexion, TreeOfThought stop at exactly N calls).
- **`chimera bench-compare`**: the controlled comparative matrix CLI —
  same model, tools, and per-task budget across N loop architectures;
  terminal / json / markdown / html output; per-task temp-dir
  environments; budget hits reported distinctly from failures; agent
  crashes isolated per task.
- **Harbor task-format adapter** (`--benchmark harbor`): consumes any
  Harbor task directory (validated against all 117 DeepSWE tasks);
  `docker_env_factory` provisions per-task images (new `docker` extra);
  verifier flow proven inside a live container.
- **ATIF v1.7** (`chimera/atif/`): emitter (EventBus subscriber, one
  step per API turn, verbatim assistant text), reader, validator, and
  the frozen upstream schema; `--emit-atif DIR` on bench-compare.
  Interop proven: Pier's own trajectory models validate Chimera output.
- **Teams plan-approval gate**: `requires_plan` tasks cannot complete
  until a lead approves; `team_propose_plan` / `team_approve_plan` MCP
  tools (lead-gated), `chimera team approvals` interactive loop.
- **Field Guide** (`site/.../field-guide/`): architecture catalog of
  the 10 replicated agents + sortable taxonomy, written from firsthand
  source reading.
- PlanAndExecute and Reflexion publish `ModelResponseEvent` per
  provider call (telemetry parity with ReAct).
- First controlled matrix + claude-models fan-out benchmark writeups;
  four implementation specs landed under `docs/specs/`.

### Fixed

- teammate-runner claim/release spinning (dep-aware spawnable filter,
  no-progress sleep, fingerprint-based reconsideration of released
  tasks so plan approval re-cues the agent).
- ProgramBench cleanroom reality fixes from the first live run
  (#141): extraction path is `/workspace`, flat input layout,
  workspace resolution for the macOS `/tmp` symlink, relative-path +
  oracle-availability prompt guidance.
- Opus 4.5/4.6/4.7 repriced to $5/$25 per MTok (4.8 added); legacy
  4.0/4.1 rate pinned by a regression test.
- Mocked docker tests no longer poison `chimera.env.docker` for
  real-daemon integration tests.
- ATIF emitter seals steps on consecutive `StepEvent`s (no more
  collapsed multi-turn trajectories).

## 0.7.0 — 2026-05-09 — Deprecation cuts + P1/P2 polish + first PyPI release

First release on PyPI (`pip install chimera-run`). Removes the v0.6.0
deprecation warnings and ships the W14 + W15 gap-closure work.

### Removed

- `AgentPreset.build()` — call `CodingAgent.from_preset(...)` instead.
- `chimera cc` top-level alias — use `chimera mink`.

### Added

- 12 codex-style ferret subcommands (`apply`, `review`, `fork`, `mcp-server`,
  `mcp {add|list|remove}`).
- otter polish: `worktree`, `stats`, `export`/`import`, PTY HTTP routes,
  remote skill marketplace.
- stoat: hooks engine + `/sessions` slash + `--continue`/`--session` flags +
  bracketed paste.
- badger: 12 new slashes (`/memory`, `/export`, `/agents`, `/skills`,
  6 git wrappers, `/bughunter`, `/ultraplan`).
- weasel print-mode: `--thinking`, `--stream-json`, piped stdin, multi-`-p`,
  `@file` expansion.
- shrew quality layer: `output_parser`, `quality_monitor`, `model_profiles`
  config, knowledge-axis skill scoring.
- mink: 9 remaining hook events wired (27/27 declared events fire),
  `settings.json` keys applied (theme, keybindings, statusline, output styles).
- ProgramBench inference loop (live agent runs end-to-end inside `task_cleanroom`
  containers).
- W15 P2 batch: 11 cosmetic / UX wins (4 ferret slashes, otter `/notify` + `/now`,
  badger `--profile` + `/teleport`, shrew `permission_gate` + `checkpoint`).

### Tests

- 7628 → 8206 passing (0 failed) across the wave.
- mypy clean across 641 files.
- Trademark scrubs clean for all 7 codenames.

## 0.6.0 — 2026-05-07 — P0 gap closure + benchmark scaffolds

Closes the 26 P0 items surfaced by the W12 audits.

### Added

- `apply_patch` tool (atomic multi-file patch DSL, ferret + otter default).
- 18 hook events wired (PreToolUse / PostToolUse / SessionStart / etc.) with
  per-event filtering on `HookMatcher`.
- 5 permission modes (`read-only` / `suggest` / `auto` / `yolo` / `strict`)
  via `--permission-mode` on ferret, badger, mink.
- `git`-shadow file-undo + `/undo` + `/redo` (otter).
- Declarative permission rules (otter, `~/.chimera/permissions.json`).
- Real Ctrl-X chord + plan mode (stoat).
- 4 default subagent profiles (planner / researcher / executor / reviewer).
- `/resume` + `/diff` slashes (badger; shared via the slash registry).
- Weasel: JS/TS extension execution + RPC streaming.
- Shrew: per-turn dynamic skill injection + 13 algorithm cheat-sheet skills +
  `write_guard` invariant.
- 14 new mink `settings.json` keys (theme / keybindings / statusline / output
  styles / cleanup / install / notification channel / etc.).
- 6 new ferret codex-flag triplet (`--full-auto`, `--yolo`, `--add-dir`,
  `--skip-git-repo-check`, `--image`, `--profile`).

### Benchmarks

- `programbench` (Yang et al 2026 — agent rebuilds program from binary + docs).
- `multi_swe_bench` with per-language runners (Java / Go / JS / Rust / Python).
- Scaffolds for `humaneval-x`, `swe-lancer`, `nocha`.

### Cross-cutting

- 13 new model entries across 7 families (qwen3, glm-4.6/5.1,
  deepseek-v3.1-terminus / coder-v3, gpt-oss, kimi-k2, mistral-codestral,
  gemma3) + GPT-OSS / Gemma prefix routing.
- chimera-plugin manifest (`plugin.json`).
- OAuth scaffolds cleanup (anthropic / openai marked as scaffolds; openrouter +
  xai unchanged).
- Help-long auto-promote helper (`register_argument`); all 7 CLIs `--help` ≤ 50
  lines.
- Plugin marketplace placeholder cleanup.

### Tests

- 6964 → 7628 passing (0 failed). +664.

## 0.5.0 — 2026-04-30 — Five-Strong Coding-Agent Family

The 0.5.0 release ships the full **mink / otter / ferret / weasel / shrew**
coding-agent family on a single Chimera substrate. v0.4.0 was cut for the mink
wave-2 milestone; 0.5.0 rolls up otter waves 1–2, mink waves 2–3, and the
wave-5 ship + wave-6 cross-CLI verification + wave-7 gap closure for the
three new CLIs (ferret / weasel / shrew). All five share the same `Agent` /
`AgentLoop` / `EventSourcedSession` / provider factory, the same 26-event
EventBus, and the same tool registry — adding a sixth is an additive walk
through the same agent allocation pattern.

### Headline — five coding-agent CLIs on one substrate

| CLI | Posture | Lines that distinguish it |
|---|---|---|
| `chimera mink` | TUI-first | `glm-5.1:cloud` defaults, 31 slash commands, 11 benchmark adapters |
| `chimera otter` | Server-first | TUI + HTTP `serve` + ACP `serve --acp`, share-by-link, 26 slash commands |
| `chimera ferret` | Sandbox-first / IDE-flagship | three sandbox modes, three approval presets, IDE-first ACP, cloud bridge |
| `chimera weasel` | Minimal harness, four modes | interactive / print / RPC / SDK, four-command slash palette, auto-discovered extensions |
| `chimera shrew` | Small-model tuned | local-first provider chain, 11 curated skills, MoE-aware context sizing, Aider Polyglot + GAIA |

Composition over rebuild — none of the new CLIs forks Chimera; each is a
thin posture on the upstream substrate. Trademark scrubs are clean across
all five (`bash scripts/all_trademark_scrub.sh` exits 0).

### New CLIs (waves 5 + 6 + 7)

- **`chimera ferret`** — sandbox-first / IDE-flagship coding agent.
  Three sandbox modes (`read-only` / `workspace-write` /
  `workspace-write-network`), three approval presets (`read-only` / `auto`
  / `full`), an IDE-first ACP transport that is a strict superset of
  otter's ACP schema with four extra notification kinds (`code/diff`,
  `editor/open_file`, `terminal/output`, `progress/step`), and an
  optional `chimera ferret bridge` long-poll HTTPS pipe with bearer auth.
  13 modules, 303 tests, 8 user docs.
- **`chimera weasel`** — minimal four-mode harness. A single resolver
  picks `interactive` / `print` / `rpc` / `sdk` from `--mode`, `-p`, or
  default. Newline-delimited JSON-RPC 2.0 over stdio (four methods, all
  five JSON-RPC error codes exported). Embeddable SDK
  (`from chimera.weasel.sdk import Agent`) with sync `run`, async `arun`,
  sync / async streaming, and multi-turn `chat`. Auto-discovered
  extensions from `.weasel/extensions/*` and `~/.weasel/extensions/`.
  Four-command slash palette (`/help`, `/exit`, `/clear`, `/model`) — no
  `/agent`, no `/share`, no `/init` by design. 9 modules, 164 tests,
  7 user docs.
- **`chimera shrew`** — small-local-model tuned harness layered on top
  of weasel. Local-first provider chain inversion (probes llama.cpp at
  `$LLAMACPP_BASE_URL` and Ollama at `$OLLAMA_BASE_URL` before any
  cloud key). Default `qwen3.6-35b-a3b` on llama.cpp. 11 curated skill
  markdowns (`knowledge/`, `protocols/`, `tools/`) with stdlib-only
  frontmatter parsing. Three small-model-fit extensions
  (`moe_offload`, `scaffold_fit`, `tool_filter`). Aider Polyglot + GAIA
  benchmark adapters. `--max-steps` defaulted to 30, restricted
  `--allowed-tools=Read,Write,Edit,Bash`. 14 modules, 193 tests,
  7 user docs.

### Otter (waves 1 + 2)

- `chimera otter` introduced as a sibling to `chimera mink` — streaming
  tool calls, hooks, sessions, and the same provider abstraction.
- HTTP server (`chimera otter serve --port`) and ACP server
  (`chimera otter serve --acp`) both wired.
- Slash palette grew to 26 entries; persisted-run inspection
  (`sessions list/show`); share-by-link (`chimera otter share`);
  preset registry; LSP first-class tools.
- Wired the full O-WIRE-1..6 set: real provider, MCP runtime,
  plugin → agent registry, rules → system prompt, custom commands →
  slash registry, LSP default-on.

### Mink (waves 2 + 3)

- `chimera mink runs cost` — per-run cost rollups and granular token
  breakdowns (cache, reasoning, per-step).
- Tau-bench (#90) end-to-end wireup + SWE-bench Verified (#84) adapter
  scaffold with `IPythonTool` + condensation hooks.
- 11 benchmark adapters under `chimera/eval/benchmarks/`
  (cline, context, dpai, feature, humaneval-plus, livecodebench, math500,
  mbpp, swe-polybench, swt, tau-bench).

### Server hardening

- TLS termination on otter `serve --tls-cert` / `--tls-key`.
- Cooperative cancellation propagation: `POST /session/<id>/cancel`
  drains the agent loop without killing the worker.
- Server-Sent Events resume-after-disconnect: `Last-Event-ID` header
  honored across reconnects.
- Per-step SSE events plumbed through `_drive_agent_streaming` over
  `async_run_events`; legacy fallback preserved.
- `GET /runs` and `GET /runs/cost` HTTP routes — eventlog inspection
  and per-run cost rollups now reachable over HTTP without dropping
  into the CLI.

### Event sourcing

- New `chimera/events/sourcing/` subsystem — registry, projector,
  `sqlite_store`, `sink`, `types`, export.
- Append-only event log with file locking, crash recovery, and gap
  detection; sessions can be reconstructed deterministically from the
  log alone.
- SQLite store + snapshot fast-resume in `EventSourcedSession` —
  resumed sessions skip log replay when a snapshot is current.
- `snapshot_after_turn` wired into otter's REPL + server.

### SSH / remote execution

- `AsyncSSHEnvironment` + SFTP transfer + ProxyJump bastion-host
  support + control-master multiplexing.
- `[ssh]` extra; `chimera mink --remote ssh://user@host[:port]/path`.
- `$HOME` expansion fix in checkpoint / restore tar paths so
  remote-environment snapshots round-trip correctly.

### Bench

- SWE-bench Verified scaffold landed (`chimera/eval/benchmarks/swe_bench_verified.py`)
  with `IPythonTool`, `SWEBenchConfig`, and condensation hooks.
- IPython tool + LLM condensation plumbed end-to-end through the
  SWE-bench Verified harness.
- HumanEval (full 164) live from each of the five CLIs against
  glm-5.1, kimi-k2.6, deepseek-v4-pro.

### Live verification

5×3 matrix exercised end-to-end on 2026-04-30:

| CLI \ Model | glm-5.1:cloud | kimi-k2.6:cloud | deepseek-v4-pro:cloud |
|---|---|---|---|
| mink | math + bash | math + bash | math + bash |
| otter | math (bash flake) | math + bash | math + bash |
| ferret | math + bash | math + bash | math (bash flake) |
| weasel | math + bash | math + bash | math (bash flake) |
| shrew | math (small-model bash limit) | math (small-model bash limit) | math (auto-deny) |

Math row 15/15. Bash row 9/15 (failures concentrated on smaller
context windows + auto-deny on shrew's deepseek path; no regressions
attributable to wave-7 work). Total wall-clock 813 s across 30 cells.
Documented in `research/I4-LIVE-MATRIX.md`.

### Quality

- **5654+ tests passing** (5651 in V1 validation pass + 660 ferret /
  weasel / shrew tests verified independently).
- **553+ mypy source files clean** (`Success: no issues found in 553
  source files`).
- **`uv run ruff check chimera/`** clean.
- **All five trademark scrubs clean** —
  `bash scripts/all_trademark_scrub.sh` exits 0
  (`passed: 5 (mink otter ferret weasel shrew); failed: 0`).
- **CI green** on Python 3.11 / 3.12 / 3.13 with five
  per-codename trademark-scrub jobs wired (`mink-trademark-scrub`,
  `otter-trademark-scrub`, `ferret-trademark-scrub`,
  `weasel-trademark-scrub`, `shrew-trademark-scrub`).

## 0.3.0 (2026-04-19) — Real Runtimes, Real Compilation, Honest Errors

### Function Synthesis — 3 real runtime backends
- `TransformersBackend` — HuggingFace transformers + PEFT adapter loading, aligned with bundle PEFT API
- `OnnxBackend` — ONNX Runtime execution, gated by new `function_synthesis_onnx` optional extra
- `ChiBundle` — `adapter_format` now accepts `peft` and `onnx` alongside the existing GGUF path
- `RuntimeBackend.stream()` — new ABC method; `LlamaCppBackend.stream()` implemented via `create_chat_completion(stream=True)`; all backends now stream
- Opt-in JSON-schema validation for input/output (minimal built-in validator, no extra deps)

### Function Synthesis — real compilation path
- `LocalCompiler` — fine-tunes PEFT LoRA adapters on-device, emits a loadable `.chi` bundle
- `import` path for existing HuggingFace PEFT adapters into `.chi` bundles

### Function Synthesis — CLI additions
- `chimera fs import-peft` — register an existing PEFT adapter as an installed program
- `chimera fs push` / `chimera fs pull` — move `.chi` bundles to/from a configured hub
- `chimera fs login` — write credentials via a file-backed `CredentialStore`
- `chimera fs rename` — slug-level moves via `ProgramRegistry.rename`

### Function Synthesis — hub adapters
- `HubAdapter` ABC + `HubError`
- `HFHubAdapter` — HuggingFace Hub upload/download for `.chi` bundles
- `S3HubAdapter` — S3-compatible object store upload/download
- All exported from the `function_synthesis` package

### Function Synthesis — top-level facade
- `compile()`, `load()`, `installed()`, `uninstall()` — one-liner lifecycle, exported from the package root
- Full-lifecycle demo added to docs, runnable without a GGUF

### Real bugs fixed (caught by live verification, not unit tests)
- `env/native_sandbox` — stop advertising Landlock; enforcement was never wired
- `env/docker` — checkpoint/restore now honest for live containers instead of silently no-op
- `rpc` — `SetModelCommand` handler was declared but never dispatched; now wired
- `learning` — `LearningStore.log()` was missing; `Dispatcher` wiring was dead
- `learning` — removed dead `field()` assignment in `FeedbackTracker.__init__`
- `function_synthesis` — `LlamaCppBackend` no longer hangs passing an empty `lora_path`
- `function_synthesis` — `PrefixCache` actually hits: pickle llama.cpp state so cold-start elimination works
- `review` — `ReviewFeedback.parse_from_text` was flipping "not approved" into an approval
- `providers` — `OpenAIResponsesProvider` normalises usage to Chimera keys (was reporting $0)
- `providers` — `OpenAIProvider` surfaces `reasoning_tokens` and `cache_read` tokens
- `providers` — `AnthropicProvider` stream emits cache tokens in the `done` usage frame
- `providers` — `ProxyProvider.complete` now accepts `thinking` and forwards tool calls
- `providers` — `CachedProvider` accepts + keys on `thinking`, forwards only when non-None
- `core` — `AutonomousLoop.iter_steps` now fires the same checkpoints/events as `run()`
- `core` — `PlanActLoop.iter_steps` actually yields plan-phase steps
- `core` — `TreeOfThought` `StepResult.cost` was always `0.0`, now reflects real spend
- `core` — async tool executor preserves `tool_calls` order; incremental variant now runs audit/checkpoint/wire hooks
- `context` — `FocusChain.add` validates relevance in `[0.0, 1.0]`; `MentionResolver` no longer captures trailing punctuation; `MemoryConsolidator.consolidate` no longer mutates input facts
- `detection` — `PatternCycleDetector` rejects `threshold<2` instead of vacuously matching
- `streaming` — `StreamHandler.handle_event` no longer double-dispatches `tool_start` and `done`
- `mcp` — `MCPServerLifecycle.connect` actually connects when asked; `initialize`/`tools-list` errors surface instead of silent success; `benchmark_server` exposes `list_benchmarks`
- `context` — `PruneProcessor` preserves `tool_call_id` on pruned tool messages
- `bridge` — `listen()` awaits async handlers; stops swallowing websocket errors
- `ci` — `CIFixWorkflow.run` honours `max_attempts` via a verify callback
- `migration` — `python2-to-3` and `commonjs-to-esm` expanded from skeletons to real rule sets
- `tools` — `DelegateTool` validates inputs and exposes class-level `name`; `TestTool` surfaces failure via `ToolResult.error`; `ImportGraphTool` wrapper exposes `import_graph` to agents
- `cli` — `/session list` in the rich REPL lists real sessions; `plugins` CLI uses the real `PluginManager` instead of a fake `Marketplace`; `/session` and `/history` stubs replaced with real impls; 13 fake/stub handlers replaced with real implementations
- `examples` — 19 broken examples fail friendly instead of tracebacking
- `hooks` — renamed `chimera/hooks/types.py` → `hook_types.py` (fixes 7 tests)
- `plugin` — `.mcp.json` now advertises all 6 MCP servers; `hooks.json` uses the Claude Code plugin format with module-loadable commands
- `docs` — `DocGenerator` emits complete signatures with defaults, varargs, annotations, and return types

### Type and lint sweeps
- Mypy: 299 errors → 0 across assignment, union-attr, attr-defined, arg-type, generic parameterisation, optional-dep imports, and untyped signatures
- Ruff: 446 lint errors → 0
- `warn_unused_ignores` disabled to stop churn against optional-dep shims

### CLI / REPL
- `_run_new_stack` REPL gains `/cost` and `/clear` plus an accurate `/help`

### Live-verified against real models
- llama.cpp — TinyLlama 460MB GGUF
- transformers — Qwen2-0.5B + PEFT adapter
- ONNX — tiny-gpt2 with `merge_and_unload`
- `LocalCompiler` → `TransformersBackend` chain exercised end-to-end

### Stats
- Tests: 3574 → 3953 (+379)
- 0 mypy errors, 0 ruff errors

## 0.2.0 (2026-04-16) — Function Synthesis Subsystem

### New: `chimera.function_synthesis`
Compile natural-language `FunctionSpec` objects into portable `.chi` bundles, load them as callable `CompiledFunction` instances, and expose them as agent tools.

**Core types**
- `FunctionSpec` — task spec dataclass (name, description, examples, schemas)
- `ChiBundle` — ZIP-format artifact (manifest, adapter, prompts, spec, metadata)
- `CompilerBackend` ABC + `CompilerError`
- `RuntimeBackend` ABC + `CompiledFunction` (context-manager API)

**Backends**
- `LlamaCppBackend` — local inference via optional `llama-cpp-python`
- `RemoteCompiler` — HTTP client speaking the [compile protocol](docs/function-synthesis-compile-protocol.md)
- `MockCompiler` — offline/test stub, deterministic bundles

**User-facing (v0.2.0 additions)**
- `CacheDirs`, `BaseModelCache`, `BundleCache` — on-disk cache at `~/.chimera/function_synthesis/` (overridable via `CHIMERA_FS_HOME`)
- `ProgramRegistry` + `slug_for()` — deterministic `<name>-<hash8>` slugs, JSON index
- `PrefixCache` — llama.cpp state cache for cold-start elimination (sha computed once per load)
- `CacheMissError`, `OfflineError` — offline mode via `CHIMERA_FS_OFFLINE=1`

**CLI: `chimera fs`**
- `compile <spec.json>` — compile + install in one step (`--compiler mock|remote`)
- `run <slug> <input>` — invoke an installed program against a base GGUF
- `list` / `info <slug>` / `rm <slug>` — manage the local registry

**Tools & strategies**
- `CompiledFunctionTool` — wrap any `CompiledFunction` as a Chimera agent tool
- `FunctionSynthesisStrategy` — training-loop strategy that compiles specs into bundles

**Docs & examples**
- `docs/function-synthesis.md` — quickstart + API tour
- `docs/function-synthesis-compile-protocol.md` — HTTP contract for remote compilers
- `examples/function_synthesis_quickstart.py` — runnable offline end-to-end walkthrough

**Tests** — 49 new tests (spec, bundle, compiler, runtime, cache, registry, prefix cache, mock, remote, CLI, strategy). Opt-in `-m live` e2e test requires a real base GGUF.

### Other
- `pyproject.toml` — `function_synthesis` extra now pulls `llama-cpp-python>=0.3.0` and `huggingface_hub>=0.25`
- `live` pytest marker registered for opt-in real-model tests

## 0.1.0 (2026-03-22) — Initial Release

### Claude Code Integration (#97-#115)
- **Plugin**: full `.claude-plugin` package with 5 slash commands, 3 subagents, 8 skills
- **MCP Servers**: codebase search (#100), code review (#101), test generation (#102), RAG/doc retrieval (#103), benchmark (#104)
- **Hooks**: path validation (#105), auto-test (#106), auto-lint (#107), security scanner (#108), stop verification (#109)
- **Anti-Hallucination**: codebase grounding system (#110) — blocks edits to non-existent files, semantic search, doc retrieval
- **Context Management**: proactive window manager with 70/85/90% thresholds (#111)
- **Loop Detection**: detect repeated commands and circular patterns (#112)
- **Persistent Memory**: survive session resets with fact extraction (#113)
- **Research Tools**: comparative agent benchmarking (#114), prompt engineering lab (#115)

### Ported Features (#73-#79)
- Relative Indenter from Aider (#73) — robust search/replace handling indentation mismatches
- AGENTS.md Discovery from Codex (#74) — hierarchical scanning with merge semantics
- Action Sampler from SWE-Agent (#75) — parallel completion sampling with scoring
- Multiple Edit Formats from Aider (#76) — whole-file, diff, search-replace, udiff
- Reviewer/Chooser from SWE-Agent (#77) — multi-stage solution ranking
- AI Comment Watcher from Aider (#78) — detect `# AI: fix this` patterns
- Memory Consolidation from Codex (#79) — two-phase explore→consolidate pipeline

### Core Framework
- 8-layer architecture: CLI → Workflows → Synthesis → Eval → Agent → Provider → Infrastructure → Environment
- 20 built-in tools (read, write, edit, bash, search, git, web_search, browser, etc.)
- 6 providers (Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compatible)
- 6 environments (Local, Docker, Git, Remote, Cloud, PersistentShell)
- 10 strategies (TestConvergence, TreeSearch, CEGIS, Incremental, MajorityVoting, etc.)
- `chimera.synthesize()` one-liner for code generation
- `chimera code` interactive REPL with 16 slash commands
- 11 CLI subcommands

### Agent Replication
- 8 agent architectures replicable: SWE-Agent, Aider, Cline, Codex CLI, OpenHands, Gemini CLI, OpenCode, Kimi CLI
- AgentPreset system — one-liner agent creation
- AutonomousLoop for long-running tasks
- RoleBasedTeam for multi-agent collaboration (planner → coder → reviewer → tester)
- 14 coding agent primitives (all audited as real implementations, not stubs)

### Pi-Mono Adoption
- CancellationToken — cooperative cancel with CancellableTool mixin
- MessageQueues — thread-safe steering + follow-up queues
- FileTracker — track read/modified files across compaction boundaries
- Provider registry — runtime provider registration
- ProxyProvider — HTTP relay for centralized key management
- ThinkingLevel — 6-level enum (OFF/MINIMAL/LOW/MEDIUM/HIGH/MAX) with per-provider mapping
- Tool operations — pluggable per-tool backends (ReadOps, WriteOps, BashOps, SearchOps)
- SessionTree — JSONL persistence with in-place branching
- RPC mode — headless JSON-RPC agent control
- OAuth flows — real device + browser flow implementations
- Auto-compaction — triggered when context > 80% of window
- Skills discovery — SKILL.md file walking + frontmatter parsing
- Extended events — 26 event types for full lifecycle coverage
- Model cycling — /model next, /model prev through --models list

### Ported Features (#73-79)
- Relative Indenter (Aider) — robust search/replace handling indentation mismatches
- AGENTS.md Discovery (Codex) — hierarchical scanning with child-overrides-parent merge
- Action Sampler (SWE-Agent) — parallel completion sampling with scoring
- Multiple Edit Formats (Aider) — whole-file, diff, search-replace, udiff
- Reviewer/Chooser (SWE-Agent) — multi-stage solution ranking
- AI Comment Watcher (Aider) — detect "# AI: fix this" patterns
- Memory Consolidation (Codex) — two-phase explore → consolidate pipeline

### Infrastructure
- Prompt caching + extended thinking support
- Ghost commits (snapshot-based undo)
- Response caching (SHA-based dedup)
- OS-native sandboxing (macOS Seatbelt)
- File watcher (reactive re-run)
- Context condensation (smart compaction, thought stripping)
- Codebase indexing (TF-IDF + optional embeddings)
- Interactive approval UX
- Project config auto-discovery (CHIMERA.md / CLAUDE.md / AGENTS.md)
- Trajectory logging (JSON/JSONL)
- Diff proposal workflow (stage, accept/reject, apply)
- Commit message style inference
- Head+tail output truncation
- Repo map context injection
- LSP feedback middleware
- Apply middleware (Cursor-style proposed changes)

### Benchmarks
- HumanEval (full 164): **90.9% pass@1**
- Terminal-Bench (10 tasks): **30%**
- SWE-bench Lite (20 instances): **10%**
- 9 workflow + synthesis tests verified against real GLM-5
- 48 total live integration tests against real LLM
- 13 benchmark transparency issues filed (#84-96)
- SWE-bench scripts: proper eval (test_patch after), Docker isolation, anti-hesitation, OpenHands-style

### Release Infrastructure
- MIT license
- CI/CD: GitHub Actions (Python 3.11, 3.12, 3.13 + docs deploy)
- CONTRIBUTING.md
- Starlight documentation site (114 pages, Mermaid diagrams, dark theme)
- 0 lint errors (ruff clean)

### Stats
- 2632 tests passing, 0 failures
- 340+ public exports
- 39 runnable examples
- 5 MCP servers, 5 hooks, 8 skills, 3 subagents
- 102-line README
- 115 GitHub issues (26 closed, 13 benchmarks open)
