# Production Features Design

Make all 15 features production-real by composing existing Chimera primitives.
Each feature proves the thesis: complex agents are just thin wiring over Agent + Provider + Tools.

## Tier 1: Wire LLM (3 features)

### #10 CI Fix Agent
Add `CIFixWorkflow.run(log, agent, env)` — parse failures, build prompt, call `agent.run()`, retry loop.

### #11 Code Review
Add `ReviewOrchestrator.run(diff, reviewer, author, env)` — reviewer Agent reviews, author Agent fixes, iterate until approved or max rounds.

### #15 Research Agent
Add `Researcher.run(question, agent, env)` — decompose question into plan, build prompt with search terms, call `agent.run()`.

## Tier 2: Register & Wire (10 features)

### #2 Git Workflow
Add `git_workflow: GitWorkflow | None` to LoopConfig. Auto-branch on Agent.run() start, auto-commit on finish.

### #3 Import Graph
Already adequate. Add as optional tool `import_graph` in tools/__init__.py.

### #4 Image Support
Add ImageReadTool to DEFAULT_TOOLS. Provider.complete() already handles content blocks.

### #5 Permission UX
Add `audit_log: AuditLog | None` to LoopConfig. Hook into tool_executor to record decisions. Add `/audit` REPL command.

### #6 Checkpoints
Add `checkpoint_manager: CheckpointManager | None` to LoopConfig. Auto-checkpoint after successful steps. Add `/checkpoint` REPL command.

### #7 MCP Config
Load `~/.chimera/mcp.json` in CLI startup via `MCPToolSource.from_config()`. Best-effort.

### #8 Agent Loader
Load custom agents from `~/.chimera/agents/` in CLI startup. Add `/agent` REPL command.

### #9 SWE-bench
Already wired to `chimera eval --benchmark swe-bench`. No changes needed.

### #12 Auto-docs
Add CLI subcommand `chimera docs <source>`. Wrap DocGenerator.

### #13 Test Generation
Add CLI subcommand `chimera testgen <source>`. Wrap TestGenerator.

### #14 Migration
Already adequate. Add CLI subcommand `chimera migrate <source> --preset python2-to-3`.

### #16 Marketplace
Add CLI subcommand `chimera plugins search|install|uninstall`. Wire to PluginManager.

## Tier 3: CLI Entry Points (new subcommands)

Add to `cli/main.py`: `review`, `ci-fix`, `research`, `docs`, `testgen`, `migrate`, `plugins`.
Add to `cli/code.py` REPL: `/audit`, `/checkpoint`, `/agent`.

## Implementation Groups (for parallel agents)

1. **LLM wiring** — CI fix, code review, research (modify 3 files)
2. **LoopConfig + tool_executor** — audit_log, checkpoint_manager, git_workflow fields + hooks (modify 3 files)
3. **DEFAULT_TOOLS + tool registration** — ImageReadTool, ImportGraph export (modify 2 files)
4. **CLI subcommands** — review, ci-fix, research, docs, testgen, migrate, plugins (modify 1 file)
5. **REPL slash commands** — /audit, /checkpoint, /agent (modify 1 file)
6. **Tests** — integration tests for all wiring (create ~6 test files)
