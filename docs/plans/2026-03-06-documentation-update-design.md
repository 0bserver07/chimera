# Documentation Update Design

**Date:** 2026-03-06
**Goal:** Update all Chimera documentation to reflect the current 8-layer / 1712-test / 32-module state. Serve both human users and coding agents.

## Approach: Layered Update

Three independently committable tiers.

## Tier 1 — Fix the Lies (~10 files)

Update factually wrong content:

| File | Changes |
|------|---------|
| `README.md` | 8-layer stack, 1712 tests, 16 tools, 6 envs, updated features/roadmap |
| `docs/architecture.md` | 8-layer Mermaid, Infrastructure + Workflows layers, updated dependency map |
| `docs/index.md` | Correct stats table (16 tools, 7 strategies, 6 envs), add Workflows/Infrastructure rows |
| `docs/modules/index.md` | Expand from 8 to ~18 modules |
| `mkdocs.yml` | Add nav entries for all new pages |

## Tier 2 — Fill the Gaps (~30 files)

### New module pages (`docs/modules/`)

Each follows: purpose paragraph → quick example → key classes with imports → integration points.

- `security.md` — SecurityRisk, Analyzers, ConfirmationPolicy
- `secrets.md` — SecretRegistry, SecretDetector, RedactionMiddleware
- `critic.md` — Critic ABC, LLMCritic, ChecklistCritic, CriticMixin
- `acp.md` — ACPClient, ExternalAgentTool, JSON-RPC protocol
- `plugins.md` — BasePlugin, PluginManager, PluginExtensionRegistry, DirectoryPluginLoader, Marketplace
- `config.md` — DiscriminatedUnion, ChimeraConfig, ProjectConfig
- `checkpoints.md` — CheckpointManager create/restore/undo
- `cost-tracking.md` — CostTracker, TokenUsage, StepUsage, budgets
- `mcp.md` — MCPClient (stdio/HTTP), MCPToolSource
- `lsp.md` — LSP client, diagnostics, completion, rename
- `cli.md` — 11 subcommands, REPL, 14 slash commands

### Workflows section (`docs/workflows/`)

- `index.md` — Workflows layer overview, umbrella imports
- `ci-fix.md` — CIFixWorkflow
- `review.md` — ReviewOrchestrator
- `research.md` — Researcher
- `migration.md` — MigrationPlanner
- `docgen.md` — DocGenerator
- `testgen.md` — TestGenerator

### Reference pages (`docs/reference/`)

Mkdocstrings autodoc pages for each new module:
- `security.md`, `secrets.md`, `critic.md`, `acp.md`, `plugins.md`, `config.md`, `checkpoints.md`, `cost-tracking.md`, `mcp.md`, `lsp.md`, `workflows.md`

## Tier 3 — New Guides (~5 files)

Practical how-to guides in `docs/guides/`:

- `use-the-repl.md` — chimera code, slash commands, session management
- `add-security-policies.md` — SecurityAnalyzer + ConfirmationPolicy + LoopConfig
- `build-a-plugin.md` — BasePlugin, directory layout, marketplace
- `connect-external-agents.md` — ACP client, ExternalAgentTool, composition
- `automate-ci-fixes.md` — CIFixWorkflow end-to-end with GitWorkflow

Each guide: Goal → Prerequisites → Step-by-step → Complete example → Next steps.

## Audience

- **Human users**: Getting-started, concepts, guides, API reference via mkdocs site
- **Coding agents**: CLAUDE.md (already current), module docs with clear import paths, consistent structure

## Total scope

~10 updated files + ~35 new files across 3 committable tiers.
