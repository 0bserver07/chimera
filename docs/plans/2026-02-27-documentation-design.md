# Chimera Documentation Design

**Date:** 2026-02-27
**Status:** Approved

## Decisions

- **Audience:** Developers using Chimera as a library to build coding agents
- **Stack:** MkDocs Material + mkdocstrings[python] + mkdocs-mermaid2-plugin
- **API docs:** Auto-generated from docstrings via mkdocstrings
- **Diagrams:** Mermaid (inline in markdown)
- **Hosting:** Local only for now (`mkdocs serve`), GitHub Pages later

## Site Structure

```
docs/
  index.md                    # Hero landing page
  getting-started.md          # Install + first agent in 5 minutes (exists, update)

  concepts/
    index.md                  # Core Concepts overview
    agents.md                 # Agent, Prompt, Context lifecycle
    providers.md              # Provider abstraction, model selection
    tools.md                  # BaseTool, tool decorator, ToolGroup
    loops.md                  # ReAct, PlanAndExecute, Reflexion, TreeOfThought
    environments.md           # LocalEnvironment, GitEnvironment, Docker
    training.md               # Spec -> Strategy -> Trainer pipeline

  modules/
    index.md                  # Extension Modules overview
    events.md                 # EventBus, event types, middleware
    compaction.md             # Token counting, pruning, summarization
    detection.md              # Loop detection strategies
    permissions.md            # Rules, policies, presets
    streaming.md              # Stream handlers, StreamingReAct
    sessions.md               # Multi-turn persistence, storage backends
    auth.md                   # API keys, OAuth flows, credential store
    agents-config.md          # AgentConfig, presets, registry

  guides/
    index.md                  # How-To Guides overview
    build-a-coding-agent.md   # End-to-end: build an interactive agent
    add-custom-tool.md        # Create and register a tool
    compose-agents.md         # Pipeline, Ensemble, Supervisor
    configure-permissions.md  # Permission rules for safe execution

  reference/                  # Auto-generated (mkdocstrings)
    index.md                  # API Reference with module listing
    core.md                   # chimera.core
    providers.md              # chimera.providers
    tools.md                  # chimera.tools
    env.md                    # chimera.env
    training.md               # chimera.training
    composition.md            # chimera.composition
    eval.md                   # chimera.eval
    events.md                 # chimera.events
    compaction.md             # chimera.compaction
    detection.md              # chimera.detection
    permissions.md            # chimera.permissions
    streaming.md              # chimera.streaming
    sessions.md               # chimera.sessions
    auth.md                   # chimera.auth
    agents.md                 # chimera.agents
    types.md                  # chimera.types

  architecture.md             # Full system Mermaid diagrams
```

## Navigation Tabs

Home | Getting Started | Concepts | Modules | Guides | API Reference | Architecture

## Diagrams (Mermaid)

### 1. Layer Stack
6-layer Chimera architecture: CLI -> Synthesis -> Evaluation -> Agent -> Provider -> Environment

### 2. Agent Loop Flow
ReAct with LoopConfig: Task -> Provider.complete -> Tool calls? -> Permission Check -> Execute -> Events -> Loop Detection -> repeat

### 3. Module Dependency Map
How the 8 new modules connect through LoopConfig, Sessions, Auth, and AgentConfig.

### 4. Per-module diagrams
Inline in each modules/*.md page (storage flow, auth lifecycle, permission evaluation, etc.)

## Config Files

### mkdocs.yml
- Material theme with dark/light toggle
- mkdocstrings[python] pointed at chimera/
- mkdocs-mermaid2-plugin
- Navigation matching site map
- Search, code copy buttons

### pyproject.toml
New `docs` optional dependency group:
- mkdocs-material
- mkdocstrings[python]
- mkdocs-mermaid2-plugin

Install: `pip install chimera-ai[docs]`

### CLAUDE.md
- Project description and philosophy
- Module map (18 directories)
- Key conventions
- How to run tests, build docs

## Implementation Order

1. Config files (mkdocs.yml, pyproject.toml update, CLAUDE.md)
2. Landing page + getting started (update existing)
3. Architecture page with Mermaid diagrams
4. Concepts pages (6 pages)
5. Modules pages (8 pages)
6. Guides pages (4 pages)
7. API Reference pages (17 pages, mostly auto-generated stubs)
8. Improve docstrings where mkdocstrings output is thin
