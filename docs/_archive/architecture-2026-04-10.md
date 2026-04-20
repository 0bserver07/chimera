# Chimera Architecture

Chimera is a layered framework for building tool-augmented LLM agents. The architecture is organized around **CodingAgent**, a single assembly point that wires together 8 phases into a fully functional coding agent.

## CodingAgent: The Assembly Point

`CodingAgent` (`chimera/assembly/coding_agent.py`) is the top-level entry point. It composes all 8 architectural phases into a working agent in a single constructor call:

```python
agent = CodingAgent(model="claude-sonnet-4-20250514")
async for event in agent.run("Fix the bug in auth.py"):
    print(event)
```

Presets (`claude_code`, `codex`, `minimal`, `explore`) control which phases are activated and how they are configured.

## The 8 Layers

```mermaid
graph TD
    L8["8. Production<br/>Feature flags, analytics, memory"]
    L7["7. Commands<br/>Slash commands, skills, SkillTool"]
    L6["6. Hooks<br/>Lifecycle events, file watchers"]
    L5["5. System Prompt<br/>Layered construction, context assembly"]
    L4["4. Permissions<br/>Rule-based tool gating"]
    L3["3. State<br/>Content replacement, file cache, transcripts"]
    L2["2. Sub-Agents<br/>AgentSpawner, context isolation"]
    L1["1. Core Loop<br/>AgentLoop, streaming, tool execution"]

    L8 --> L7 --> L6 --> L5 --> L4 --> L3 --> L2 --> L1

    style L1 fill:#e3f2fd
    style L2 fill:#e8f5e9
    style L3 fill:#fff9c4
    style L4 fill:#ffe0b2
    style L5 fill:#f3e5f5
    style L6 fill:#fce4ec
    style L7 fill:#e0f2f1
    style L8 fill:#f1f8e9
```

### Layer 1: Core Loop

The async-generator `AgentLoop` that alternates between LLM calls and tool execution. Handles streaming, error recovery, compaction, abort signals, and turn counting.

**Key modules:** `agent_loop.py`, `loop_state.py`, `loop_events.py`, `streaming_executor.py`, `recovery.py`, `abort.py`

### Layer 2: Sub-Agents

`AgentSpawner` creates isolated sub-agent instances from `AgentDefinition` descriptors. Each sub-agent gets its own `AgentContext` with scoped tools and conversation history.

**Key modules:** `agent_spawner.py`, `agent_context.py`, `agent_definition.py`, `builtin_agents.py`

### Layer 3: State

Tracks mutable state across the loop. `ContentReplacementState` decides when to persist large tool results to disk. `FileStateCache` provides LRU caching for file reads. `TranscriptStorage` records conversations to JSONL.

**Key modules:** `content_replacement.py`, `file_state_cache.py`, `transcript.py`, `resume.py`

### Layer 4: Permissions

Multi-source rule-based permission checking for tool calls. Rules are loaded from project-level and user-level settings files. Supports ALLOW, DENY, and ASK behaviors with denial tracking.

**Key modules:** `checker.py`, `context.py`, `rules.py`, `loader.py`, `modes.py`, `decisions.py`, `denial_tracking.py`

### Layer 5: System Prompt

Layered system prompt construction. `ContextAssembler` builds the prompt from project context (git status, directory listing, tool schemas). `SystemPromptBuilder` stacks cacheable and non-cacheable layers. Persistent memory is injected as a final layer.

**Key modules:** `context_assembler.py`, `system_prompt.py`, `system_prompts.py`

### Layer 6: Hooks

Lifecycle hook system that fires events at key points: `SESSION_START`, `PRE_TOOL_USE`, `POST_TOOL_USE`, `STOP`, `SESSION_END`, and more. Hooks can block tool execution, modify inputs, or prevent the agent from stopping.

**Key modules:** `executor.py`, `loader.py`, `events.py`, `types.py`, `emitter.py`, `file_watcher.py`

### Layer 7: Commands

Slash-command system for user input. `InputHandler` intercepts `/command` input before it reaches the model. `CommandRegistry` manages builtins, skills, and plugin commands. `SkillTool` lets the model invoke skills programmatically.

**Key modules:** `input_handler.py`, `processor.py`, `registry.py`, `builtins.py`, `skill_tool.py`

### Layer 8: Production

Cross-cutting production concerns. `FeatureFlags` gates experimental features via environment variables. `AnalyticsManager` routes events to pluggable sinks. `PersistentMemory` stores cross-session facts in a Markdown file.

**Key modules:** `feature_flags.py`, `manager.py`, `sinks.py`, `memory.py`

## Module Map

```mermaid
graph LR
    subgraph assembly["chimera/assembly/"]
        CA["coding_agent.py"]
        PR["presets.py"]
        TS["tool_sets.py"]
        SP["system_prompts.py"]
    end

    subgraph core["chimera/core/"]
        AL["agent_loop.py"]
        LS["loop_state.py"]
        LE["loop_events.py"]
        SE["streaming_executor.py"]
        AS["agent_spawner.py"]
        CR["content_replacement.py"]
        SYS["system_prompt.py"]
        CTX["context_assembler.py"]
        FF["feature_flags.py"]
        MEM["memory.py"]
    end

    subgraph permissions["chimera/permissions/"]
        PC["checker.py"]
        PX["context.py"]
        RL["rules.py"]
        PL["loader.py"]
    end

    subgraph hooks["chimera/hooks/"]
        HE["executor.py"]
        HL["loader.py"]
        HV["events.py"]
        HT["types.py"]
    end

    subgraph commands["chimera/commands/"]
        IH["input_handler.py"]
        CP["processor.py"]
        RG["registry.py"]
    end

    CA --> AL
    CA --> AS
    CA --> CR
    CA --> PC
    CA --> CTX
    CA --> HE
    CA --> IH
    CA --> FF

    style assembly fill:#e8eaf6
    style core fill:#e3f2fd
    style permissions fill:#ffe0b2
    style hooks fill:#fce4ec
    style commands fill:#e0f2f1
```

## Data Flow

The path from user input to agent response:

```mermaid
flowchart TD
    Input["User Input"] --> SlashCheck{"Starts with /?"}

    SlashCheck -->|Yes| CmdProc["SlashCommandProcessor<br/>Execute command"]
    CmdProc --> CmdResult["Return system event"]

    SlashCheck -->|No| Assemble["ContextAssembler<br/>Build system prompt"]
    Assemble --> Memory["Inject PersistentMemory"]
    Memory --> Loop["AgentLoop.run()"]

    Loop --> Provider["Provider.async_complete()<br/>or async_stream()"]
    Provider --> HasTools{Tool calls?}

    HasTools -->|No| StopHook{"STOP hook<br/>allows?"}
    StopHook -->|Yes| Done["Yield result event"]
    StopHook -->|No| Inject["Inject reason as<br/>user message"] --> Loop

    HasTools -->|Yes| PermCheck{"Permission<br/>check"}
    PermCheck -->|Deny| DenyResult["Return denial<br/>as tool result"]
    PermCheck -->|Allow| PreHook["PRE_TOOL_USE hook"]

    PreHook -->|Blocked| BlockResult["Return blocked<br/>as tool result"]
    PreHook -->|OK| Execute["StreamingToolExecutor<br/>Run tools concurrently"]

    Execute --> PostHook["POST_TOOL_USE hook"]
    PostHook --> Append["Append results<br/>to conversation"]

    DenyResult --> Append
    BlockResult --> Append
    Append --> Loop

    style Input fill:#e1f5ff
    style Done fill:#c8e6c9
    style Loop fill:#fff9c4
    style Execute fill:#ffe0b2
```

### Step-by-step

1. **Slash check** -- `InputHandler` inspects user input. If it starts with `/`, route to `SlashCommandProcessor` and return without calling the model.
2. **System prompt assembly** -- `ContextAssembler` gathers project context (git status, directory structure, tool schemas, user-supplied prompt text). `PersistentMemory` content is appended as a non-cacheable layer.
3. **AgentLoop** -- The core async-generator loop. Each iteration calls the provider, checks for tool calls, and either completes or continues.
4. **Provider call** -- Sends the full message history (system prompt + conversation) to the LLM. Supports both streaming (`async_stream`) and non-streaming (`async_complete`) modes.
5. **Permission check** -- If a `PermissionChecker` is configured, each tool call is checked against loaded rules. DENY returns an error result without executing. ASK requires interactive approval (or defaults to denial in non-interactive mode).
6. **Hook gates** -- `PRE_TOOL_USE` hooks can block execution or modify tool arguments. `POST_TOOL_USE` hooks observe results. `STOP` hooks can prevent the agent from finishing.
7. **Tool execution** -- `StreamingToolExecutor` runs non-blocked tool calls concurrently with configurable parallelism.
8. **Context update** -- Tool results are appended to the conversation as tool-result messages, then the loop iterates.
