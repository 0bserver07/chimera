# Chimera Documentation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a complete MkDocs Material documentation site for Chimera covering landing page, getting started, 6 concept pages, 8 module pages, 4 how-to guides, 17 auto-generated API reference pages, architecture diagrams, CLAUDE.md, and critical docstring improvements.

**Architecture:** MkDocs Material with mkdocstrings[python] for auto-generated API docs and mkdocs-mermaid2-plugin for inline diagrams. All docs live under `docs/`. Config in `mkdocs.yml` at project root. Docstrings improved to Google-style for the 6 most critical files.

**Tech Stack:** mkdocs-material, mkdocstrings[python], mkdocs-mermaid2-plugin, griffe (mkdocstrings backend)

---

### Task 1: Install dependencies and create mkdocs.yml

**Files:**
- Modify: `pyproject.toml:19-23`
- Create: `mkdocs.yml`

**Step 1: Add docs dependency group to pyproject.toml**

Add after line 23 (`dev = [...]`):

```toml
docs = [
    "mkdocs-material>=9.5",
    "mkdocstrings[python]>=0.24",
    "mkdocs-mermaid2-plugin>=1.1",
]
```

Also update the `all` line to: `all = ["chimera-run[anthropic,openai]"]` (unchanged).

**Step 2: Create mkdocs.yml**

```yaml
site_name: Chimera
site_description: A composable coding agent framework. Synthesize codebases from specifications.
site_url: ""
repo_url: https://github.com/your-username/chimera
repo_name: chimera

theme:
  name: material
  palette:
    - scheme: default
      primary: deep purple
      accent: amber
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    - scheme: slate
      primary: deep purple
      accent: amber
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - navigation.top
    - search.suggest
    - search.highlight
    - content.code.copy
    - content.code.annotate
  icon:
    repo: fontawesome/brands/github

plugins:
  - search
  - mkdocstrings:
      default_handler: python
      handlers:
        python:
          options:
            docstring_style: google
            show_source: true
            show_root_heading: true
            show_root_full_path: false
            members_order: source
            merge_init_into_class: true
            show_if_no_docstring: false
  - mermaid2

markdown_extensions:
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.snippets
  - admonition
  - pymdownx.details
  - attr_list
  - md_in_html
  - toc:
      permalink: true

nav:
  - Home: index.md
  - Getting Started: getting-started.md
  - Concepts:
    - concepts/index.md
    - Agents: concepts/agents.md
    - Providers: concepts/providers.md
    - Tools: concepts/tools.md
    - Loops: concepts/loops.md
    - Environments: concepts/environments.md
    - Training: concepts/training.md
  - Modules:
    - modules/index.md
    - Events: modules/events.md
    - Compaction: modules/compaction.md
    - Detection: modules/detection.md
    - Permissions: modules/permissions.md
    - Streaming: modules/streaming.md
    - Sessions: modules/sessions.md
    - Auth: modules/auth.md
    - Agent Config: modules/agents-config.md
  - Guides:
    - guides/index.md
    - Build a Coding Agent: guides/build-a-coding-agent.md
    - Add a Custom Tool: guides/add-custom-tool.md
    - Compose Agents: guides/compose-agents.md
    - Configure Permissions: guides/configure-permissions.md
  - API Reference:
    - reference/index.md
    - Core: reference/core.md
    - Providers: reference/providers.md
    - Tools: reference/tools.md
    - Environments: reference/env.md
    - Training: reference/training.md
    - Composition: reference/composition.md
    - Evaluation: reference/eval.md
    - Events: reference/events.md
    - Compaction: reference/compaction.md
    - Detection: reference/detection.md
    - Permissions: reference/permissions.md
    - Streaming: reference/streaming.md
    - Sessions: reference/sessions.md
    - Auth: reference/auth.md
    - Agents: reference/agents.md
    - Types: reference/types.md
  - Architecture: architecture.md
```

**Step 3: Install docs dependencies and verify mkdocs runs**

Run: `pip install -e ".[docs]"`
Run: `mkdocs serve --no-livereload`
Expected: Server starts, shows default page (will error on missing docs pages — that's fine)

**Step 4: Commit**

```bash
git add mkdocs.yml pyproject.toml
git commit -m "docs: add mkdocs-material configuration and docs dependencies"
```

---

### Task 2: Create CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

**Step 1: Create CLAUDE.md at project root**

```markdown
# Chimera

A composable coding agent framework. Synthesize codebases from specifications.

## Quick Reference

- **Language:** Python 3.11+
- **Build:** hatchling
- **License:** MIT
- **Tests:** `python -m pytest` (823 tests)
- **Lint:** `ruff check chimera/`
- **Types:** `mypy chimera/`
- **Docs:** `pip install -e ".[docs]" && mkdocs serve`

## Architecture

6-layer stack (each layer usable independently):

```
Layer 6: CLI           chimera synthesize / chimera eval / chimera bench
Layer 5: Synthesis     Trainer, Strategy, Spec, Architecture, Constraint
Layer 4: Evaluation    Harness, Metrics, Benchmarks
Layer 3: Agent         Agent, Tools, Loops, Prompt, Context
Layer 2: Provider      Anthropic, OpenAI, Google, Ollama, OpenAI-compat
Layer 1: Environment   Local, Docker, Git, persistent shell (tmux)
```

## Module Map

### Core (`chimera/core/`)
- `agent.py` — Agent class, main entry point
- `context.py` — Conversation history manager
- `loop.py` — ReAct loop (reason-act-observe)
- `loop_config.py` — LoopConfig dataclass (permissions, detection, events, etc.)
- `tool_executor.py` — Shared tool execution with permission/event/detection hooks
- `prompt.py` — System prompt with variable substitution
- `tool.py` — BaseTool ABC and @tool decorator
- `tool_group.py` — ToolGroup and DEFAULT_TOOLS
- `loops/` — PlanAndExecute, Reflexion, TreeOfThought

### Providers (`chimera/providers/`)
- `base.py` — Provider ABC, Response, StreamEvent
- `factory.py` — `create_provider()` auto-detection
- `anthropic.py`, `openai_provider.py`, `google.py`, `ollama.py`, `modal.py`
- `cost.py` — Per-model pricing and cost calculation

### Tools (`chimera/tools/`)
13 built-in tools: read, write, edit, bash, search, list_files, test, git, web_fetch, replace_in_file, verify, delegate, repo_map

### Environments (`chimera/env/`)
- `base.py` — Environment ABC
- `local.py` — LocalEnvironment (filesystem)
- `git.py` — GitEnvironment (branch isolation)
- `docker.py` — DockerEnvironment (container isolation)
- `shell.py` — PersistentShell (tmux sessions)

### Training (`chimera/training/`)
- `trainer.py` — Trainer orchestrator
- `spec.py` — Spec (task specification)
- `architecture.py` — Architecture (multi-layer builds)
- `strategies/` — TestConvergence, TreeSearch, Curriculum, Ensemble, MajorityVoting, AIMOEnsemble, Passthrough

### Composition (`chimera/composition/`)
- Pipeline (sequential), Ensemble (parallel), Supervisor (coordinator + workers)

### Evaluation (`chimera/eval/`)
- Harness, Benchmark ABC, metrics (pass@k, resolve_rate, avg_cost)

### Extension Modules (new)
- `chimera/events/` — EventBus, 9 event types, middleware
- `chimera/compaction/` — Token counting, pruning, LLM summarization
- `chimera/detection/` — Loop detection (exact repeat, pattern cycle)
- `chimera/permissions/` — Rule-based permission policies
- `chimera/streaming/` — Stream handlers, StreamingReAct
- `chimera/sessions/` — Multi-turn persistence (memory, file, SQLite)
- `chimera/auth/` — API key, OAuth device/browser flows, credential store
- `chimera/agents/` — AgentConfig, presets (Build, Plan, Explore, General, Review), registry

## Key Conventions

- **Zero-dependency core.** Only stdlib in main package. Providers are optional extras.
- **TYPE_CHECKING imports.** Use `if TYPE_CHECKING:` for cross-module type hints to avoid circular imports.
- **3-tier API.** Every feature has: one-liner convenience, developer configuration, framework-author subclassing.
- **LoopConfig pattern.** All loop-level features (permissions, detection, compaction, streaming, events) funnel through a single `LoopConfig` dataclass injected into loop constructors. When `None`, behavior is unchanged.
- **Google-style docstrings.** Use Args/Returns/Raises sections.
- **Tests mirror source.** `chimera/foo/bar.py` → `tests/test_bar.py` or `tests/test_foo.py`.
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md project context file"
```

---

### Task 3: Create landing page and update getting-started

**Files:**
- Create: `docs/index.md`
- Modify: `docs/getting-started.md`

**Step 1: Create docs/index.md**

```markdown
---
hide:
  - navigation
  - toc
---

# Chimera

**A composable coding agent framework.** Synthesize codebases from specifications.

---

Chimera is a Python framework that treats code synthesis as machine learning. Write a spec, let agents iterate, get a codebase that passes all tests.

## Three Levels of Control

=== "One-Liner"

    ```python
    import chimera

    result = chimera.synthesize("Build a REST API for tasks", tests="./tests/")
    ```

=== "Configured"

    ```python
    import chimera

    trainer = chimera.Trainer(
        architecture=chimera.Architecture(layers=[
            chimera.Layer("api", deps=[]),
            chimera.Layer("db", deps=["api"]),
        ]),
        spec=chimera.Spec.from_tests("./tests/", "Build a task manager"),
        agent=chimera.Agent(provider=chimera.create_provider("claude-sonnet-4")),
    )
    result = trainer.synthesize(strategy=chimera.TestConvergence(max_epochs=10))
    ```

=== "Framework Author"

    ```python
    import chimera

    class MyAgent(chimera.Agent):
        tools = chimera.DEFAULT_TOOLS
        loop = chimera.ReAct(max_steps=50)

    class MyStrategy(chimera.Strategy):
        def run(self, agent, spec, env, constraints=None, callbacks=None):
            # Your custom synthesis loop
            ...
    ```

## Install

```bash
pip install chimera-run                  # core (zero dependencies)
pip install chimera-run[anthropic]       # + Claude support
pip install chimera-run[openai]          # + OpenAI support
pip install chimera-run[all]             # all providers
```

Requires Python 3.11+.

## What's Inside

| Feature | Details |
|---------|---------|
| **6 LLM Providers** | Anthropic, OpenAI, Google, Ollama, Modal, any OpenAI-compatible |
| **13 Built-in Tools** | File I/O, bash, git, search, test runner, web fetch, repo map |
| **4 Loop Types** | ReAct, PlanAndExecute, Reflexion, TreeOfThought |
| **3 Composition Patterns** | Pipeline, Ensemble, Supervisor |
| **8 Training Strategies** | TestConvergence, TreeSearch, Curriculum, MajorityVoting, and more |
| **8 Extension Modules** | Events, Permissions, Sessions, Auth, Streaming, Detection, Compaction, Agent Config |

<div class="grid cards" markdown>

-   :material-book-open-variant:{ .lg .middle } **Getting Started**

    ---

    Install Chimera and build your first agent in 5 minutes.

    [:octicons-arrow-right-24: Get started](getting-started.md)

-   :material-cube-outline:{ .lg .middle } **Core Concepts**

    ---

    Agents, Providers, Tools, Loops, Environments, Training.

    [:octicons-arrow-right-24: Learn concepts](concepts/index.md)

-   :material-puzzle-outline:{ .lg .middle } **Extension Modules**

    ---

    Events, Permissions, Sessions, Auth, and more.

    [:octicons-arrow-right-24: Explore modules](modules/index.md)

-   :material-code-braces:{ .lg .middle } **API Reference**

    ---

    Auto-generated from source. Every class, function, type.

    [:octicons-arrow-right-24: Browse API](reference/index.md)

</div>
```

**Step 2: Update docs/getting-started.md**

Rewrite the existing file with proper MkDocs formatting, keeping the same content but adding admonitions, tabs, and better structure. Keep the existing provider setup info, add a "Your First Agent" section with a complete runnable example.

**Step 3: Commit**

```bash
git add docs/index.md docs/getting-started.md
git commit -m "docs: add landing page and update getting-started guide"
```

---

### Task 4: Create architecture page with Mermaid diagrams

**Files:**
- Create: `docs/architecture.md`

**Step 1: Create docs/architecture.md**

The page should contain 4 Mermaid diagrams:

1. **Layer Stack** — 6-layer architecture (CLI → Synthesis → Evaluation → Agent → Provider → Environment)
2. **Agent Loop Flow** — ReAct with LoopConfig showing: Task → Provider.complete → Tool calls? → Permission Check → Execute Tool → Emit Events → Loop Detection → repeat or return
3. **Module Dependency Map** — How the 8 new extension modules connect through LoopConfig, Sessions, Auth, AgentConfig
4. **Training Pipeline** — Spec → Strategy → Agent → Environment → Evaluation → iterate

Each diagram should have a brief explanatory paragraph above it.

**Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: add architecture page with Mermaid diagrams"
```

---

### Task 5: Create concepts pages

**Files:**
- Create: `docs/concepts/index.md`
- Create: `docs/concepts/agents.md`
- Create: `docs/concepts/providers.md`
- Create: `docs/concepts/tools.md`
- Create: `docs/concepts/loops.md`
- Create: `docs/concepts/environments.md`
- Create: `docs/concepts/training.md`

**Step 1: Create docs/concepts/index.md**

Overview page listing the 6 core concepts with brief descriptions and links. One paragraph intro explaining Chimera's layered architecture.

**Step 2: Create docs/concepts/agents.md**

Cover: What an Agent is, Agent lifecycle (`__init__` → `run()` → AgentResult), Prompt and Context, the three API tiers (one-liner, configured, subclass). Include code examples at each tier. Reference `chimera.core.agent`.

**Step 3: Create docs/concepts/providers.md**

Cover: Provider abstraction, `create_provider()` factory with auto-detection, supported providers table (Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compat), env var configuration, Response dataclass, cost tracking. Include code examples.

**Step 4: Create docs/concepts/tools.md**

Cover: BaseTool ABC, `@tool` decorator for quick tools, ToolGroup, DEFAULT_TOOLS, the 13 built-in tools table (name, what it does), how to write a custom tool. Include code examples for both class-based and decorator-based tools.

**Step 5: Create docs/concepts/loops.md**

Cover: What a loop is (the agent's execution strategy), ReAct (default), PlanAndExecute (plan then execute), Reflexion (act-reflect-repeat), TreeOfThought (candidates + evaluation). Show how to choose between them. Explain LoopConfig injection. Include code examples.

**Step 6: Create docs/concepts/environments.md**

Cover: Environment ABC, LocalEnvironment (filesystem), GitEnvironment (branch isolation), DockerEnvironment (container isolation), PersistentShell (tmux), SessionMixin. Context manager usage. Include code examples.

**Step 7: Create docs/concepts/training.md**

Cover: The ML analogy (spec=loss, iteration=training, code=model), Spec (from_string, from_tests, from_file), Architecture (layers + dependencies), Trainer.synthesize(), Strategy ABC, the 8 built-in strategies table, Constraints, Callbacks. Include the full configured example from README.

**Step 8: Commit**

```bash
git add docs/concepts/
git commit -m "docs: add 6 core concept pages"
```

---

### Task 6: Create module pages for the 8 extension modules

**Files:**
- Create: `docs/modules/index.md`
- Create: `docs/modules/events.md`
- Create: `docs/modules/compaction.md`
- Create: `docs/modules/detection.md`
- Create: `docs/modules/permissions.md`
- Create: `docs/modules/streaming.md`
- Create: `docs/modules/sessions.md`
- Create: `docs/modules/auth.md`
- Create: `docs/modules/agents-config.md`

**Step 1: Create docs/modules/index.md**

Overview page explaining the 8 extension modules added to support interactive coding agents. Brief description of each with links. Explain that all loop-level features funnel through LoopConfig. Include the module dependency Mermaid diagram.

**Step 2: Create docs/modules/events.md**

Cover: EventBus (pub/sub), Event base class, 9 event types (ToolCallEvent, ToolResultEvent, StepEvent, TextDeltaEvent, ErrorEvent, LoopDetectedEvent, CompactionEvent, PermissionEvent, SessionEvent), Middleware (LoggingMiddleware, FilterMiddleware), `bus.on()` decorator. Include Mermaid flow diagram of event lifecycle. Code examples for subscribing, publishing, middleware.

**Step 3: Create docs/modules/compaction.md**

Cover: CompactionStrategy ABC, TokenCounter (tiktoken or heuristic), PruneCompaction (truncate large tool outputs), SummaryCompaction (LLM-powered), CompositeCompaction (chain strategies). Include Mermaid diagram of compaction pipeline. Code examples.

**Step 4: Create docs/modules/detection.md**

Cover: DetectionStrategy ABC, DetectionResult, ExactRepeatDetector (MD5), PatternCycleDetector (A-B-A-B), CompositeDetector, LoopDetector facade, OnDetect enum (ASK/BREAK/WARN). Code examples.

**Step 5: Create docs/modules/permissions.md**

Cover: PermissionPolicy ABC, PermissionAction enum (ALLOW/DENY/ASK), Rule dataclass, PermissionRuleset (ordered rules, last-match-wins), pattern matching (fnmatch), presets (AutoApprove, AlwaysDeny, ReadOnly, Interactive, AllowList). Include Mermaid diagram of permission evaluation flow. Code examples.

**Step 6: Create docs/modules/streaming.md**

Cover: StreamHandler ABC (on_text_delta, on_tool_call, on_tool_result, on_step_start, on_step_end), ConsoleStreamHandler, CollectStreamHandler, NullStreamHandler, StreamingProvider protocol, StreamingReAct loop variant. Code examples.

**Step 7: Create docs/modules/sessions.md**

Cover: Session class (chat, fork, resume, save), SessionData, Storage ABC, InMemoryStorage, FileStorage, SQLiteStorage. Include Mermaid diagram of session lifecycle (create → chat → save → resume/fork). Code examples for each storage backend.

**Step 8: Create docs/modules/auth.md**

Cover: AuthProvider ABC, Credential dataclass (with expiry), APIKeyAuth (env var + stored key), OAuthDeviceFlow (RFC 8628), OAuthBrowserFlow (PKCE), CredentialStore (JSON, 0o600), AuthManager facade (login/get_token/logout). Include Mermaid diagram of OAuth device flow. Code examples.

**Step 9: Create docs/modules/agents-config.md**

Cover: AgentConfig dataclass, from_markdown() parser (YAML frontmatter), build() method, tool/loop/permission registries, AgentRegistry (discover + register + load_directory), 5 preset agents (BuildAgent, PlanAgent, ExploreAgent, GeneralAgent, ReviewAgent) with their tool sets. Code examples for markdown agent definition and registry usage.

**Step 10: Commit**

```bash
git add docs/modules/
git commit -m "docs: add 8 extension module pages"
```

---

### Task 7: Create how-to guide pages

**Files:**
- Create: `docs/guides/index.md`
- Create: `docs/guides/build-a-coding-agent.md`
- Create: `docs/guides/add-custom-tool.md`
- Create: `docs/guides/compose-agents.md`
- Create: `docs/guides/configure-permissions.md`

**Step 1: Create docs/guides/index.md**

Overview page listing the 4 guides with descriptions.

**Step 2: Create docs/guides/build-a-coding-agent.md**

End-to-end guide: install → create provider → create agent with tools → add LoopConfig (permissions + events + detection) → create session → multi-turn chat → save/resume. Complete runnable code from start to finish. This is the flagship guide.

**Step 3: Create docs/guides/add-custom-tool.md**

Two approaches: `@tool` decorator (simple) and BaseTool subclass (full control). Show how to define schema, execute, handle errors, register in ToolGroup, use in an Agent. Include testing the tool.

**Step 4: Create docs/guides/compose-agents.md**

Show Pipeline (sequential agents), Ensemble (parallel + selector), Supervisor (coordinator + workers). Real examples: Pipeline for plan-then-code, Ensemble for best-of-3, Supervisor for multi-file tasks.

**Step 5: Create docs/guides/configure-permissions.md**

Show permission rules: allow read tools, deny dangerous bash commands, ask for everything else. PermissionRuleset with glob patterns. Presets (ReadOnly, Interactive). Integration via LoopConfig. Event monitoring with EventBus.

**Step 6: Commit**

```bash
git add docs/guides/
git commit -m "docs: add 4 how-to guides"
```

---

### Task 8: Create API reference stub pages

**Files:**
- Create: `docs/reference/index.md`
- Create: `docs/reference/core.md`
- Create: `docs/reference/providers.md`
- Create: `docs/reference/tools.md`
- Create: `docs/reference/env.md`
- Create: `docs/reference/training.md`
- Create: `docs/reference/composition.md`
- Create: `docs/reference/eval.md`
- Create: `docs/reference/events.md`
- Create: `docs/reference/compaction.md`
- Create: `docs/reference/detection.md`
- Create: `docs/reference/permissions.md`
- Create: `docs/reference/streaming.md`
- Create: `docs/reference/sessions.md`
- Create: `docs/reference/auth.md`
- Create: `docs/reference/agents.md`
- Create: `docs/reference/types.md`

**Step 1: Create docs/reference/index.md**

```markdown
# API Reference

Auto-generated from source code docstrings.

Browse by module:

| Module | Description |
|--------|-------------|
| [Core](core.md) | Agent, Context, Loops, Tools, Prompt |
| [Providers](providers.md) | LLM providers and factory |
| [Tools](tools.md) | Built-in agent tools |
| [Environments](env.md) | Execution environments |
| [Training](training.md) | Trainer, Spec, Strategy, Architecture |
| [Composition](composition.md) | Pipeline, Ensemble, Supervisor |
| [Evaluation](eval.md) | Harness, Benchmark, metrics |
| [Events](events.md) | EventBus and event types |
| [Compaction](compaction.md) | Context compaction strategies |
| [Detection](detection.md) | Loop detection |
| [Permissions](permissions.md) | Permission policies |
| [Streaming](streaming.md) | Stream handlers |
| [Sessions](sessions.md) | Multi-turn persistence |
| [Auth](auth.md) | Authentication |
| [Agents](agents.md) | Agent configuration and presets |
| [Types](types.md) | Shared type definitions |
```

**Step 2: Create all 16 reference pages**

Each page follows this template (example for events):

```markdown
# chimera.events

::: chimera.events
    options:
      show_submodules: true
```

For modules with submodules that need explicit listing (like `chimera.core` which has loops, tool_executor, etc.), list each submodule:

```markdown
# chimera.core

::: chimera.core.agent

::: chimera.core.context

::: chimera.core.loop

::: chimera.core.loop_config

::: chimera.core.tool

::: chimera.core.tool_executor

::: chimera.core.prompt

::: chimera.core.tool_group
```

Reference pages for all 16 modules:
- `core.md` — chimera.core.agent, context, loop, loop_config, tool, tool_executor, prompt, tool_group
- `providers.md` — chimera.providers (with show_submodules)
- `tools.md` — chimera.tools (with show_submodules)
- `env.md` — chimera.env (with show_submodules)
- `training.md` — chimera.training (with show_submodules)
- `composition.md` — chimera.composition (with show_submodules)
- `eval.md` — chimera.eval (with show_submodules)
- `events.md` — chimera.events (with show_submodules)
- `compaction.md` — chimera.compaction (with show_submodules)
- `detection.md` — chimera.detection (with show_submodules)
- `permissions.md` — chimera.permissions (with show_submodules)
- `streaming.md` — chimera.streaming (with show_submodules)
- `sessions.md` — chimera.sessions (with show_submodules)
- `auth.md` — chimera.auth (with show_submodules)
- `agents.md` — chimera.agents (with show_submodules)
- `types.md` — chimera.types

**Step 3: Commit**

```bash
git add docs/reference/
git commit -m "docs: add 17 API reference stub pages (mkdocstrings)"
```

---

### Task 9: Improve critical docstrings for API reference quality

**Files:**
- Modify: `chimera/core/agent.py`
- Modify: `chimera/core/tool.py`
- Modify: `chimera/providers/base.py`
- Modify: `chimera/providers/factory.py`
- Modify: `chimera/composition/pipeline.py`
- Modify: `chimera/composition/supervisor.py`
- Modify: `chimera/composition/ensemble.py`
- Modify: `chimera/training/trainer.py`
- Modify: `chimera/training/spec.py`
- Modify: `chimera/eval/harness.py`
- Modify: `chimera/env/base.py`
- Modify: `chimera/core/context.py`

The docstring survey found 0/12 module docstrings, most methods lack Args/Returns sections. For each file:

1. Add module-level docstring
2. Add/improve class docstrings
3. Add Google-style Args/Returns/Raises to `__init__`, `run()`, `execute()`, factory methods
4. Add brief Examples where helpful

Use this format:

```python
"""Brief one-line description.

Longer description if needed.

Args:
    param1: Description.
    param2: Description.

Returns:
    Description of return value.

Raises:
    ValueError: When something is wrong.

Example:
    ```python
    agent = Agent(provider=provider, tools=[bash])
    result = agent.run("Fix the bug", env=env)
    ```
"""
```

Priority order (most impactful for API docs):
1. `chimera/core/agent.py` — Agent.__init__, Agent.run()
2. `chimera/providers/base.py` — Provider ABC, Response, complete()
3. `chimera/providers/factory.py` — create_provider() (already has Args, add Returns)
4. `chimera/core/tool.py` — BaseTool, tool() decorator, execute()
5. `chimera/core/context.py` — Context.__init__, add(), to_messages()
6. `chimera/training/trainer.py` — Trainer.__init__, synthesize()
7. `chimera/training/spec.py` — Spec, from_string(), from_tests(), to_prompt()
8. `chimera/composition/pipeline.py` — Pipeline.run()
9. `chimera/composition/supervisor.py` — Supervisor.run()
10. `chimera/composition/ensemble.py` — Ensemble.run(), best()
11. `chimera/eval/harness.py` — Harness.run(), Benchmark abstract methods
12. `chimera/env/base.py` — Environment ABC methods (already decent, add Returns)

**Step 1: Improve docstrings in all 12 files**

**Step 2: Run tests to verify no regressions**

Run: `python -m pytest tests/ -x -q`
Expected: All 823 tests pass

**Step 3: Commit**

```bash
git add chimera/
git commit -m "docs: improve docstrings across 12 core files for API reference"
```

---

### Task 10: Build and verify the full docs site

**Step 1: Build the docs**

Run: `mkdocs build --strict 2>&1`
Expected: Build succeeds with no errors. Warnings about missing cross-references are acceptable.

**Step 2: Serve locally and verify**

Run: `mkdocs serve`
Check: Navigate tabs (Home, Getting Started, Concepts, Modules, Guides, API Reference, Architecture). Verify:
- Landing page renders with tabs and cards
- Mermaid diagrams render in Architecture page
- API reference pages show auto-generated content from docstrings
- Navigation works, search works
- Dark/light mode toggle works

**Step 3: Fix any build issues**

Common issues:
- mkdocstrings can't find a module → check `chimera/` is importable from project root
- Mermaid syntax errors → check fence format
- Navigation mismatches → check file paths in mkdocs.yml match actual files

**Step 4: Final commit**

```bash
git add -A
git commit -m "docs: complete Chimera documentation site (mkdocs-material)"
```

---

## Summary

| Task | Pages | Description |
|------|-------|-------------|
| 1 | 0 | mkdocs.yml + pyproject.toml deps |
| 2 | 0 | CLAUDE.md |
| 3 | 2 | Landing page + getting started |
| 4 | 1 | Architecture with 4 Mermaid diagrams |
| 5 | 7 | 6 concept pages + index |
| 6 | 9 | 8 module pages + index |
| 7 | 5 | 4 guide pages + index |
| 8 | 17 | API reference stubs (auto-generated) |
| 9 | 0 | Docstring improvements (12 source files) |
| 10 | 0 | Build, verify, fix |
| **Total** | **41 pages** | |
