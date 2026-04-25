# Module Index

All 78 source modules in the architecture, organized by package.

## chimera/assembly/ -- Assembly Layer

| Module | Description |
|--------|-------------|
| `coding_agent.py` | `CodingAgent` -- one-liner entry point, wires all 8 phases |
| `presets.py` | `AssemblyConfig` and 4 preset configurations (`claude_code`, `codex`, `minimal`, `explore`) |
| `tool_sets.py` | Named tool collections (`coding_tools`, `minimal_tools`, `explore_tools`) |
| `system_prompts.py` | Canned system prompts for each preset |

## chimera/core/ -- Core Engine

| Module | Description |
|--------|-------------|
| `agent_loop.py` | `AgentLoop` -- core async-generator loop driving tool-augmented LLM agents |
| `loop_state.py` | `LoopState` -- per-turn bookkeeping, retry policies, query source tracking |
| `loop_events.py` | `LoopEvent` and `LoopEventType` -- event types yielded by the loop |
| `loop.py` | Legacy loop with `Context` integration and incremental tool execution |
| `loop_deps.py` | `LoopDeps` -- dependency injection container for the agent loop |
| `streaming_executor.py` | `StreamingToolExecutor` -- concurrent tool execution with concurrency control |
| `abort.py` | `AbortSignal` -- one-shot abort primitive with child propagation |
| `recovery.py` | `ErrorRecovery` -- retry and recovery system for provider errors |
| `agent.py` | Core agent module tying together providers, tools, loops, and prompts |
| `agent_spawner.py` | `AgentSpawner` -- create and run sub-agent instances from definitions |
| `agent_context.py` | `AgentContext` -- isolated execution context for sub-agents |
| `agent_definition.py` | `AgentDefinition` -- declarative agent configuration loaded from YAML/JSON |
| `builtin_agents.py` | Built-in agent definitions shipped with chimera |
| `content_replacement.py` | `ContentReplacementState` -- persist large tool results to disk |
| `file_state_cache.py` | `FileStateCache` -- LRU cache for file contents keyed by (path, offset, limit) |
| `system_prompt.py` | `SystemPrompt` and `SystemPromptBuilder` -- cacheable layered prompt construction |
| `context_assembler.py` | `ContextAssembler` -- builds system prompt from project context |
| `compaction_integration.py` | `CompactionIntegration` -- bridges agent loop and context compaction |
| `snip_compact.py` | Conversation compaction utilities -- snip old tool results and truncate |
| `token_budget.py` | Token budget enforcement for agent loops |
| `token_estimator.py` | Token counting and estimation utilities |
| `tool.py` | `BaseTool` -- tool abstraction for giving agents action capabilities |
| `tool_deferral.py` | Tool deferral management -- eager vs. deferred tool loading |
| `tool_pool.py` | `ToolPool` -- tool collection with deferred loading support |
| `tool_result_persister.py` | Persist large tool results to disk and retrieve them later |
| `cache_safe_params.py` | Cache-safe parameter tracking for LLM calls |
| `feature_flags.py` | `FeatureFlags` -- gate experimental and production features via env vars |
| `memory.py` | `PersistentMemory` -- cross-session memory backed by a Markdown file |
| `model_fallback.py` | Fallback model switching on rate limits or overload |
| `auto_background.py` | `AutoBackgroundMonitor` -- decide when to move long-running tasks to background |
| `task_manager.py` | `TaskManager` -- lifecycle management for background agent tasks |
| `uuid_chain.py` | Chain of UUIDs linking consecutive messages in a transcript |

## chimera/permissions/ -- Permission System

| Module | Description |
|--------|-------------|
| `checker.py` | `PermissionChecker` -- central permission checker implementing the step-by-step algorithm |
| `context.py` | `PermissionContext` -- immutable snapshot of permission state passed to the checker |
| `rules.py` | Permission rule primitives -- sources, behaviors (`ALLOW`/`DENY`/`ASK`), and rule values |
| `decisions.py` | `PermissionDecision` -- types returned by the checker |
| `modes.py` | `PermissionMode` -- overall approval behaviour modes |
| `loader.py` | `PermissionRuleLoader` -- load rules from on-disk settings files |
| `denial_tracking.py` | Track repeated denials to auto-deny after a threshold |
| `interactive.py` | Interactive approval UX for tool execution |
| `prompt_handler.py` | Interactive permission prompt handler with pluggable callback |
| `sandbox.py` | Sandbox adapter for controlled command execution and path filtering |

## chimera/hooks/ -- Hook System

| Module | Description |
|--------|-------------|
| `executor.py` | `HookExecutor` -- run hooks and merge their results |
| `loader.py` | `HookLoader` -- load and merge hooks from settings files and session |
| `events.py` | `HookEvent` enum -- all lifecycle events the hook system can fire |
| `types.py` | `HookInput`, `HookOutput`, and hook descriptors |
| `emitter.py` | Centralized hook emission helper |
| `async_registry.py` | `AsyncHookRegistry` -- track and poll async hook tasks |
| `file_watcher.py` | `FileWatcher` -- detect file and cwd changes, emit hook events |
| `session_hooks.py` | `SessionHookManager` -- runtime hook registration for a single session |

## chimera/commands/ -- Command System

| Module | Description |
|--------|-------------|
| `input_handler.py` | `InputHandler` -- detect slash commands or pass input to model |
| `processor.py` | `SlashCommandProcessor` -- parse and dispatch slash commands |
| `registry.py` | `CommandRegistry` -- central registry for builtins, skills, and plugins |
| `builtins.py` | Built-in slash commands shipped with chimera |
| `types.py` | Command type definitions (`CommandDef`, `CommandResult`) |

## chimera/tools/ -- Agent Tools

| Module | Description |
|--------|-------------|
| `agent_tool.py` | `AgentTool` -- launch a sub-agent for complex tasks |
| `cached_read.py` | `CachedReadTool` -- `ReadFileTool` with `FileStateCache` integration |
| `skill_tool.py` | `SkillTool` -- lets the model invoke a skill or slash command by name |
| `task_tools.py` | `TaskOutputTool`, `TaskStopTool`, `TaskListTool` -- background task management |
| `tool_search.py` | `ToolSearchTool` -- discover available tools at runtime |

## chimera/sessions/ -- Session Management

| Module | Description |
|--------|-------------|
| `transcript.py` | `TranscriptStorage` -- JSONL-based transcript for main and subagent conversations |
| `resume.py` | Resume a session from its persisted transcript |

## chimera/skills/ -- Skill System

| Module | Description |
|--------|-------------|
| `definition.py` | `SkillDefinition` -- skill definition and prompt expansion |
| `loader.py` | `SkillLoader` -- load skill definitions from `.chimera/skills/*.md` files |
| `bundled.py` | Registry for skills bundled directly in code (not loaded from disk) |

## chimera/analytics/ -- Analytics

| Module | Description |
|--------|-------------|
| `manager.py` | `AnalyticsManager` -- analytics event manager with pluggable sinks |
| `sinks.py` | Built-in analytics sinks: file, stdout, and in-memory |

## chimera/bridge/ -- Bridge Protocol

| Module | Description |
|--------|-------------|
| `protocol.py` | Bridge protocol for inter-process agent communication |
| `repl_bridge.py` | REPL bridge for connecting a REPL-style interface to an agent |
| `transports.py` | Built-in bridge transports (stdio, WebSocket) |

## chimera/coordinator/ -- Coordinator

| Module | Description |
|--------|-------------|
| `mode.py` | `CoordinatorMode` -- multi-agent task dispatch and orchestration |

## chimera/mcp/ -- Model Context Protocol

| Module | Description |
|--------|-------------|
| `lifecycle.py` | `MCPServerLifecycle` -- manage MCP server connections with memoization and lifecycle control |

## chimera/plugins/ -- Plugin System

| Module | Description |
|--------|-------------|
| `base.py` | Plugin base classes, registry, and extension dataclasses |
| `manager.py` | `PluginManager` -- discover, load, and unload plugins |
