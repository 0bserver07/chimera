# Changelog

## 0.1.0 (2026-03-20) — Initial Release

### Core Framework (Phases 1-21)
- 8-layer architecture: CLI → Workflows → Synthesis → Eval → Agent → Provider → Infrastructure → Environment
- 20 built-in tools (read, write, edit, bash, search, git, web_search, browser, etc.)
- 6 providers (Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compatible)
- 6 environments (Local, Docker, Git, Remote, Cloud, PersistentShell)
- 10 strategies (TestConvergence, TreeSearch, CEGIS, Incremental, MajorityVoting, etc.)
- `chimera.synthesize()` one-liner for code generation
- `chimera code` interactive REPL with 16 slash commands
- 11 CLI subcommands (synthesize, eval, bench, code, review, ci-fix, research, docs, testgen, migrate, plugins)

### Agent Replication (Phases 22-39)
- 8 major agent architectures replicable: SWE-Agent, Aider, Cline, Codex CLI, OpenHands, Gemini CLI, OpenCode, Kimi CLI
- AgentPreset system (SWE_AGENT, AIDER, CLINE, CODEX) — one-liner agent creation
- AutonomousLoop for long-running tasks
- RoleBasedTeam for multi-agent collaboration (planner → coder → reviewer → tester)
- 13 coding agent primitives: RetryLoop, PlanActLoop, LintFeedbackLoop, FocusChain, HistoryProcessor, TreeSitter, DefinitionLookup, SandboxPolicy, LongTermMemory, InstructionLayer, DemonstrationPrompt, etc.

### Infrastructure (Phases 28-39)
- Prompt caching + extended thinking support
- Ghost commits (snapshot-based undo)
- Response caching (SHA-based dedup)
- OS-native sandboxing (macOS Seatbelt profile generation)
- File watcher (reactive re-run on external changes)
- Context condensation (smart compaction, thought stripping)
- Codebase indexing (TF-IDF + optional embeddings)
- Interactive approval UX
- Project config auto-discovery (CHIMERA.md / CLAUDE.md)
- Trajectory logging (JSON/JSONL)
- Diff proposal workflow (stage, accept/reject, apply)
- Commit message style inference

### Benchmarks
- HumanEval (full 164): 90.9% pass@1
- Terminal-Bench (10 tasks): 30%
- SWE-bench Lite (20 instances): 10%
- 13 benchmark issues filed with transparency framework

### Stats
- 2459 tests passing
- 340 public exports
- 39 runnable examples
- 114-page documentation site (Starlight)
