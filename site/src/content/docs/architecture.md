---
title: "Chimera Architecture"
description: "Chimera Architecture"
---

Chimera is a layered framework designed to synthesize codebases from specifications using AI agents. The architecture is organized into eight distinct layers, each of which can be used independently or composed together for increasingly sophisticated workflows.

## Layer Stack

Chimera's architecture follows a modular, layered approach where each layer builds upon the lower layers and provides specific functionality:

```mermaid
graph TD
    subgraph Layer8["Layer 8: CLI"]
        direction LR
        CLI1["chimera synthesize"]
        CLI2["chimera eval"]
        CLI3["chimera bench"]
        CLI4["chimera code"]
        CLI5["chimera review"]
        CLI6["chimera ci-fix"]
        CLI7["chimera research"]
        CLI8["chimera docs"]
        CLI9["chimera testgen"]
        CLI10["chimera migrate"]
        CLI11["chimera plugins"]
        CLI1 --> CLI2
        CLI2 --> CLI3
        CLI3 --> CLI4
        CLI4 --> CLI5
        CLI5 --> CLI6
        CLI6 --> CLI7
        CLI7 --> CLI8
        CLI8 --> CLI9
        CLI9 --> CLI10
        CLI10 --> CLI11
    end

    subgraph Layer7["Layer 7: Workflows"]
        direction LR
        CIFix["CIFixWorkflow"]
        Review["ReviewOrchestrator"]
        Research["Researcher"]
        Migration["MigrationPlanner"]
        DocGen["DocGenerator"]
        TestGen["TestGenerator"]
        CIFix --> Review
        Review --> Research
        Research --> Migration
        Migration --> DocGen
        DocGen --> TestGen
    end

    subgraph Layer6["Layer 6: Synthesis"]
        direction LR
        Trainer["Trainer"]
        Strategy["Strategy"]
        Spec["Spec"]
        Architecture["Architecture"]
        Constraint["Constraint"]
        Trainer --> Strategy
        Trainer --> Spec
        Trainer --> Architecture
        Trainer --> Constraint
    end

    subgraph Layer5["Layer 5: Evaluation"]
        direction LR
        Harness["Harness"]
        Metrics["Metrics"]
        Benchmarks["Benchmarks"]
        Harness --> Metrics
        Harness --> Benchmarks
    end

    subgraph Layer4["Layer 4: Agent"]
        direction LR
        Agent["Agent"]
        Tools["Tools"]
        Loops["Loops"]
        Prompt["Prompt"]
        Context["Context"]
        Critic["Critic"]
        ACP["ACP"]
        Agent --> Tools
        Agent --> Loops
        Agent --> Prompt
        Agent --> Context
        Agent --> Critic
        Agent --> ACP
    end

    subgraph Layer3["Layer 3: Provider"]
        direction LR
        Anthropic["Anthropic<br/>Claude"]
        OpenAI["OpenAI<br/>GPT"]
        Google["Google<br/>Gemini"]
        Ollama["Ollama"]
        Modal["Modal"]
        Compatible["OpenAI-<br/>compatible"]
        Anthropic -.-> OpenAI
        OpenAI -.-> Google
        Google -.-> Ollama
        Ollama -.-> Modal
        Modal -.-> Compatible
    end

    subgraph Layer2["Layer 2: Infrastructure"]
        direction LR
        Security["Security"]
        Secrets["Secrets"]
        Permissions["Permissions"]
        Events["Events"]
        Sessions["Sessions"]
        Compaction["Compaction"]
        Streaming["Streaming"]
        Detection["Detection"]
        Config["Config"]
        PluginsInfra["Plugins"]
        MCPInfra["MCP"]
        LSPInfra["LSP"]
        Security --> Secrets
        Secrets --> Permissions
        Permissions --> Events
        Events --> Sessions
        Sessions --> Compaction
        Compaction --> Streaming
        Streaming --> Detection
        Detection --> Config
        Config --> PluginsInfra
        PluginsInfra --> MCPInfra
        MCPInfra --> LSPInfra
    end

    subgraph Layer1["Layer 1: Environment"]
        direction LR
        Local["Local"]
        Docker["Docker"]
        Git["Git"]
        Remote["Remote"]
        Cloud["Cloud"]
        Shell["Persistent<br/>Shell"]
        Local --> Docker
        Docker --> Git
        Git --> Remote
        Remote --> Cloud
        Cloud --> Shell
    end

    Layer8 --> Layer7
    Layer7 --> Layer6
    Layer6 --> Layer5
    Layer5 --> Layer4
    Layer4 --> Layer3
    Layer3 --> Layer2
    Layer2 --> Layer1
```

### Layer Descriptions

**Layer 1: Environment** — Execution contexts for running code and tests. Supports local filesystem operations, Docker containers, Git-based checkpointing, remote HTTP environments, managed cloud sandboxes, and persistent tmux-based shell sessions that maintain state across agent steps.

**Layer 2: Infrastructure** — Cross-cutting concerns wired through LoopConfig. Security analyzers evaluate tool call risk. Secrets detects and redacts sensitive data. Permissions gates tool execution. Events provides pub/sub. Sessions persists conversations. Compaction manages context window. Streaming handles real-time output. Detection catches infinite loops. Config provides polymorphic serialization. Plugins manages extensions. MCP connects to external tool servers. LSP provides language server integration.

**Layer 3: Provider** — LLM backends abstracted behind a common interface. Supports Anthropic Claude, OpenAI GPT, Google Gemini, Ollama, Modal, and any OpenAI-compatible endpoint (OpenRouter, vLLM, Groq, GLM-5). Includes per-model cost tracking with granular token breakdowns (cache, reasoning, per-step).

**Layer 4: Agent** — The agent loop system combining Provider, Tools, Loop strategies, Prompt templates, Context management, Critic evaluation, and ACP (Agent Client Protocol) for connecting external agents. The ReAct loop (default) implements Reason → Act → Observe cycles with tool use. Alternative loops include PlanAndExecute, Reflexion, and TreeOfThought.

**Layer 5: Evaluation** — Benchmarking and metrics for assessing synthesis quality. Includes harness abstraction, metric calculators (pass@k, resolve rate, cost), and adapters for SWE-bench, HumanEval, AIMO, and custom benchmarks.

**Layer 6: Synthesis** — The training system for code synthesis. Trainer orchestrates Architecture (layer dependencies), Spec (requirements), Strategies (synthesis algorithms), and Constraints (success criteria).

**Layer 7: Workflows** — Thin glue composing Agent.run() with domain-specific parsing and prompting. CIFixWorkflow parses CI logs and retries fixes. ReviewOrchestrator runs reviewer and author agent loop. Researcher does plan decomposition and synthesis. MigrationPlanner applies rule-based transforms with presets (python2-to-3, commonjs-to-esm). DocGenerator scans AST for documentation generation. TestGenerator creates test skeletons from source analysis.

**Layer 8: CLI** — Command-line interface exposing 11 subcommands: synthesize, eval, bench, code (interactive REPL), review, ci-fix, research, docs, testgen, migrate, and plugins. The `code` subcommand provides an interactive REPL with 16 slash commands for agent interaction.

### Using Each Layer

The layers are designed to be composable:

- **Layers 1-4**: Use as an agent toolkit for building agentic applications
- **Layers 1-6**: Use as a full synthesis framework for generating code from specs
- **Layers 7-8**: Use the workflows and CLI for terminal-based synthesis, evaluation, benchmarking, CI fixing, code review, research, migration, documentation, and test generation

For example, you can build a simple agent using only Layers 1-4, or a complete code generation pipeline using all six lower layers before adding workflows and the CLI.

---

## Agent Loop Flow

The ReAct (Reason → Act → Observe) loop is the core execution engine for agents in Chimera. It iterates between asking the LLM provider for the next action and executing tool calls until the agent produces a final response.

```mermaid
flowchart TD
    Start["User Task"] --> Complete["Provider.complete()"]
    Complete --> HasTools{Has Tool Calls?}

    HasTools -->|No| Return["Return AgentResult"]

    HasTools -->|Yes| Permission{Permission Check<br/>via LoopConfig}

    Permission -->|Deny| SkipTool["Skip Tool Execution"]
    SkipTool --> LoopDetect

    Permission -->|Allow| Execute["Execute Tool Calls"]
    Execute --> Emit["Emit Tool Events"]
    Emit --> Results["Add Results to Context"]
    Results --> LoopDetect

    LoopDetect{Loop Detection<br/>via LoopConfig}

    LoopDetect -->|Detected| Break["Break Loop"]
    Break --> ReturnFail["Return with Loop Flag"]

    LoopDetect -->|OK| HasSteps{Steps < max_steps?}
    HasSteps -->|Yes| Complete
    HasSteps -->|No| StepLimit["Reached Step Limit"]
    StepLimit --> ReturnFail

    Return --> End["Done"]
    ReturnFail --> End

    style Start fill:#e1f5ff
    style End fill:#c8e6c9
    style Complete fill:#fff9c4
    style Execute fill:#ffe0b2
    style Break fill:#ffccbc
```

### Loop Mechanics

**Provider.complete()** — Queries the LLM with the current conversation context and tool schemas. Returns the next response with optional tool calls.

**Permission Check** — Uses the `ApprovalPolicy` from `LoopConfig` to decide whether to execute a tool call. Policies include:
- `AutoApprove`: execute all tools
- `AlwaysDeny`: reject all tools
- `AllowList`: execute only whitelisted tools
- Custom policies for fine-grained control

**Tool Execution** — Runs the requested tool (file read/write, bash, test, git, etc.) and captures the output.

**Event Emission** — Publishes `ToolCallEvent` and `ToolResultEvent` to the event bus for logging and observability.

**Loop Detection** — Detects infinite loops by maintaining a sliding window of recent action signatures. If the agent repeats the same sequence, execution breaks.

**Context Management** — Tool results are added back to the conversation context, allowing the LLM to reason about the output in the next iteration.

---

## Module Dependency Map

Chimera's modules are organized around `LoopConfig`, which acts as a central hub for configurable loop behaviors, and `Agent`, which integrates tools and extensions.

```mermaid
graph LR
    LoopConfig["LoopConfig<br/>(central hub)"]

    Events["Events<br/>EventBus, Event types"]
    Permissions["Permissions<br/>ApprovalPolicy variants"]
    Detection["Detection<br/>LoopDetector, Signals"]
    Compaction["Compaction<br/>CompactionStrategy"]
    Streaming["Streaming<br/>StreamHandler"]
    AuditLog["AuditLog<br/>AuditEntry, summary"]
    CheckpointMgr["CheckpointManager<br/>create, restore, undo"]
    GitWorkflow["GitWorkflow<br/>branch isolation, diffs"]
    SecurityMod["Security<br/>SecurityAnalyzer,<br/>ConfirmationPolicy"]
    CriticMod["Critic<br/>CriticMixin,<br/>LLMCritic"]

    Sessions["Sessions<br/>SessionMixin, storage"]
    Auth["Auth<br/>ProviderAuth, Credentials"]
    AgentConfig["AgentConfig<br/>Agent configuration"]

    Agent["Agent<br/>Core agent loop"]

    Secrets["Secrets<br/>SecretDetector,<br/>RedactionMiddleware"]
    Plugins["Plugins<br/>PluginExtensionRegistry"]
    ACPMod["ACP<br/>ExternalAgentTool"]
    MCPMod["MCP<br/>MCPToolSource"]
    LSPMod["LSP<br/>LSPTool"]
    CostTracker["CostTracker<br/>Token tracking"]

    LoopConfig --> Events
    LoopConfig --> Permissions
    LoopConfig --> Detection
    LoopConfig --> Compaction
    LoopConfig --> Streaming
    LoopConfig --> AuditLog
    LoopConfig --> CheckpointMgr
    LoopConfig --> GitWorkflow
    LoopConfig --> SecurityMod
    LoopConfig --> CriticMod

    Secrets --> Events

    Sessions --> LoopConfig
    Sessions --> Agent

    Auth --> Providers["Providers<br/>Anthropic, OpenAI, etc."]
    CostTracker --> Providers

    AgentConfig --> Agent
    AgentConfig --> LoopConfig

    Plugins --> Agent
    ACPMod --> Agent
    MCPMod --> Agent
    LSPMod --> Agent

    CheckpointMgr --> Environments["Environments<br/>Local, Docker, Git,<br/>Remote, Cloud"]

    style LoopConfig fill:#fff9c4,stroke:#f57f17,stroke-width:3px
    style Agent fill:#f0f4c3,stroke:#558b2f,stroke-width:2px
    style Events fill:#e8f5e9
    style Permissions fill:#e8f5e9
    style Detection fill:#e8f5e9
    style Compaction fill:#e8f5e9
    style Streaming fill:#e8f5e9
    style AuditLog fill:#e8f5e9
    style CheckpointMgr fill:#e8f5e9
    style GitWorkflow fill:#e8f5e9
    style SecurityMod fill:#e8f5e9
    style CriticMod fill:#e8f5e9
    style Secrets fill:#e1f5fe
    style Plugins fill:#e1f5fe
    style ACPMod fill:#e1f5fe
    style MCPMod fill:#e1f5fe
    style LSPMod fill:#e1f5fe
    style CostTracker fill:#e1f5fe
```

### Module Roles

**LoopConfig** — Central configuration object that plugs extensions into the ReAct loop. Holds references to event bus, approval policy, loop detector, context compressor, stream handler, audit log, checkpoint manager, git workflow, security analyzer, confirmation policy, and critic.

**Events** — Publish-subscribe system for observing agent execution (step start, tool calls, tool results, errors, security events, critic events, cost events). Decouples logging/monitoring from the core loop.

**Permissions** — Approval policies that gate tool execution. Enables human-in-the-loop, safety constraints, and debugging workflows.

**Detection** — Loop detection algorithms to prevent infinite cycles. Maintains signatures of recent actions and breaks on repetition.

**Compaction** — Context compression strategies (Summary, Prune, Counter, Composite) to manage token usage in long conversations. Supports SOFT/HARD thresholds with tool call/result atomicity.

**Streaming** — Streaming handlers for real-time output observation. Allows printing to stdout or collecting results without blocking.

**AuditLog** — Records all tool executions with timestamps, risk levels, and outcomes. Provides summary and per-tool filtering.

**CheckpointManager** — Creates and restores named checkpoints in the environment. Supports undo and listing operations.

**GitWorkflow** — Branch isolation, diff context injection, and commit strategies for agent-driven code changes.

**Security** — SecurityAnalyzer (LLM-based, rule-based, or composite) evaluates tool call risk. ConfirmationPolicy (NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold) determines when to prompt for approval.

**Critic** — CriticMixin integrates LLMCritic or ChecklistCritic into the loop for iterative refinement. Operates in all_actions or finish_only mode.

**Secrets** — SecretDetector identifies sensitive data (API keys, AWS credentials, bearer tokens, private keys) and RedactionMiddleware scrubs it from the event bus.

**Sessions** — Persistent conversation sessions with save/resume/fork. Supports Memory, File, and SQLite storage backends plus event-sourced persistence.

**Auth** — Provider authentication and credential management. Supports environment variable loading, token refresh, and multi-provider auth.

**AgentConfig** — High-level agent configuration builder. Wraps Agent, LoopConfig, and related settings into a single configuration object with markdown-based presets.

**Plugins** — PluginExtensionRegistry registers tools, agents, strategies, constraints, middleware, skills, MCP servers, and hooks. Supports directory-based loading and a marketplace for discovery/install.

**ACP** — Agent Client Protocol for connecting external agents. ExternalAgentTool wraps external agents as Chimera tools via JSON-RPC 2.0 over subprocess stdio.

**MCP** — MCPToolSource connects to external tool servers (stdio or HTTP) and surfaces their tools as native Chimera tools.

**LSP** — Language Server Protocol client providing diagnostics, completion, and rename capabilities as agent tools.

**CostTracker** — Granular token tracking with cache-aware accounting, reasoning token breakdowns, and per-step cost calculation.

---

## Training Pipeline

The synthesis process (Layer 6) orchestrates agents with specifications and constraints to generate code that passes tests. The pipeline iterates until constraints are satisfied.

```mermaid
flowchart LR
    Spec["Spec<br/>(requirements)"]
    Strategy["Strategy<br/>(algorithm)"]

    Spec --> Synthesize["Trainer.synthesize()"]
    Strategy --> Synthesize

    Synthesize --> AgentRun["Agent.run()<br/>(ReAct loop)"]

    AgentRun --> Env["Environment<br/>(execute + test)"]

    Env --> TestPass{Tests Pass?<br/>+ Constraints<br/>Satisfied?}

    TestPass -->|No| Iterate["Iterate<br/>(next epoch)"]
    Iterate --> AgentRun

    TestPass -->|Yes| Return["Return SynthesisResult<br/>(code + metadata)"]

    Return --> End["Done"]

    style Spec fill:#e3f2fd
    style Strategy fill:#e3f2fd
    style Synthesize fill:#fff9c4
    style AgentRun fill:#ffe0b2
    style Env fill:#ffe0b2
    style TestPass fill:#ffccbc
    style Iterate fill:#ffccbc
    style Return fill:#c8e6c9
    style End fill:#a5d6a7
```

### Pipeline Stages

**Spec + Strategy** — The specification defines what code should do (from tests, documentation, or examples). The strategy determines how to synthesize (TestConvergence, TreeSearch, Curriculum, Ensemble, etc.).

**Trainer.synthesize()** — Main entry point. Orchestrates the strategy with the agent, environment, and constraints.

**Agent.run()** — Executes the ReAct loop with the current codebase and specification. The agent reads files, thinks about changes, and writes code.

**Environment (execute + test)** — Runs tests to validate the generated code. LocalEnvironment, DockerEnvironment, and GitEnvironment all support test execution.

**Tests Pass?** — Check if all test constraints are satisfied. If not, the agent iterates with feedback about what failed.

**Iterate** — If tests fail, loop back with refined prompts, checkpoints, or branch exploration (for tree search).

**Return SynthesisResult** — When constraints are satisfied, return the generated code, metrics, and synthesis history.

### Strategy Variants

Chimera includes multiple synthesis strategies:

- **TestConvergence** (default) — Iterate until all tests pass
- **TreeSearch** — Explore multiple branches in parallel, prune low-scoring paths
- **Curriculum** — Synthesize layers in topological order (dependencies first)
- **Ensemble** — Run multiple agents in parallel, pick the best result
- **MajorityVoting** — Combine multiple attempts via consensus
- **AIMOEnsemble** — Voting with tree search fallback for harder problems
- **Passthrough** — Single-shot synthesis (no iteration)

---

## Extension Architecture

Chimera supports custom extensions through well-defined interfaces:

- **Custom Tools** — Subclass `BaseTool` to add new agent capabilities
- **Custom Loops** — Subclass `Loop` base class for new reasoning strategies (e.g., multi-agent loops)
- **Custom Strategies** — Subclass `Strategy` for new synthesis algorithms
- **Custom Constraints** — Implement constraint functions for custom success criteria
- **Custom Environments** — Subclass `Environment` to support new execution contexts (e.g., Kubernetes, cloud functions)
- **Custom Providers** — Subclass `Provider` to integrate new LLM backends
- **Custom Security Analyzers** — Subclass `SecurityAnalyzer` to implement custom risk evaluation logic
- **Custom Critics** — Subclass `Critic` to create custom evaluation and refinement strategies
- **Custom Compaction** — Subclass `CompactionStrategy` for custom context window management
- **Custom Confirmation Policies** — Subclass `ConfirmationPolicy` for custom approval workflows
- **Custom Plugins** — Subclass `BasePlugin` to bundle tools, agents, strategies, and hooks into distributable packages

All extensions integrate seamlessly via the public API exports in `chimera/__init__.py`.
