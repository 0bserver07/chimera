# Chimera

Compose coding agents from modular primitives. Synthesize codebases from specifications.

## Quick Reference

- **Language:** Python 3.11+
- **Build:** hatchling + uv
- **License:** MIT
- **Setup:** `uv sync --extra dev --extra anthropic`
- **Tests:** `uv run pytest` (10421 passing + 98 skipped locally as of 2026-07-27, excluding the live-infra files `tests/integration/test_env_docker_integration.py`, `tests/env/test_modal_sandbox.py`, `tests/env/test_ssh_live.py`; one `tests/function_synthesis/test_validation_split.py` test is env-sensitive locally but green in CI)
- **Lint:** `uv run ruff check chimera/`
- **Types:** `uv run mypy chimera/`
- **CI posture (run before pushing batches):** `bash scripts/ci_posture_check.sh` — CI installs NO `tui` extra, so a green local gate can still be a red CI. New modules importing textual/rich need the pyproject `[[tool.mypy.overrides]]` textual block; tests importing them need `pytest.importorskip`. mypy caches are posture-specific — trust only cold-cache runs when extras change.
- **Docs:** Astro/Starlight in `site/`. Local: `cd site && pnpm install && pnpm dev`. Deploys to <https://0bserver07.github.io/chimera/> via `.github/workflows/ci.yml`.
- **⚠️ "Could not connect to the Modal server" is usually NOT a Modal outage.** Under this repo's default venv interpreter (CPython **3.12.8**) every connect to Modal's API fails *instantly* with `OSError: [Errno 9] Connect call failed ('<ip>', 443)`; the client retries 8× and reports the message above, which reads exactly like infrastructure being down. Verified 2026-07-24 that it is not: status.modal.com fully green, and `curl`/`nc`/other interpreters reached the identical IP:443 fine. **Verified workaround — pin a different interpreter for anything touching Modal:** `uv run --python 3.13 --extra modal-sandbox --extra anthropic modal …` (also `chimera bench-matrix --env modal|swe-modal|e2b|daytona`, `scripts/modal_bench_app.py`). **Root cause is UNKNOWN — do not repin `.python-version` on a guess.** It is *not* asyncio-specific (blocking sockets fail the same), *not* TLS-specific (plain TCP fails), and *not* uv-vs-system (uv's own 3.11.7 works); the same 3.12.8 reaches other hosts fine, i.e. it is destination-*and*-interpreter-specific, which points at environmental interposition rather than a CPython defect. Cheap next probe: `uv python install --reinstall 3.12`. Diagnostic order before ever concluding an outage: status page → `curl -s -o /dev/null -w '%{http_code}' https://api.modal.com` (200 = reachable) → retry pinned. Cost of not knowing this: hours of misdiagnosis and a dead SWE-bench run.
- **Versioning — SUB-VERSIONS, do not march the digit.** After `0.9.2`, batches ship as `0.9.2.1`, `0.9.2.2`, … (fourth component); the dev version is `0.9.2.N.dev0`, **never `0.9.3.dev0`**. Moving the third digit (→ 0.9.3) needs an explicit owner decision, never accumulation; 1.0 needs a breakthrough. Full rules: `docs/playbooks/14-release-discipline.md`.

## Architecture

8-layer stack (each layer usable independently):

```
Layer 8: CLI             chimera synthesize / eval / bench / code / review /
                         ci-fix / research / docs / testgen / migrate / plugins
Layer 7: Workflows       CIFixWorkflow, ReviewOrchestrator, Researcher,
                         MigrationPlanner, DocGenerator, TestGenerator
Layer 6: Synthesis       Trainer, Strategy, Spec, Architecture, Constraint
Layer 5: Evaluation      Harness, Metrics, Benchmarks (SWE-bench, HumanEval, AIMO)
Layer 4: Agent           Agent, Tools, Loops, Prompt, Context, Critic, ACP,
                         Cancellation, MessageQueues, FileTracker, Operations
Layer 3: Provider        Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compat,
                         Proxy, Registry, ThinkingLevel
Layer 2: Infrastructure  Security, Secrets, Permissions, Events, Sessions, Wire,
                         Compaction, Streaming, Detection, Config, Plugins, MCP, LSP,
                         SessionTree, RPC
Layer 1: Environment     Local, Docker, Git, Remote, Cloud, PersistentShell
```

## Module Map

### Core (`chimera/core/`)
- `agent.py` — Agent class, main entry point
- `context.py` — Conversation history manager
- `loop.py` — ReAct loop (reason-act-observe), stream-level cancellation, steering drain, 10 lifecycle event emissions
- `loop_config.py` — LoopConfig dataclass (permissions, detection, events, audit, checkpoints, git workflow, cancellation, message_queues, file_tracker)
- `tool_executor.py` — Shared tool execution with permission/event/detection/audit/cancellation/file-tracking hooks
- `prompt.py` — System prompt with variable substitution
- `tool.py` — BaseTool ABC and @tool decorator
- `tool_group.py` — ToolGroup, DEFAULT_TOOLS, `create_default_tools(ops=...)` factory
- `cancellation.py` — CancellationToken (thread-safe cooperative cancel), OperationCancelled, CancellableTool mixin
- `file_tracker.py` — FileTracker (track files read/modified across compaction boundaries)
- `message_queue.py` — MessageQueues (thread-safe steering + follow-up queues)
- `operations.py` — ReadOps, WriteOps, BashOps, SearchOps protocols + Local implementations
- `loops/` — PlanAndExecute, Reflexion, TreeOfThought

### Providers (`chimera/providers/`)
- `base.py` — Provider ABC (with `thinking` param), Response, StreamEvent
- `factory.py` — `create_provider()` auto-detection via registry
- `registry.py` — Runtime provider registry (`register_provider`, `get_provider_factory`, self-registration)
- `capabilities.py` — Declarative capability matrix: `ProviderCapabilities` quirk record keyed by `WireProtocol` (openai-compat/anthropic-compat/google), `resolve_capabilities`/`register_capabilities` (protocol default → provider → model-prefix override). Providers source quirks from it (`CompatFlags` is its openai-compat projection). Add a backend on an existing protocol as a ~20-line data row — guide: `docs/guides/add-a-provider.md`
- `thinking.py` — ThinkingLevel enum (OFF/MINIMAL/LOW/MEDIUM/HIGH/MAX), `budget_for_level()`
- `proxy.py` — ProxyProvider (HTTP relay for centralized key management)
- `anthropic.py`, `openai_provider.py`, `google.py`, `ollama.py`, `modal.py`, `compatible.py`
- `cost.py` — Per-model pricing and cost calculation
- `cost_tracker.py` — Granular token tracking (cache, reasoning, per-step breakdown)

### Tools (`chimera/tools/`)
**49 tool modules ship**; two curated groups in `core/tool_group.py` decide what an agent actually gets (verify with `python -c "from chimera.core.tool_group import AGENT_TOOLS; print(len(AGENT_TOOLS.tools))"` — never quote a count from memory):
- `DEFAULT_TOOLS` (**4**): bash, read_file, read_image, write_file
- `AGENT_TOOLS` (**23**), the interactive set: apply_patch, bash, cron_create, cron_delete, cron_list, edit_file, enter_worktree, exit_worktree, git, list_files, notebook_edit, read_file, read_image, replace_in_file, repo_map, search, test, think, todo, verify_answer, web_search, write_file, write_guard
- Shipped but outside both groups (opt-in via `create_default_tools(ops=…)`, presets, or plugins): browser, delegate, dmail, import_graph, ask_user, web_fetch, ipython, powershell, task_tool, and others under `chimera/tools/`

### Environments (`chimera/env/`)
- `base.py` — Environment ABC + `glob_match` (the ONE definition of
  `list_files(pattern)`: pathlib glob semantics, `*` stops at `/`). Backends
  that enumerate paths remotely must filter through it or benchmarks see
  different file sets per sandbox.
- `factory.py` — `create_environment(provider, **opts)`, the single entry point
  for every backend + `register_environment` for custom ones
- `local.py` / `git_env.py` / `docker.py` — filesystem, branch isolation, container
- `ssh.py` — `SSHEnvironment` (stdlib, subprocess `ssh`/`scp`) and
  `AsyncSSHEnvironment` (`[ssh]` extra: asyncssh, native SFTP, ProxyJump chains,
  connect retries, bounded concurrency)
- `remote.py` / `cloud.py` — HTTP workspace server / HTTP provisioning API
- `modal_sandbox.py` / `e2b.py` / `daytona.py` — managed cloud sandboxes
- `native_sandbox.py` — OS-native confinement (macOS Seatbelt, Linux Landlock)
- **Cloud backends fail loudly.** Missing SDK → `ImportError` with the extra
  hint; missing creds → `ValueError` at construction; `bench-matrix --env` exits
  2. Never let a sandbox degrade to local — the result would be
  indistinguishable from a real cloud run. Guide:
  `docs/guides/remote-and-cloud-environments.md`
- **Test posture:** fake SDK/transport injected at the module boundary
  (`chimera.env.e2b.Sandbox`, `chimera.env.daytona._sdk`,
  `chimera.env.ssh.asyncssh`) so the tests run in CI, which installs no extras.
  `pytest.importorskip` on an optional extra means the test never guards a merge.

### Training (`chimera/training/`)
- `trainer.py` — Trainer orchestrator
- `spec.py` — Spec (task specification)
- `architecture.py` — Architecture (multi-layer builds)
- `strategies/` — TestConvergence, TreeSearch, Curriculum, Ensemble, MajorityVoting, AIMOEnsemble, Passthrough, CEGISStrategy, IncrementalStrategy

### Composition (`chimera/composition/`)
- Pipeline (sequential), Ensemble (parallel), Supervisor (coordinator + workers)

### Evaluation (`chimera/eval/`)
- Harness, Benchmark ABC, metrics (pass@k, resolve_rate, avg_cost)
- Benchmarks: SWE-bench, HumanEval, AIMO, Custom

### Workflows (`chimera/workflows/`, `chimera/ci/`, `chimera/review/`, `chimera/research/`, `chimera/migration/`, `chimera/docs/`, `chimera/testgen/`)
- `workflows/git_workflow.py` — GitWorkflow (branch isolation, diff context, commit strategies)
- `ci/fix_workflow.py` — CIFixWorkflow: parse CI logs → prompt → Agent.run() → retry loop
- `review/orchestrator.py` — ReviewOrchestrator: reviewer Agent + author Agent iteration
- `research/researcher.py` — Researcher: plan decomposition → Agent.run() → synthesis
- `migration/planner.py` — MigrationPlanner: rule-based code transforms with presets (python2-to-3, commonjs-to-esm)
- `docs/generator.py` — DocGenerator: AST-based documentation scanning and generation
- `testgen/generator.py` — TestGenerator: source analysis → test case skeletons

### Agent System (`chimera/agents/`)
- `config.py` — AgentConfig with from_markdown(), build(), registries
- `presets/` — Build, Plan, Explore, General, Review preset agents
- `registry.py` — AgentRegistry with register, get, list, load_directory
- `loader.py` — FileAgentDef, AgentLoader (priority: project > user > built-in), AgentFactory

### Critic (`chimera/critic/`)
- `base.py` — Critic ABC, CriticResult, CriticConfig, CriticMode (all_actions / finish_only)
- `llm_critic.py` — LLMCritic (provider-based), ChecklistCritic (rule-based)
- `mixin.py` — CriticMixin for loop integration with iterative refinement

### Agent Client Protocol (`chimera/acp/`)
- `types.py` — ACPSessionConfig, ACPToolCall, ACPResponse
- `client.py` — ACPClient (JSON-RPC 2.0 over subprocess stdio)
- `tool.py` — ExternalAgentTool (wrap external agents as Chimera tools)

### Security (`chimera/security/`)
- `risk.py` — SecurityRisk enum, RiskClassifier
- `analyzer.py` — SecurityAnalyzer ABC, LLMSecurityAnalyzer, RuleBasedSecurityAnalyzer, CompositeSecurityAnalyzer
- `policy.py` — ConfirmationPolicy ABC, NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold

### Secrets (`chimera/secrets/`)
- `registry.py` — SecretRegistry (register, redact, env-var loading)
- `detector.py` — SecretDetector (10 built-in patterns: API keys, AWS, Bearer, private keys, etc.)
- `redactor.py` — RedactionMiddleware for EventBus

### Permissions (`chimera/permissions/`)
- `base.py` — 5 policies (AutoApprove, AlwaysDeny, AllowList, DenyList, custom)
- `audit.py` — AuditEntry, AuditLog (record, summary, for_tool, clear)
- `risk.py` — RiskLevel enum, classify_risk() for bash patterns

### Checkpoints (`chimera/checkpoints.py`)
- CheckpointManager: create, restore_by_name, restore_by_id, undo, list_checkpoints

### Events (`chimera/events/`)
- EventBus, 26 event types, middleware
- Core: ToolCall, ToolResult, Step, TextDelta, Error, LoopDetected, Permission, Session, StepCost
- Lifecycle: AgentStart/End, TurnStart/End, StreamStart/End, ModelRequest/Response
- Advanced: Compaction, Critic, ExternalAgent (Start/Complete/ToolCall), Security, Steering, Cancellation

### Sessions (`chimera/sessions/`)
- `session.py` — Session with chat(), iter_chat(), fork(), save(), resume(), steer(), queue(), cancel(), auto-compaction
- `tree.py` — SessionTree (JSONL persistence with in-place branching via parent_id, fork, switch, thread-safe)
- `storage/` — Memory, File, SQLite backends
- `eventlog/` — Event-sourced persistence (append-only log, file locking, crash recovery, gap detection)

### Compaction (`chimera/compaction/`)
- `base.py` — CompactionStrategy ABC, AtomicGroup, CompactionView, CompactionUrgency, CompactionMetadata, FileAwareCompaction
- `summary.py` — SummaryCompaction (extends FileAwareCompaction, includes file tracking in summaries)
- `strategies.py` — Prune, Counter, Composite
- `thresholds.py` — ThresholdCompaction (SOFT/HARD thresholds, tool call/result atomicity)

### Config (`chimera/config/`)
- `paths.py` — **the path registry**: the one declared truth for every on-disk
  store (`Store` rows: name/scope/rel/writer/prunable/note) + `chimera_home()`,
  `project_state_dir()`, `store_path()`, `all_stores()`, `store_retention()`.
  Root precedence: `$CHIMERA_HOME` → `[storage] root` → `~/.chimera`. Guide:
  `docs/guides/storage-and-paths.md`
- `storage.py` — the inspection + retention engine over that registry:
  `report_stores()` / `find_orphans()` (orphan scan covers project-root
  `.chimera*` **siblings**, not just the two scope roots) feed `chimera doctor
  --section storage`; `plan_gc()` / `apply_prune()` feed `chimera gc` (dry-run
  default, `--apply`/`--archive` opt-in). `select_for_prune` is the ONE
  retention implementation — the cohort pruner calls it. Guide:
  `docs/guides/storage-inspection-and-gc.md`
- `user_config.py` — the ONE config chain (XDG < user < project), any of
  `config.{toml,yaml,yml,json}`; `load_section` / `load_tui_config` /
  `load_storage_config`
- `union.py` — DiscriminatedUnion base (from_config/to_config dispatch, type field validation)
- `config_file.py` — ChimeraConfig for YAML/JSON loading
- `loader.py` — ProjectConfig discovery

### Plugins (`chimera/plugins/`)
- `base.py` — BasePlugin ABC, ComponentRegistry, Hook, MCPServerConfig
- `manager.py` — PluginManager (load, unload, discover)
- `registry.py` — PluginExtensionRegistry (agents, strategies, constraints, middleware, skills, MCP, hooks)
- `dir_loader.py` — DirectoryPluginLoader (agents/*.md, .mcp.json, hooks/)
- `marketplace.py` — PluginInfo, MarketplaceRegistry, Marketplace (search, install, uninstall)

### Wire (`chimera/wire/`)
- `types.py` — WireMessage, WireRequest/Response, TurnBegin/End, StepBegin/End, ApprovalRequest/Response, UserQuestion/Answer, StatusUpdate
- `wire.py` — Wire bidirectional channel (send, request/response, listeners)

### Skills (`chimera/skills/`)
- `flow.py` — Flow (Mermaid flowchart → decision tree → agent prompt), FlowNode, FlowEdge
- `discovery.py` — Skill discovery (walk SKILL.md files with YAML frontmatter), `discover_skills()`, `format_skills_for_prompt()`

### Other Infrastructure
- `chimera/streaming/` — Stream handlers, StreamingReAct
- `chimera/detection/` — Loop detection (exact repeat, pattern cycle)
- `chimera/mcp/` — MCPClient (stdio/HTTP), MCPToolSource, from_config()
- `chimera/mcp_servers/` — 7 stdin/stdout JSON-RPC servers (search, review, testgen, migration, rag, benchmark, team coordination) + teammate_runner (drives external agent CLIs against a team queue)
- `chimera/lsp/` — LSP client, diagnostics, completion, rename
- `chimera/auth/` — API key, OAuth device/browser flows (real stdlib HTTP impl), credential store (file-based, 0o600 perms)
- `chimera/rpc/` — JSON-RPC server (stdin/stdout), RpcHandler (prompt/steer/cancel/get_state/compact), command/response/event types

### Testing (`chimera/testing/`)
- `harness.py` — hermetic agent-loop harness: `create_harness` (real AgentLoop) / `create_assembled_harness` (AgentDriver/CodingAgent) run FauxProvider scripts through the REAL loop with real tools in a temp workspace; `HarnessRun` exposes ordered LoopEvents, tool calls/results, file diffs, usage/cost, terminal reason. Regression locks live in `tests/regressions/` (commit-named, revert-verified). Complements — never replaces — real-LLM validation (guide: `docs/guides/testing-agents.md`).

### Assembly (`chimera/assembly/`)
- `coding_agent.py` — CodingAgent, the assembled daily-driver stack behind `chimera code` (presets, conversation memory, loop postures via `LOOP_POSTURES`)
- `driver.py` — AgentDriver: the one control surface a REPL/TUI drives (send/steer/cancel/clear/load_history + model/tools/cost/history)
- `loop_adapter.py` — run a strategy loop (plan-execute/reflexion/tot) as a LoopEvent stream (worker-thread bridge, bounded provider)
- `presets.py` / `system_prompts.py` / `tool_sets.py` — AssemblyConfig PRESETS (coding_agent, codex, minimal, explore), prompts, tool factories

### Embedding (`chimera/embed.py`)
- The stable SDK surface (semver-stable within 0.9.x): `chimera.AgentSession` (AgentDriver subclass + blocking `run()`/`run_async()` → `TurnResult`, `close()`, context manager) and `chimera.run_agent` one-liner; re-exported from the package root with `AgentDriver`/`render_event`/`LoopEvent`/`LoopEventType`. Guide: `docs/guides/embed.md`

### TUI (`chimera/tui/`)
Interactive frontends over AgentDriver (spec: `docs/specs/interactive-frontends.md`, all 3 phases shipped):
- `app.py` — DEPRECATED shim (ChimeraTUI/run_tui superseded by the one-lane multiplexer, #172; importable one release, not load-bearing)
- `multiplex.py` — the multiplexer: N lanes race one task (`--tui --models a[:preset[:loop]],b,…` / `chimera otter --multiplex`), broadcast/targeted routing, resume; bare `chimera code --tui` = `run_single_agent` (one inplace lane, single-lane chrome, model verbatim)
- `lane.py` / `cohort.py` — Lane (driver+workspace+telemetry+tool_log), Cohort (manifest, persistence to `~/.chimera/cohorts/`, export, list/load for `--resume`)
- `workspace.py` — per-lane isolation (git worktree per lane, copy fallback; `apply_diff` for resume)
- `render.py` / `results.py` / `prompt.py` / `routing.py` / `history_io.py` — shared transcript rendering (markdown assistant prose, collapsed reasoning), comparison screen (scoreboard + per-file/split diffs), multi-line prompt + slash autocomplete, pure input routing, faithful history codec

### Codename Agent CLIs (`chimera/{mink,otter,ferret,weasel,shrew,stoat,badger}/`)
- 7 replicated coding-agent CLIs: `chimera mink|otter|ferret|weasel|shrew|stoat|badger` (aliases: tui, multi, sandbox, mini, tiny, shell, strict)
- `mink/team.py` — `chimera team` subcommand (create/join/task/status/ls/rm/watch/approvals/roles)
- `mink/team_approvals.py` — interactive plan-approval loop for team leads
- `cli/agent_teams.py` — Team + TeamMailbox primitives (file-locked JSONL task queue, deps, requires_plan gate)

### CLI (`chimera/cli/`)
- `main.py` — 30+ subcommands: synthesize, eval, bench, code, the 7 codename CLIs, team, resume, agents, review, ci-fix, research, docs, testgen, migrate, fs, config, which, tier-status, completion, plugins, doctor, auth
- `code.py` — Interactive REPL with 19 slash commands, two-mode terminal (readline idle / raw stdin running), threaded agent execution
  - Commands: /help, /model (next/prev cycling), /cost, /clear, /history, /tools, /context, /debug, /session, /compact, /audit, /checkpoint, /agent, /init, /yolo, /tree, /branch, /switch, /exit
  - Flags: `--mode interactive|rpc|json`, `--models glm-5,claude-sonnet-4` (comma-separated for cycling)
  - Features: mid-turn steering, Ctrl+C cancellation, session auto-save to `~/.chimera/sessions/`, auto-compaction, skills discovery

## Definition of Done (sweep BEFORE reporting anything as done)

1. Gates green — tests, ruff, mypy, scrubs; `bash scripts/ci_posture_check.sh` when the change touches optional-extra code (CI installs no `tui` extra).
2. Pushed, and CI green **on that SHA** (watch the `ci.yml` run, not the newest workflow).
3. New user-facing surfaces have user docs (`docs/guides/`), not just specs/docstrings.
4. `CHANGELOG.md` Unreleased entry lands **with** the work (playbook 14).
5. Lessons become repo rules (CLAUDE.md / playbooks / scripts) — never only session memory.
6. GitHub issues closed for shipped work, with commit references.
7. Leftover processes/worktrees/cloud infra reconciled or explicitly handed off.

## Key Conventions

- **The repo root is a guarded interface** (`tests/test_repo_hygiene.py`): no
  loose `.py` files at the root, and no new top-level entries without extending
  that test's allowlist in the same commit. Run outputs never live in the repo:
  datasets stage to `~/.chimera/datasets`, results are explicit `--output`
  files with curated receipts committed under `data/`, one-off drivers go in
  `scripts/experiments/`, raw run dirs belong outside the repo. Why: `pb_*.py`
  scratch drivers + 1.3 GB of `pb-runs/`/`runs/` output accumulated at the
  root, six of the files gitignored *while tracked* — invisible to every gate
  until an owner audit found them. The claim that package code writes no
  cwd-relative directory is now **enforced**, not asserted: a static `ast`
  scan in the same test file fails the suite on a literal relative write
  (`os.makedirs("runs")`, `Path("out").mkdir()`, …). Caller-supplied,
  temp-, and home-rooted paths are deliberately not flagged.
- **Never hand-build a `~/.chimera` path.** Every on-disk store goes through
  `chimera/config/paths.py` — `store_path("<name>")`, `chimera_home()`,
  `project_state_dir(project)` — and adding a store means adding a `Store`
  row, not a code path. A directory the registry does not name is, by
  definition, an orphan: that is what lets `doctor` report it and what makes
  it structurally impossible for `gc` to delete something undeclared. Why:
  ~90 hand-built `Path.home() / ".chimera" / …` constructions across 60 files
  meant nobody could answer where data lived or what was safe to reclaim, and
  a 2.0 GB checkpoint tree sat undetected for four months — written, it
  turns out, by a LIVE writer (`LocalEnvironment.setup()`), not an orphan. Acceptance for
  any change here is the grep audit: zero home-anchored `.chimera`
  constructions outside `paths.py`. Guide: `docs/guides/storage-and-paths.md`.
- **Zero-dependency core.** Only stdlib in main package. Providers and tools like browser (playwright), remote env (httpx) are optional extras.
- **TYPE_CHECKING imports.** Use `if TYPE_CHECKING:` for cross-module type hints to avoid circular imports.
- **3-tier API.** Every feature has: one-liner convenience, developer configuration, framework-author subclassing.
- **LoopConfig pattern.** All loop-level features (permissions, detection, compaction, streaming, events, audit, checkpoints, git workflow, cancellation, message queues, file tracking) funnel through a single `LoopConfig` dataclass injected into loop constructors. When `None`, behavior is unchanged.
- **Google-style docstrings.** Use Args/Returns/Raises sections.
- **Tests mirror source.** `chimera/foo/bar.py` → `tests/test_bar.py` or `tests/test_foo.py`.
- **Name-shaped guards must be tested against the name the LOOP sees, not the
  name you wrote it for.** MCP tools arrive namespaced (`mcp__<server>__<tool>`),
  so any allow/deny rule keyed on a tool-name prefix needs a case using the
  namespaced spelling. A whole hermetic suite passed while a `team_` allowance
  blocked every `mcp__chimera-team__team_*` call in production (#150/#151); only
  the live run caught it. Corollary, and the reason the rule exists: **a feature
  that runs external agents is not verified until a real model has run it.**
