# Chimera Architecture

Chimera is a layered framework designed to synthesize codebases from specifications using AI agents. The architecture is organized into six distinct layers, each of which can be used independently or composed together for increasingly sophisticated workflows.

## Layer Stack

Chimera's architecture follows a modular, layered approach where each layer builds upon the lower layers and provides specific functionality:

```mermaid
graph TD
    subgraph Layer6["Layer 6: CLI"]
        direction LR
        CLI1["chimera synthesize"]
        CLI2["chimera eval"]
        CLI3["chimera bench"]
        CLI1 --> CLI2
        CLI2 --> CLI3
    end

    subgraph Layer5["Layer 5: Synthesis"]
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

    subgraph Layer4["Layer 4: Evaluation"]
        direction LR
        Harness["Harness"]
        Metrics["Metrics"]
        Benchmarks["Benchmarks"]
        Harness --> Metrics
        Harness --> Benchmarks
    end

    subgraph Layer3["Layer 3: Agent"]
        direction LR
        Agent["Agent"]
        Tools["Tools"]
        Loops["Loops"]
        Prompt["Prompt"]
        Context["Context"]
        Agent --> Tools
        Agent --> Loops
        Agent --> Prompt
        Agent --> Context
    end

    subgraph Layer2["Layer 2: Provider"]
        direction LR
        Anthropic["Anthropic<br/>Claude"]
        OpenAI["OpenAI<br/>GPT"]
        Google["Google<br/>Gemini"]
        Ollama["Ollama"]
        Compatible["OpenAI-<br/>compatible"]
        Anthropic -.-> OpenAI
        OpenAI -.-> Google
        Google -.-> Ollama
        Ollama -.-> Compatible
    end

    subgraph Layer1["Layer 1: Environment"]
        direction LR
        Local["Local"]
        Docker["Docker"]
        Git["Git"]
        Shell["Persistent<br/>Shell"]
        Local --> Docker
        Docker --> Git
        Git --> Shell
    end

    Layer6 --> Layer5
    Layer5 --> Layer4
    Layer4 --> Layer3
    Layer3 --> Layer2
    Layer2 --> Layer1
```

### Layer Descriptions

**Layer 1: Environment** — Execution contexts for running code and tests. Supports local filesystem operations, Docker containers, Git-based checkpointing, and persistent tmux-based shell sessions that maintain state across agent steps.

**Layer 2: Provider** — LLM backends abstracted behind a common interface. Supports Anthropic Claude, OpenAI GPT, Google Gemini, Ollama, and any OpenAI-compatible endpoint (OpenRouter, vLLM, Groq, GLM-5).

**Layer 3: Agent** — The agent loop system combining Provider, Tools, Loop strategies, Prompt templates, and Context management. The ReAct loop (default) implements Reason → Act → Observe cycles with tool use.

**Layer 4: Evaluation** — Benchmarking and metrics for assessing synthesis quality. Includes harness abstraction, metric calculators (pass@k, resolve rate, cost), and adapters for SWE-bench, HumanEval, and custom benchmarks.

**Layer 5: Synthesis** — The training system for code synthesis. Trainer orchestrates Architecture (layer dependencies), Spec (requirements), Strategies (synthesis algorithms), and Constraints (success criteria).

**Layer 6: CLI** — Command-line interface exposing synthesize, eval, and bench operations to end users.

### Using Each Layer

The layers are designed to be composable:

- **Layers 1–3**: Use as an agent toolkit for building agentic applications
- **Layers 1–5**: Use as a full synthesis framework for generating code from specs
- **Layer 6**: Use the CLI for terminal-based synthesis, evaluation, and benchmarking

For example, you can build a simple agent using only Layers 1–3, or a complete code generation pipeline using all five lower layers before adding the CLI.

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

    Return --> End["✓ Synthesis/Task Complete"]
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

Chimera's extension modules (Phase 19+) are organized around `LoopConfig`, which acts as a central hub for configurable loop behaviors.

```mermaid
graph LR
    LoopConfig["LoopConfig<br/>(central hub)"]

    Events["Events<br/>EventBus, Event types"]
    Permissions["Permissions<br/>ApprovalPolicy variants"]
    Detection["Detection<br/>LoopDetector, Signals"]
    Compaction["Compaction<br/>ContextCompressor strategies"]
    Streaming["Streaming<br/>StreamHandler implementations"]

    Sessions["Sessions<br/>SessionMixin, tmux integration"]
    Auth["Auth<br/>ProviderAuth, Credentials"]
    AgentConfig["AgentConfig<br/>Agent configuration"]

    Agent["Agent<br/>Core agent loop"]

    LoopConfig --> Events
    LoopConfig --> Permissions
    LoopConfig --> Detection
    LoopConfig --> Compaction
    LoopConfig --> Streaming

    Sessions --> LoopConfig
    Sessions --> Agent

    Auth --> Providers["Providers<br/>Anthropic, OpenAI, etc."]

    AgentConfig --> Agent
    AgentConfig --> LoopConfig

    style LoopConfig fill:#fff9c4,stroke:#f57f17,stroke-width:3px
    style Agent fill:#f0f4c3,stroke:#558b2f,stroke-width:2px
    style Events fill:#e8f5e9
    style Permissions fill:#e8f5e9
    style Detection fill:#e8f5e9
    style Compaction fill:#e8f5e9
    style Streaming fill:#e8f5e9
```

### Module Roles

**LoopConfig** — Central configuration object that plugs extensions into the ReAct loop. Holds references to event bus, approval policy, loop detector, context compressor, and stream handler.

**Events** — Publish-subscribe system for observing agent execution (step start, tool calls, tool results, errors). Decouples logging/monitoring from the core loop.

**Permissions** — Approval policies that gate tool execution. Enables human-in-the-loop, safety constraints, and debugging workflows.

**Detection** — Loop detection algorithms to prevent infinite cycles. Maintains signatures of recent actions and breaks on repetition.

**Compaction** — Context compression strategies (keep-first, keep-last, summarize) to manage token usage in long conversations.

**Streaming** — Streaming handlers for real-time output observation. Allows printing to stdout or collecting results without blocking.

**Sessions** — Persistent shell sessions via SessionMixin. Enables stateful command execution across agent steps using tmux.

**Auth** — Provider authentication and credential management. Supports environment variable loading, token refresh, and multi-provider auth.

**AgentConfig** — High-level agent configuration builder. Wraps Agent, LoopConfig, and related settings into a single configuration object.

---

## Training Pipeline

The synthesis process (Layers 5) orchestrates agents with specifications and constraints to generate code that passes tests. The pipeline iterates until constraints are satisfied.

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

    Return --> End["✓ Done"]

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

All extensions integrate seamlessly via the public API exports in `chimera/__init__.py`.
