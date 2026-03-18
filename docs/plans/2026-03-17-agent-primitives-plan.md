# Agent Primitives Plan: Make Chimera Recreate Any Coding Agent

**Goal:** Identify every primitive missing from Chimera's component library that prevents you from composing any coding agent architecture — SWE-Agent, Codex, Cline, Aider, OpenHands — by mixing layers together.

**Analogy:** PyTorch has `nn.Linear`, `nn.Conv2d`, `nn.Attention`, `nn.Dropout` — composable primitives that let you build any neural network. Chimera needs the equivalent for coding agents.

---

## Method

For each agent in `/Users/yadkonrad/dev_dev/year26/feb26/coding_agent_research/`:

1. **Decompose** — map the agent's architecture onto Chimera's 8 layers
2. **Gap** — for each layer, what primitives are missing?
3. **Spec** — design the missing primitive as a Chimera component
4. **Verify** — can we now compose that agent's architecture?

---

## Phase 1: Decompose 6 Key Agents

### Agent A: SWE-Agent → "The Benchmark Runner"

| Layer | What SWE-Agent does | Chimera has | Missing primitive |
|-------|-------------------|-------------|-------------------|
| L1 Env | Docker containers with SWE-env | DockerEnvironment | - |
| L3 Provider | OpenAI, Claude via abstraction | 6 providers | - |
| L4 Tools | ACI: ~8 minimal tools (view, edit, search, bash) | 20 tools | **`SWE_TOOLS` preset** — minimal tool group |
| L4 Loop | ReAct + retry loop with scoring | ReAct only | **`RetryLoop`** — wrap any loop with retry + scorer |
| L4 Context | History processors (compress, prune, truncate) | CompactionStrategy | **`HistoryProcessor`** — configurable pruning |
| L4 Prompt | Demonstration-based (show examples in prompt) | Prompt.from_string | **`DemonstrationPrompt`** — include solved examples |
| L5 Eval | SWE-bench trajectory, batch runs | Harness + SWE-bench adapter | **`BatchRunner`** — run N tasks, collect trajectories |
| L8 CLI | Batch mode, trajectory replay | CLI exists | **`--batch` mode** for CLI |

**To recreate SWE-Agent:**
```python
agent = Agent(
    provider=create_provider(model="claude-sonnet"),
    tools=list(SWE_TOOLS),
    loop=RetryLoop(inner=ReAct(max_steps=30), max_retries=3, scorer=pass_rate_scorer),
    prompt=DemonstrationPrompt(examples=["solved_example.md"]),
)
config = LoopConfig(
    compaction=HistoryProcessor(strategy="prune", max_tokens=8000),
)
```

### Agent B: Codex → "The Sandboxed Operator"

| Layer | What Codex does | Chimera has | Missing primitive |
|-------|---------------|-------------|-------------------|
| L1 Env | Seatbelt/bubblewrap OS sandbox | Docker, Git | **`SandboxPolicy`** — declarative sandbox rules |
| L2 Infra | Execution policy (approve/deny per operation) | PermissionPolicy | **`ExecutionPolicy`** — fine-grained per-tool-per-arg |
| L2 Infra | Memory consolidation (cross-session) | Sessions (per-session) | **`LongTermMemory`** — persistent agent memory |
| L2 Infra | Network proxy (egress control) | None | **`NetworkPolicy`** — allow/deny outbound |
| L4 Tools | MCP-first extensibility | MCPClient | - |
| L4 Loop | Hierarchical (orchestrator delegates) | Supervisor | - |
| L4 Context | Turn diff tracking, shell snapshots | Wire (step events) | **`TurnDiffTracker`** — track file changes per turn |
| L4 Prompt | Personality/instruction system | Prompt | **`InstructionLayer`** — composable prompt layers |

**To recreate Codex:**
```python
agent = Agent(
    provider=create_provider(model="gpt-4o"),
    tools=list(AGENT_TOOLS) + mcp_tools,
    loop=ReAct(max_steps=50, config=LoopConfig(
        permissions=ExecutionPolicy(rules=codex_rules),
        wire=Wire(),
    )),
    prompt=InstructionLayer(base=system_prompt, personality=personality, project=project_docs),
)
env = SandboxedEnvironment(policy=SeatbeltPolicy(allow_read=["/workspace"], deny_net=True))
memory = LongTermMemory(store=SQLiteStore("~/.chimera/memory.db"))
```

### Agent C: Cline → "The IDE Agent"

| Layer | What Cline does | Chimera has | Missing primitive |
|-------|---------------|-------------|-------------------|
| L2 Infra | Focus chain (smart context selection) | None | **`FocusChain`** — token-budget-aware context |
| L3 Provider | 20+ providers | 6 + compatible | **Provider configs** for Bedrock/Azure/Vertex |
| L4 Tools | Code definition lookup (AST cross-lang) | RepoMap (4 langs) | **`DefinitionLookup`** — find symbol definition |
| L4 Tools | Browser / computer use | BrowserTool (basic) | **`ComputerUseTool`** — vision + click |
| L4 Loop | Plan/Act mode toggle | ReAct only | **`PlanActLoop`** — read-only plan, then execute |
| L4 Context | @file, @folder, @url, @problems mentions | None | **`ContextMention`** — structured context injection |
| L8 CLI | Checkpoint snapshots (workspace state) | CheckpointManager | - |

**To recreate Cline:**
```python
agent = Agent(
    provider=create_provider(model="claude-sonnet"),
    tools=list(AGENT_TOOLS) + [DefinitionLookup(), ComputerUseTool()],
    loop=PlanActLoop(
        plan_loop=ReAct(max_steps=5, read_only=True),
        act_loop=ReAct(max_steps=20),
    ),
    prompt=Prompt.from_string(system),
)
config = LoopConfig(
    compaction=FocusChain(token_budget=4000, mentions=context_mentions),
)
```

### Agent D: Aider → "The Git-Native Editor"

| Layer | What Aider does | Chimera has | Missing primitive |
|-------|---------------|-------------|-------------------|
| L1 Env | Git-aware (auto-commit) | GitEnvironment | **`GitAutoCommit`** — commit after each successful edit |
| L4 Tools | tree-sitter RepoMap (100+ langs) | RepoMap (4 langs) | **`TreeSitterRepoMap`** — tree-sitter AST |
| L4 Tools | Specialized editors (15+ formats) | edit_file (1 format) | **`EditFormat`** enum + multiple editor tools |
| L4 Loop | Chat with lint feedback | ReAct | **`LintFeedbackLoop`** — run linter after edits, retry |
| L4 Context | Token-budget-aware map selection | None | Covered by **`FocusChain`** |
| L4 Prompt | Coder class variants (architect, ask, context) | Prompt | **`AgentMode`** — switch between read/write/plan |

**To recreate Aider:**
```python
agent = Agent(
    provider=create_provider(model="claude-sonnet"),
    tools=[TreeSitterRepoMap(), UnifiedDiffEditor(), SearchReplaceTool()],
    loop=LintFeedbackLoop(inner=ReAct(), linter="ruff"),
    prompt=AgentMode.ARCHITECT,  # or .EDITOR, .ASK
)
env = GitEnvironment(workdir=".", auto_commit=True, commit_message_style="aider")
```

### Agent E: OpenHands → "The SDK Agent"

| Layer | What OpenHands does | Chimera has | Missing primitive |
|-------|-------------------|-------------|-------------------|
| L1 Env | Docker with image selection | DockerEnvironment | **Container image config** |
| L2 Infra | Event-sourced architecture | EventBus + EventLog | - |
| L4 Tools | Browser automation (computer use) | BrowserTool | **`ComputerUseTool`** (shared with Cline) |
| L4 Composition | SDK for defining agents in code | Agent class | - (already composable) |
| L7 Workflow | Theory-of-Mind reasoning | None | **`ReasoningModule`** — meta-cognitive layer |
| L8 CLI | Multi-deployment (SDK, CLI, GUI, Cloud) | CLI only | **`ServerMode`** — HTTP API for Chimera |

### Agent F: Gemini CLI → "The Google Agent"

| Layer | What Gemini does | Chimera has | Missing primitive |
|-------|----------------|-------------|-------------------|
| L3 Provider | Google Search grounding | None | **`SearchGrounding`** — augment prompts with search |
| L4 Context | 1M token context, caching | CompactionStrategy | **`TokenCache`** — cache repeated context |
| L8 CLI | Headless/scripting mode | CLI | **`--headless` flag** |
| L8 CLI | GEMINI.md project context | ProjectConfig | - (already exists) |

---

## Phase 2: Deduplicate — The Missing Primitives

Across all 6 agents, the missing primitives collapse into **15 unique components**:

### Context Layer (L2/L4)
1. **`FocusChain`** — token-budget-aware context selection (from Cline, Aider)
2. **`HistoryProcessor`** — compress/prune/truncate conversation history (from SWE-Agent)
3. **`LongTermMemory`** — persistent memory across sessions (from Codex)
4. **`ContextMention`** — @file, @folder, @url structured injection (from Cline)
5. **`TokenCache`** — cache repeated context to save tokens (from Gemini)

### Loop Layer (L4)
6. **`RetryLoop`** — wrap any loop with retry + scoring (from SWE-Agent)
7. **`PlanActLoop`** — read-only planning phase, then execution (from Cline)
8. **`LintFeedbackLoop`** — run linter after edits, feed errors back (from Aider)

### Tool Layer (L4)
9. **`TreeSitterRepoMap`** — 100+ language AST via tree-sitter (from Aider)
10. **`DefinitionLookup`** — find symbol definition across languages (from Cline)
11. **`ComputerUseTool`** — vision + browser + click (from Cline, OpenHands)

### Environment Layer (L1)
12. **`SandboxPolicy`** — declarative OS-level sandbox rules (from Codex)

### Prompt Layer (L4)
13. **`DemonstrationPrompt`** — include solved examples in prompt (from SWE-Agent)
14. **`InstructionLayer`** — composable prompt layers (base + personality + project) (from Codex)

### Preset Layer (L4)
15. **`AgentPreset`** — named configurations that recreate specific agents:
    - `AgentPreset.SWE_AGENT` → SWE_TOOLS + RetryLoop + HistoryProcessor
    - `AgentPreset.CODEX` → AGENT_TOOLS + ExecutionPolicy + LongTermMemory
    - `AgentPreset.AIDER` → TreeSitterRepoMap + LintFeedbackLoop + GitAutoCommit
    - `AgentPreset.CLINE` → PlanActLoop + FocusChain + DefinitionLookup

---

## Phase 3: Implementation Order

Grouped by dependency and value:

### Wave 1: Context primitives (highest value, unblocks most agents)
- `FocusChain` — unblocks Cline + Aider patterns
- `HistoryProcessor` — unblocks SWE-Agent pattern
- `ContextMention` — unblocks @file/@folder pattern

### Wave 2: Loop variants (unblocks agent-style differences)
- `RetryLoop` — unblocks SWE-Agent benchmarking
- `PlanActLoop` — unblocks Cline dual-mode
- `LintFeedbackLoop` — unblocks Aider linting pattern

### Wave 3: Tool upgrades (deeper capability)
- `TreeSitterRepoMap` — major upgrade to code understanding
- `DefinitionLookup` — cross-language go-to-definition
- `DemonstrationPrompt` — few-shot example prompting

### Wave 4: Infrastructure (security + persistence)
- `SandboxPolicy` — OS-level sandboxing
- `LongTermMemory` — cross-session persistence
- `InstructionLayer` — composable prompts

### Wave 5: Agent presets (the payoff)
- `AgentPreset` — named configurations that compose all of the above

---

## Phase 4: Verification

For each preset, verify by running the same benchmark task:

```python
# Does SWE-Agent preset produce SWE-Agent-like behavior?
agent = AgentPreset.SWE_AGENT.build(provider=create_provider())
result = Harness(SWEBench(), agent).run()

# Does Aider preset produce Aider-like behavior?
agent = AgentPreset.AIDER.build(provider=create_provider())
result = agent.run("Fix the bug in utils.py", env=GitEnvironment("."))
```

---

## Research Protocol Per Agent

For each agent in `coding_agent_research/`:

```
1. Read README.md and AGENTS.md (5 min)
2. Map source structure to Chimera's 8 layers (10 min)
3. For each layer:
   a. What does this agent do?
   b. Does Chimera have an equivalent?
   c. If not, what primitive is needed?
   d. Is this primitive needed by other agents too?
4. Write the gap as a component spec (5 min per primitive)
5. Create GitHub issue for each new primitive
```

Total: ~19 agents × 20 min = ~6 hours of research
Expected output: ~15-20 unique primitives, ~20 GitHub issues
