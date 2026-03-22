# Changelog

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
