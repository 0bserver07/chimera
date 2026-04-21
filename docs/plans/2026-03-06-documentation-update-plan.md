# Documentation Update Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update all Chimera documentation to reflect the current 8-layer / 1712-test / 32-module state. Serve both human users and coding agents.

**Architecture:** Three independently committable tiers: (1) fix stale content in existing files, (2) add module/workflow/reference pages for all new packages, (3) add practical how-to guides. All docs use mkdocs-material with mkdocstrings autodoc.

**Tech Stack:** Markdown, Mermaid diagrams, mkdocs-material, mkdocstrings (Python/Google-style)

---

## Tier 1: Fix the Lies

### Task 1: Update README.md

**Files:**
- Modify: `README.md`

**Step 1: Rewrite README.md**

Replace the full contents with updated version reflecting:
- 8-layer stack diagram (matching CLAUDE.md)
- "1700+ tests passing" (don't hardcode exact count)
- 16 tools, 6 providers, 4 loops, 3 composition, 7 strategies, 6 environments
- Features section covering: Workflows (CI fix, review, research, migration, docs gen, test gen), Security (analyzers, policies), Plugins (manager, marketplace, directory loader), REPL (14 slash commands), Critic (LLM + checklist), ACP (external agent protocol), MCP, LSP, Cost Tracking, Secrets, Checkpoints, Sessions (memory/file/SQLite + event-sourced)
- Roadmap: mark plugin system, docs site, CI agent as DONE. Keep Docker integration tests as TODO.
- Contributing: updated test count, mention `ruff check` and `mypy`

Key content for the architecture section:
```
8-layer stack (each layer usable independently):

Layer 8: CLI             chimera synthesize / eval / bench / code / review /
                         ci-fix / research / docs / testgen / migrate / plugins
Layer 7: Workflows       CIFixWorkflow, ReviewOrchestrator, Researcher,
                         MigrationPlanner, DocGenerator, TestGenerator
Layer 6: Synthesis       Trainer, Strategy, Spec, Architecture, Constraint
Layer 5: Evaluation      Harness, Metrics, Benchmarks (SWE-bench, HumanEval, AIMO)
Layer 4: Agent           Agent, Tools, Loops, Prompt, Context, Critic, ACP
Layer 3: Provider        Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compat
Layer 2: Infrastructure  Security, Secrets, Permissions, Events, Sessions,
                         Compaction, Streaming, Detection, Config, Plugins, MCP, LSP
Layer 1: Environment     Local, Docker, Git, Remote, Cloud, PersistentShell
```

**Step 2: Commit**
```bash
git add README.md
git commit -m "docs: update README to reflect 8-layer architecture and current state"
```

---

### Task 2: Update docs/architecture.md

**Files:**
- Modify: `docs/architecture.md`

**Step 1: Rewrite architecture.md**

Update the Mermaid layer diagram to show 8 layers (add Infrastructure between Provider and Environment, add Workflows between Synthesis and CLI). Add sections for:

- Layer 7 (Workflows): CI Fix, Review, Research, Migration, DocGen, TestGen — all thin glue calling Agent.run()
- Layer 2 (Infrastructure): Security, Secrets, Permissions, Events, Sessions, Compaction, Streaming, Detection, Config, Plugins, MCP, LSP, Auth

Update the Module Dependency Map mermaid to include:
- LoopConfig → audit_log, checkpoint_manager, git_workflow (new fields)
- Security → LoopConfig (analyzer + policy)
- Critic → LoopConfig (CriticMixin)
- Secrets → Events (RedactionMiddleware)
- Plugins → Tools, Agents, Strategies (PluginExtensionRegistry)
- ACP → Agent (ExternalAgentTool)
- MCP → Tools (MCPToolSource)
- LSP → Tools (LSPTool)

Update Extension Architecture to list all extensible ABCs:
- SecurityAnalyzer, Critic, CompactionStrategy, ConfirmationPolicy, BasePlugin, DiscriminatedUnion

Keep the existing Agent Loop Flow and Training Pipeline diagrams — they're still correct.

**Step 2: Commit**
```bash
git add docs/architecture.md
git commit -m "docs: update architecture diagrams to 8-layer stack"
```

---

### Task 3: Update docs/index.md (landing page)

**Files:**
- Modify: `docs/index.md`

**Step 1: Update stats table and explore cards**

Update the "What's Inside" table:
| Category | Count | Highlights |
|---|---|---|
| Providers | 6 | Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compatible |
| Tools | 16 | read, write, edit, bash, search, git, test, web_fetch, repo_map, delegate, browser, image_read, import_graph, replace_in_file, verify, list_files |
| Loops | 4 | ReAct, PlanAndExecute, Reflexion, TreeOfThought |
| Composition | 3 | Pipeline, Ensemble, Supervisor |
| Strategies | 7 | TestConvergence, Curriculum, Ensemble, Passthrough, TreeSearch, MajorityVoting, AIMOEnsemble |
| Environments | 6 | Local, Docker, Git, Remote, Cloud, PersistentShell |
| Workflows | 6 | CI Fix, Code Review, Research, Migration, Doc Gen, Test Gen |
| Infrastructure | 14 | Security, Secrets, Permissions, Events, Sessions, Compaction, Streaming, Detection, Config, Plugins, MCP, LSP, Auth, Checkpoints |

Add Explore cards for Workflows and CLI/REPL sections.

**Step 2: Commit**
```bash
git add docs/index.md
git commit -m "docs: update landing page stats and navigation cards"
```

---

### Task 4: Update docs/modules/index.md

**Files:**
- Modify: `docs/modules/index.md`

**Step 1: Expand module table**

Add rows for all new modules (in addition to existing 8):
| Module | Package | Purpose |
|---|---|---|
| [Security](security.md) | `chimera.security` | Tool call risk analysis and confirmation policies |
| [Secrets](secrets.md) | `chimera.secrets` | Secret detection and redaction in event streams |
| [Critic](critic.md) | `chimera.critic` | In-loop action evaluation with LLM or rule-based critics |
| [ACP](acp.md) | `chimera.acp` | Agent Client Protocol for external agent interop |
| [Plugins](plugins.md) | `chimera.plugins` | Plugin lifecycle, extension registry, marketplace |
| [Config](config.md) | `chimera.config` | Polymorphic config serialization, project config |
| [Checkpoints](checkpoints.md) | `chimera.checkpoints` | Named checkpoints with create/restore/undo |
| [Cost Tracking](cost-tracking.md) | `chimera.providers.cost_tracker` | Granular token and cost tracking with budgets |
| [MCP](mcp.md) | `chimera.mcp` | Model Context Protocol client (stdio/HTTP) |
| [LSP](lsp.md) | `chimera.lsp` | Language Server Protocol for diagnostics, completion, rename |

Update dependency diagram mermaid to include Security, Critic, Secrets, Plugins, ACP, MCP, LSP.

**Step 2: Commit**
```bash
git add docs/modules/index.md
git commit -m "docs: expand modules index with 10 new module entries"
```

---

### Task 5: Update mkdocs.yml navigation

**Files:**
- Modify: `mkdocs.yml`

**Step 1: Add nav entries for all new pages**

Add to nav:
```yaml
  - Workflows:
    - workflows/index.md
    - CI Fix: workflows/ci-fix.md
    - Code Review: workflows/review.md
    - Research: workflows/research.md
    - Migration: workflows/migration.md
    - Doc Generation: workflows/docgen.md
    - Test Generation: workflows/testgen.md
```

Add to Modules section:
```yaml
    - Security: modules/security.md
    - Secrets: modules/secrets.md
    - Critic: modules/critic.md
    - ACP: modules/acp.md
    - Plugins: modules/plugins.md
    - Config: modules/config.md
    - Checkpoints: modules/checkpoints.md
    - Cost Tracking: modules/cost-tracking.md
    - MCP: modules/mcp.md
    - LSP: modules/lsp.md
    - CLI & REPL: modules/cli.md
```

Add to Guides section:
```yaml
    - Use the REPL: guides/use-the-repl.md
    - Add Security Policies: guides/add-security-policies.md
    - Build a Plugin: guides/build-a-plugin.md
    - Connect External Agents: guides/connect-external-agents.md
    - Automate CI Fixes: guides/automate-ci-fixes.md
```

Add to API Reference section:
```yaml
    - Security: reference/security.md
    - Secrets: reference/secrets.md
    - Critic: reference/critic.md
    - ACP: reference/acp.md
    - Plugins: reference/plugins.md
    - Config: reference/config.md
    - Checkpoints: reference/checkpoints.md
    - Cost Tracking: reference/cost-tracking.md
    - MCP: reference/mcp.md
    - LSP: reference/lsp.md
    - Workflows: reference/workflows.md
    - CLI: reference/cli.md
```

**Step 2: Commit**
```bash
git add mkdocs.yml
git commit -m "docs: add nav entries for all new doc pages"
```

---

## Tier 2: Fill the Gaps

### Task 6: Create module docs — Security

**Files:**
- Create: `docs/modules/security.md`
- Create: `docs/reference/security.md`

**Step 1: Write docs/modules/security.md**

Structure:
- Purpose: Evaluate tool call risk before execution. Three analyzer types + configurable confirmation policies.
- Quick example showing RuleBasedSecurityAnalyzer + ConfirmAboveThreshold
- Key classes: SecurityRisk (LOW/MEDIUM/HIGH/UNKNOWN), SecurityAnalyzer ABC, LLMSecurityAnalyzer, RuleBasedSecurityAnalyzer, CompositeSecurityAnalyzer, ConfirmationPolicy ABC, NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold
- Integration: wire into LoopConfig, works with Permissions module
- Import: `from chimera.security import RuleBasedSecurityAnalyzer, ConfirmAboveThreshold, SecurityRisk`

**Step 2: Write docs/reference/security.md**

```markdown
# Security API Reference

::: chimera.security
    options:
      show_root_heading: false
      members_order: source
```

**Step 3: Commit**
```bash
git add docs/modules/security.md docs/reference/security.md
git commit -m "docs: add security module and reference pages"
```

---

### Task 7: Create module docs — Secrets

**Files:**
- Create: `docs/modules/secrets.md`
- Create: `docs/reference/secrets.md`

**Step 1: Write docs/modules/secrets.md**

Structure:
- Purpose: Detect and redact secrets (API keys, AWS credentials, bearer tokens, etc.) from agent output and event streams.
- Quick example: SecretRegistry with custom secrets + SecretDetector with 10 built-in patterns + RedactionMiddleware on EventBus
- Key classes: SecretRegistry, SecretDetector, RedactionMiddleware
- Integration: RedactionMiddleware plugs into EventBus middleware chain
- Import: `from chimera.secrets import SecretRegistry, SecretDetector, RedactionMiddleware`

**Step 2: Write docs/reference/secrets.md**

mkdocstrings autodoc for `chimera.secrets`.

**Step 3: Commit**
```bash
git add docs/modules/secrets.md docs/reference/secrets.md
git commit -m "docs: add secrets module and reference pages"
```

---

### Task 8: Create module docs — Critic

**Files:**
- Create: `docs/modules/critic.md`
- Create: `docs/reference/critic.md`

**Step 1: Write docs/modules/critic.md**

Structure:
- Purpose: In-loop evaluation of agent actions. LLMCritic uses a provider to score actions; ChecklistCritic uses rule-based checks. CriticMixin adds iterative refinement to any loop.
- Quick example: ChecklistCritic with custom rules, CriticConfig with mode and threshold
- Key classes: Critic ABC, CriticResult, CriticConfig, CriticMode (ALL_ACTIONS / FINISH_ONLY / TOOL_AND_FINISH), LLMCritic, ChecklistCritic, CriticMixin
- Integration: CriticMixin mixed into loop classes, CriticConfig in LoopConfig
- Import: `from chimera.critic import ChecklistCritic, CriticConfig, CriticMode`

**Step 2: Write docs/reference/critic.md**

mkdocstrings autodoc for `chimera.critic`.

**Step 3: Commit**
```bash
git add docs/modules/critic.md docs/reference/critic.md
git commit -m "docs: add critic module and reference pages"
```

---

### Task 9: Create module docs — ACP

**Files:**
- Create: `docs/modules/acp.md`
- Create: `docs/reference/acp.md`

**Step 1: Write docs/modules/acp.md**

Structure:
- Purpose: Agent Client Protocol — connect to external agents (any language/framework) via JSON-RPC 2.0 over subprocess stdio. Wrap external agents as Chimera tools.
- Quick example: ACPClient with ACPSessionConfig, ExternalAgentTool wrapping an external agent
- Key classes: ACPSessionConfig, ACPToolCall, ACPResponse, ACPClient, ExternalAgentTool
- Protocol: JSON-RPC 2.0, methods: session.start, session.message, session.stop, session.fork
- Integration: ExternalAgentTool is a BaseTool, can be added to any agent's tool list or used in Supervisor composition
- Import: `from chimera.acp import ACPClient, ACPSessionConfig, ExternalAgentTool`

**Step 2: Write docs/reference/acp.md**

mkdocstrings autodoc for `chimera.acp`.

**Step 3: Commit**
```bash
git add docs/modules/acp.md docs/reference/acp.md
git commit -m "docs: add ACP module and reference pages"
```

---

### Task 10: Create module docs — Plugins

**Files:**
- Create: `docs/modules/plugins.md`
- Create: `docs/reference/plugins.md`

**Step 1: Write docs/modules/plugins.md**

Structure:
- Purpose: Plugin lifecycle management. Load/unload plugins, register extensions (tools, agents, strategies, constraints, middleware, skills, MCP servers, hooks), discover from directories, search/install from marketplace.
- Quick example: Create a BasePlugin subclass, register tools, load via PluginManager
- Key classes: BasePlugin ABC, ComponentRegistry, Hook, MCPServerConfig, PluginManager, PluginExtensionRegistry, DirectoryPluginLoader, Marketplace, MarketplaceRegistry, PluginInfo
- Directory layout: `agents/*.md` (agent definitions), `.mcp.json` (MCP server configs), `hooks/` (shell hooks)
- Import: `from chimera.plugins import BasePlugin, PluginManager, PluginExtensionRegistry`

**Step 2: Write docs/reference/plugins.md**

mkdocstrings autodoc for `chimera.plugins`.

**Step 3: Commit**
```bash
git add docs/modules/plugins.md docs/reference/plugins.md
git commit -m "docs: add plugins module and reference pages"
```

---

### Task 11: Create module docs — Config

**Files:**
- Create: `docs/modules/config.md`
- Create: `docs/reference/config.md`

**Step 1: Write docs/modules/config.md**

Structure:
- Purpose: Polymorphic configuration serialization and project config discovery. DiscriminatedUnion enables from_config/to_config dispatch by type field. ChimeraConfig loads YAML/JSON. ProjectConfig discovers config files in project directories.
- Quick example: DiscriminatedUnion subclass with type dispatch, ChimeraConfig.from_file()
- Key classes: DiscriminatedUnion, ChimeraConfig, ProjectConfig, ConfigSource, Skill, SkillRegistry, StructuredOutput
- Import: `from chimera.config import DiscriminatedUnion, ChimeraConfig, ProjectConfig`

**Step 2: Write docs/reference/config.md**

mkdocstrings autodoc for `chimera.config`.

**Step 3: Commit**
```bash
git add docs/modules/config.md docs/reference/config.md
git commit -m "docs: add config module and reference pages"
```

---

### Task 12: Create module docs — Checkpoints, Cost Tracking

**Files:**
- Create: `docs/modules/checkpoints.md`
- Create: `docs/modules/cost-tracking.md`
- Create: `docs/reference/checkpoints.md`
- Create: `docs/reference/cost-tracking.md`

**Step 1: Write docs/modules/checkpoints.md**

Structure:
- Purpose: Named checkpoints with metadata on top of Environment.checkpoint/restore. Create, restore by name/ID, undo, list.
- Quick example: CheckpointManager with GitEnvironment
- Key classes: CheckpointManager, CheckpointInfo
- Integration: checkpoint_manager field in LoopConfig, /checkpoint REPL command
- Import: `from chimera.checkpoints import CheckpointManager, CheckpointInfo`

**Step 2: Write docs/modules/cost-tracking.md**

Structure:
- Purpose: Granular token and cost tracking. Per-step breakdown with cache/reasoning token counts. Budget limits with callbacks.
- Quick example: CostTracker with budget, record usage, check breakdown
- Key classes: CostTracker, TokenUsage, StepUsage, CostLimitExceeded
- Integration: CostTracker in Agent, /cost REPL command
- Import: `from chimera.providers.cost_tracker import CostTracker, TokenUsage`

**Step 3: Write reference pages**

mkdocstrings autodoc for `chimera.checkpoints` and `chimera.providers.cost_tracker`.

**Step 4: Commit**
```bash
git add docs/modules/checkpoints.md docs/modules/cost-tracking.md docs/reference/checkpoints.md docs/reference/cost-tracking.md
git commit -m "docs: add checkpoints and cost tracking module pages"
```

---

### Task 13: Create module docs — MCP, LSP

**Files:**
- Create: `docs/modules/mcp.md`
- Create: `docs/modules/lsp.md`
- Create: `docs/reference/mcp.md`
- Create: `docs/reference/lsp.md`

**Step 1: Write docs/modules/mcp.md**

Structure:
- Purpose: Model Context Protocol client. Connect to MCP servers via stdio or HTTP transport. Auto-discover tools and expose them as Chimera tools.
- Quick example: MCPClient with StdioTransport, MCPToolSource
- Key classes: MCPClient, MCPTool, MCPToolSource, MCPTransport, StdioTransport, HTTPTransport
- Import: `from chimera.mcp import MCPClient, MCPToolSource, StdioTransport`

**Step 2: Write docs/modules/lsp.md**

Structure:
- Purpose: Language Server Protocol integration. Get diagnostics, completions, code actions, and rename refactoring from any LSP-compatible language server.
- Quick example: LSPClient with LanguageServerConfig, get diagnostics
- Key classes: LSPClient, LSPManager, LSPSession, LSPTool, Diagnostic, Severity, LanguageServerConfig, BUILTIN_SERVERS
- Import: `from chimera.lsp import LSPClient, LSPTool, LanguageServerConfig`

**Step 3: Write reference pages**

mkdocstrings autodoc for `chimera.mcp` and `chimera.lsp`.

**Step 4: Commit**
```bash
git add docs/modules/mcp.md docs/modules/lsp.md docs/reference/mcp.md docs/reference/lsp.md
git commit -m "docs: add MCP and LSP module pages"
```

---

### Task 14: Create CLI & REPL module page

**Files:**
- Create: `docs/modules/cli.md`
- Create: `docs/reference/cli.md`

**Step 1: Write docs/modules/cli.md**

Structure:
- Purpose: Command-line interface with 11 subcommands and an interactive REPL with 14 slash commands.
- Subcommands table:
  | Command | Description |
  |---|---|
  | `chimera synthesize` | Synthesize code from spec + tests |
  | `chimera eval` | Evaluate against benchmarks |
  | `chimera bench` | Run benchmark suites |
  | `chimera code` | Interactive REPL |
  | `chimera review` | AI code review |
  | `chimera ci-fix` | Diagnose and fix CI failures |
  | `chimera research` | Research a question |
  | `chimera docs` | Generate API docs from source |
  | `chimera testgen` | Generate test skeletons |
  | `chimera migrate` | Apply migration presets |
  | `chimera plugins` | Search/install/uninstall plugins |
- REPL slash commands table:
  | Command | Description |
  |---|---|
  | `/help` | Show available commands |
  | `/model` | Show current model |
  | `/cost` | Show cost breakdown |
  | `/clear` | Clear conversation context |
  | `/history` | Show conversation history |
  | `/tools` | List loaded tools |
  | `/context` | Show context stats |
  | `/debug` | Toggle debug mode |
  | `/session` | Save/load session |
  | `/compact` | Compact context window |
  | `/audit` | Show permission audit log |
  | `/checkpoint` | Create/restore checkpoints |
  | `/agent` | Switch agent preset |
  | `/exit` | Exit REPL |
- Quick start: `pip install chimera-run[anthropic] && chimera code`

**Step 2: Write docs/reference/cli.md**

mkdocstrings autodoc for `chimera.cli.main` and `chimera.cli.code`.

**Step 3: Commit**
```bash
git add docs/modules/cli.md docs/reference/cli.md
git commit -m "docs: add CLI and REPL module page"
```

---

### Task 15: Create Workflows section

**Files:**
- Create: `docs/workflows/index.md`
- Create: `docs/workflows/ci-fix.md`
- Create: `docs/workflows/review.md`
- Create: `docs/workflows/research.md`
- Create: `docs/workflows/migration.md`
- Create: `docs/workflows/docgen.md`
- Create: `docs/workflows/testgen.md`

**Step 1: Write docs/workflows/index.md**

Overview of Layer 7 — Workflows are thin glue composing Agent.run() with domain-specific parsing and prompting. All accessible via CLI and Python API. Umbrella import: `from chimera.workflows import CIFixWorkflow, ReviewOrchestrator, Researcher, ...`

**Step 2: Write individual workflow pages**

Each workflow page follows:
1. What it does (one paragraph)
2. CLI usage (`chimera ci-fix --log build.log`)
3. Python API example (instantiate + call `.run()`)
4. Key classes with import paths
5. How it integrates with Agent and Environment

Content for each:

**ci-fix.md**: CIFixWorkflow — parse_ci_log() extracts FailureInfo, build_prompt() creates agent instructions, run() retries with budget. CLI: `chimera ci-fix --log build.log --max-attempts 3`.

**review.md**: ReviewOrchestrator — Two-agent loop: reviewer Agent reviews diff, author Agent fixes comments. Iterates until approved or max rounds. CLI: `chimera review --diff changes.patch --max-rounds 3`.

**research.md**: Researcher — Plan decomposition (question → sub-questions + search terms), agent executes research, returns synthesis. CLI: `chimera research --question "How does X work?"`.

**migration.md**: MigrationPlanner — Rule-based code transforms with presets (python2-to-3, commonjs-to-esm). Each MigrationRule has a pattern + replacement. CLI: `chimera migrate --source ./src --preset python2-to-3`.

**docgen.md**: DocGenerator — AST-based source scanning, extracts DocSection entries (classes, functions, modules), writes markdown. CLI: `chimera docs --source ./chimera --output docs/api`.

**testgen.md**: TestGenerator — Source analysis → test case skeletons. Analyzes function signatures, generates pytest test stubs. CLI: `chimera testgen --source ./chimera --output tests/generated`.

**Step 3: Commit**
```bash
git add docs/workflows/
git commit -m "docs: add workflows section with 6 workflow pages"
```

---

## Tier 3: New Guides

### Task 16: Guide — Use the REPL

**Files:**
- Create: `docs/guides/use-the-repl.md`

**Step 1: Write guide**

Sections:
1. **Goal**: Get productive with `chimera code` interactive REPL
2. **Prerequisites**: `pip install chimera-run[anthropic]`, API key set
3. **Start the REPL**: `chimera code --model claude-sonnet-4 --workdir ./my-project`
4. **Slash commands walkthrough**: /model, /cost, /tools, /context, /clear, /history, /debug
5. **Session management**: /session save, /session load, resume conversations
6. **Cost tracking**: /cost for per-model breakdown, budget limits
7. **Checkpoints**: /checkpoint create "before refactor", /checkpoint restore "before refactor"
8. **Audit log**: /audit to see tool execution history
9. **Agent presets**: /agent build, /agent review, /agent explore
10. **Context compaction**: /compact when context window fills up
11. **Complete example**: Full REPL session transcript

**Step 2: Commit**
```bash
git add docs/guides/use-the-repl.md
git commit -m "docs: add REPL usage guide"
```

---

### Task 17: Guide — Add Security Policies

**Files:**
- Create: `docs/guides/add-security-policies.md`

**Step 1: Write guide**

Sections:
1. **Goal**: Add security analysis and confirmation policies to your agent
2. **Prerequisites**: Working agent setup
3. **Step 1 — Choose an analyzer**: RuleBasedSecurityAnalyzer (fast, no LLM) vs LLMSecurityAnalyzer (smarter, costs tokens) vs CompositeSecurityAnalyzer (both)
4. **Step 2 — Configure a policy**: NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold(SecurityRisk.MEDIUM)
5. **Step 3 — Wire into LoopConfig**: Pass analyzer + policy to LoopConfig
6. **Step 4 — Combine with permissions**: Use AllowList/DenyList alongside security
7. **Complete example**: Full working script

**Step 2: Commit**
```bash
git add docs/guides/add-security-policies.md
git commit -m "docs: add security policies guide"
```

---

### Task 18: Guide — Build a Plugin

**Files:**
- Create: `docs/guides/build-a-plugin.md`

**Step 1: Write guide**

Sections:
1. **Goal**: Create a Chimera plugin that adds tools and agent presets
2. **Prerequisites**: Understanding of BaseTool
3. **Step 1 — Plugin class**: Subclass BasePlugin, implement activate/deactivate
4. **Step 2 — Register tools**: Use ComponentRegistry to register custom tools
5. **Step 3 — Directory layout**: agents/*.md for agent definitions, .mcp.json for MCP servers, hooks/ for shell hooks
6. **Step 4 — Load with PluginManager**: PluginManager.load(), discover from directory
7. **Step 5 — Extension registry**: Register strategies, constraints, middleware, skills via PluginExtensionRegistry
8. **Step 6 — Marketplace**: PluginInfo, publish to MarketplaceRegistry
9. **Complete example**: Full plugin with tool + agent + hook

**Step 2: Commit**
```bash
git add docs/guides/build-a-plugin.md
git commit -m "docs: add plugin development guide"
```

---

### Task 19: Guide — Connect External Agents (ACP)

**Files:**
- Create: `docs/guides/connect-external-agents.md`

**Step 1: Write guide**

Sections:
1. **Goal**: Connect external agents (any language/framework) to Chimera via ACP
2. **Prerequisites**: External agent with JSON-RPC 2.0 stdio interface
3. **Step 1 — ACPSessionConfig**: Set command, args, env, timeout
4. **Step 2 — ACPClient**: Start session, send messages, get responses
5. **Step 3 — ExternalAgentTool**: Wrap client as a Chimera tool
6. **Step 4 — Use in Supervisor**: Add ExternalAgentTool to Supervisor workers
7. **Protocol reference**: JSON-RPC methods (session.start, session.message, session.stop, session.fork)
8. **Complete example**: Python external agent + Chimera supervisor

**Step 2: Commit**
```bash
git add docs/guides/connect-external-agents.md
git commit -m "docs: add external agents (ACP) guide"
```

---

### Task 20: Guide — Automate CI Fixes

**Files:**
- Create: `docs/guides/automate-ci-fixes.md`

**Step 1: Write guide**

Sections:
1. **Goal**: Automatically diagnose and fix CI failures
2. **Prerequisites**: CI log file, working agent setup
3. **Step 1 — Parse CI log**: CIFixWorkflow.diagnose() → list[FailureInfo]
4. **Step 2 — Configure workflow**: max_attempts, budget limits
5. **Step 3 — Run with agent**: workflow.run(log, agent, env) with retry loop
6. **Step 4 — Branch isolation**: Combine with GitWorkflow for branch isolation
7. **Step 5 — CLI shortcut**: `chimera ci-fix --log build.log --max-attempts 3`
8. **Complete example**: Full CI fix pipeline script

**Step 2: Commit**
```bash
git add docs/guides/automate-ci-fixes.md
git commit -m "docs: add CI fix automation guide"
```

---

### Task 21: Final commit and verify

**Step 1: Run mkdocs build to verify**
```bash
pip install -e ".[docs]" && mkdocs build 2>&1
```

Check for broken links or missing pages. Fix any issues.

**Step 2: Final commit if any fixes needed**
```bash
git add -A docs/ mkdocs.yml README.md
git commit -m "docs: fix any build issues from mkdocs build"
```
