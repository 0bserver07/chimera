# Coding Agent Feature Comparison

Chimera vs 8 major coding agents. Based on source code analysis of repos in `coding_agent_research/`.

---

## Summary Matrix

| Feature | Chimera | Codex | OpenCode | Kimi CLI | Aider | SWE-Agent | OpenHands | Cline | Gemini CLI |
|---------|---------|-------|----------|----------|-------|-----------|-----------|-------|------------|
| **Language** | Python | Rust/TS | Bun TS | Python | Python | Python | Python | TS | TS |
| **Providers** | 6+ | 1 (OpenAI) | 4+ | 1 (Kimi) | 4+ | 4+ | 4+ (LiteLLM) | 20+ | 1 (Google) |
| **Built-in Tools** | **20** | 15-20 | 20+ | 10-15 | 5-8 | 8-10 | 15-20 | 18 | 12-15 |
| **Strategies** | **9** (ReAct, CEGIS, Incremental, Tree, Plan, Reflexion, ToT, Curriculum, Passthrough) | ReAct | Dual (build/plan) | ReAct | Chat variants | ReAct+retry | Event-driven | Plan/Act | Task-based |
| **Composition** | **Pipeline, Ensemble, Supervisor** | Hierarchical | Subagents | ACP | Coder classes | Retry loops | SDK | Subagents | MCP |
| **Sandboxing** | Docker, Git isolation | **Seatbelt/BW** | Optional | None | None | Docker | Docker opt. | None | None |
| **MCP** | Yes | Yes | Yes | **Full** | No | No | Yes | **Full** | Yes |
| **LSP** | **Yes** | Via IDE | Yes (built-in) | No | No | No | Optional | Yes | No |
| **Cost Tracking** | **Per-model, budget limits** | Yes | Yes | Basic | Yes | Yes | Yes | Per-token | Yes |
| **Sessions** | Memory, File, SQLite, Event-sourced | State DB | File | File | Git-aware | Trajectory | DB | Disk | Checkpoints |
| **Synthesis** | **TestConvergence, CEGIS, TreeSearch, Sketch, Curriculum** | No | No | No | No | No | No | No | No |
| **Evaluation** | **SWE-bench, HumanEval, AIMO, Custom** | Limited | Testing | No | No | **SWE-bench** | SWE-bench | No | GH workflow |
| **ML Primitives** | **Training Curves, Validation Split, Regularization, Tuner, Oracle, Mutation, Fault Loc, Impact, Spec Inference** | No | No | No | No | No | No | No | No |
| **Open Source** | Yes (MIT) | No | Yes | No | Yes | Yes | Partial | Yes | No |

---

## Where Chimera Is Unique

### 1. Synthesis Layer (no equivalent in any other agent)

Chimera treats code generation as ML training. No other agent has:

- **Test-driven synthesis** — `synthesize("Build X", tests="./tests/")` iterates until tests pass
- **9 strategies** — TestConvergence, CEGIS, Incremental, TreeSearch, Curriculum, Ensemble, MajorityVoting, AIMOEnsemble, Passthrough
- **Sketch synthesis** — fill holes in partial programs
- **Validation splits** — hold out tests to detect overfitting
- **Training curves** — per-epoch diagnostics with plateau/oscillation detection

Other agents are interactive tools. Chimera can also be a batch synthesis framework.

### 2. ML/Program Synthesis Primitives (unique to Chimera)

- **Training Curves** — diagnose synthesis: plateau, oscillation, cost explosion
- **Validation Splits** — train/val test split, overfit gap metric
- **Regularization** — complexity/line-count/duplication penalties + critic-as-regularizer
- **Hyperparameter Search** — grid search over model/strategy/config
- **CEGIS** — counterexample-guided, one failure at a time
- **Growing Test Suite** — oracle generates new tests during synthesis
- **Fault Localization** — Ochiai-style suspiciousness ranking
- **Change Impact Analysis** — show blast radius before edits
- **Mutation Testing** — find weak tests by mutating code
- **Specification Inference** — auto-generate regression tests from existing code

### 3. Composition Patterns (most structured)

Other agents have ad-hoc delegation. Chimera has formal patterns:

- **Pipeline** — sequential agent chain (coder → reviewer)
- **Ensemble** — parallel agents, pick best
- **Supervisor** — coordinator delegates to named workers

### 4. Three-Tier API (ML-framework-inspired)

```python
# One-liner
result = chimera.synthesize("Build X", tests="./tests/")

# Configured
trainer = Trainer(spec=spec, agent=agent, env=env)
result = trainer.synthesize(strategy=TestConvergence())

# Subclass
class MyStrategy(Strategy):
    def run(self, agent, spec, env, ...): ...
```

No other agent offers this progressive disclosure.

---

## Where Others Are Stronger

### Sandboxing — Codex wins

Codex has Seatbelt (macOS), bubblewrap (Linux), custom Windows sandbox with fine-grained SBPL policies, network proxy for egress control. Chimera has Docker and Git isolation but nothing at the OS syscall level.

**What Chimera could port:** OS-level sandboxing is complex (platform-specific). But the execution policy pattern (`exec_policy.rs`) — approve/deny at the tool level — maps to Chimera's existing `PermissionPolicy` system.

### Provider Breadth — Cline wins

Cline supports 20+ providers including OpenRouter, AWS Bedrock, Azure, GCP Vertex, Cerebras, Groq, LM Studio. Chimera has 6 providers + OpenAI-compatible catch-all.

**What Chimera could port:** The OpenAI-compatible provider already handles most of these. Adding explicit provider configs for Bedrock/Azure/Vertex would expand reach.

### RepoMap — Aider wins

Aider's RepoMap uses tree-sitter for 100+ language AST parsing, SQLite caching, token-budget-aware context selection. Chimera's RepoMap uses Python `ast` module (Python only) + regex parsers for TS/Go/Rust.

**What Chimera could port:** tree-sitter integration for multi-language AST. This would be a significant upgrade to `chimera/tools/parsers/`.

### IDE Integration — Cline wins

Cline is VS Code native with focus chains, diagnostics integration, diff preview, checkpoint snapshots. Chimera has CLI + REPL.

**What Chimera could port:** VS Code extension wrapping Chimera's Agent. The Wire protocol was designed for this — it provides the bidirectional UI channel.

### Benchmarking — SWE-Agent wins

SWE-Agent is optimized for SWE-bench (trajectories, batch runs, retry loops, demonstration prompting). Chimera has benchmark adapters but hasn't optimized for competitive scores.

**What Chimera could port:** SWE-Agent's history processor patterns (compress, prune) and retry loop strategies. The ACI (Agent-Computer Interface) concept — minimalist tool set designed for benchmarks — could be a new ToolGroup.

### Event Architecture — OpenHands

OpenHands has a full event-sourced architecture with SDK for composing agents, Theory-of-Mind module, enterprise features (RBAC, Slack/Jira integration).

**What Chimera could port:** The SDK-first pattern. Chimera's `Agent` + `Tool` + `Loop` is already composable, but a higher-level SDK for defining agents declaratively (like OpenHands) could be valuable.

---

## Feature Gap Analysis: What to Port Next

### High Value (fills real gaps)

| Feature | From | Why |
|---------|------|-----|
| tree-sitter RepoMap | Aider | 100+ language AST vs 4 languages |
| OS-level sandboxing | Codex | Real security for untrusted code |
| Retry loop strategies | SWE-Agent | Better benchmark performance |
| Focus chain / smart context | Cline | Better token efficiency |
| Demonstration prompting | SWE-Agent | Learn from examples |

### Medium Value (nice to have)

| Feature | From | Why |
|---------|------|-----|
| VS Code extension | Cline | IDE integration |
| Browser automation | Cline/OpenHands | Computer use capability |
| Google Search grounding | Gemini CLI | Web-augmented generation |
| Memory consolidation | Codex | Long-term agent memory |
| Headless/scripting mode | Gemini CLI | CI/CD integration |

### Already Covered by Chimera

| Feature | Other agents have | Chimera equivalent |
|---------|-------------------|-------------------|
| Multi-provider | Cline (20+), Aider (4+) | 6 + compatible catch-all |
| MCP | Kimi, Cline, Codex | MCPClient + MCPToolSource |
| Subagent delegation | Cline, OpenCode | DelegateTool + Supervisor |
| Session persistence | Most | Memory, File, SQLite, Event-sourced |
| Cost tracking | Most | CostTracker + per-model pricing |
| Git integration | Aider (best), Codex | GitEnvironment + GitWorkflow |
| Context compaction | Codex, OpenCode | CompactionStrategy + D-Mail |
| Permissions | Codex (best), Cline | PermissionPolicy + SecurityAnalyzer |

---

## Conclusion

Chimera's unique position: **it's the only framework that treats agentic coding as machine learning.** The synthesis layer, ML primitives, and program synthesis techniques have no equivalent in any other agent.

The gaps are in:
1. **Breadth** (more providers, more language ASTs)
2. **Depth** (OS-level sandboxing, IDE integration)
3. **Optimization** (competitive SWE-bench scores, better token efficiency)

These are engineering tasks, not architectural ones — Chimera's architecture already supports all of them.
