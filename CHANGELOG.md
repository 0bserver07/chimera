# Changelog

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
