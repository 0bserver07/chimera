# Agent Intelligence Design

**Date**: 2026-02-28
**Status**: Approved
**Goal**: Deepen existing layers with agent intelligence features — MCP client, production LSP, project config, provider catalog, fuzzy edits.

---

## Thesis

These features aren't new layers. They deepen the existing 6-layer stack. MCP is how Layer 3 gets external tools. LSP is how Layer 1 gets code intelligence. The catalog is how Layer 2 gets extensible. Config is how Layer 3 gets project-aware.

```
Layer 3: Agent
  chimera/mcp/        — MCP client as ToolSource (stdio + HTTP transports)
  chimera/config/     — Project config (AGENTS.md, skills, structured output)
  chimera/lsp/        — Rewrite: LSP tool + diagnostic injection
  chimera/tools/edit  — Fuzzy edit strategies (5-tier fallback)

Layer 2: Provider
  chimera/providers/catalog.py — Dynamic provider registry (model→config→Provider)
```

3-tier API preserved: one-liner, developer config, framework-author subclassing.

Zero new dependencies. Everything stdlib-only or reuses existing optional deps.

---

## Module 1: MCP Client (`chimera/mcp/`)

**Layer 3 — Agent.** MCP servers are tool sources. The MCP client discovers tools from external servers and wraps them as `BaseTool` instances.

### API

```python
from chimera.mcp import MCPClient, MCPToolSource

# One-liner
tools = MCPToolSource.from_stdio("npx", ["-y", "@modelcontextprotocol/server-filesystem"])

# Developer
client = MCPClient()
client.add_stdio("filesystem", "npx", ["-y", "@modelcontextprotocol/server-filesystem"])
client.add_stdio("github", "npx", ["-y", "@modelcontextprotocol/server-github"])
client.add_http("remote-db", "https://db.example.com/mcp", auth=oauth_token)
agent = Agent(provider=claude, tools=DEFAULT_TOOLS + client.tools, loop=ReAct())

# Framework author
class MyTransport(MCPTransport):
    async def send(self, message: dict) -> dict: ...
```

### Key Classes

- `MCPTransport` — ABC for stdio/HTTP/custom transports
- `MCPClient` — manages server connections, discovers tools/resources
- `MCPToolSource` — convenience wrapper, returns `list[BaseTool]`
- `MCPTool(BaseTool)` — wraps an MCP tool as a Chimera BaseTool
- `MCPResource` — wraps MCP resources as readable context

### Scope

Stdio transport + HTTP transport. Bearer token auth (no OAuth). Resource reading but not subscriptions.

---

## Module 2: LSP Integration (`chimera/lsp/`)

**Dual role: Layer 1 (Environment) + Layer 3 (Tool).** Rewrite the existing stub as a real LSP client.

### API

```python
from chimera.lsp import LSPManager, LSPTool

# One-liner
lsp = LSPManager.for_project("./myapp")  # auto-detects languages

# Developer
lsp = LSPManager()
lsp.add("python", "pyright-langserver", ["--stdio"])
lsp.add("typescript", "typescript-language-server", ["--stdio"])
lsp.add("go", "gopls", ["serve"])
lsp.add("rust", "rust-analyzer")

# Layer 1: diagnostics as environment state
lsp.start(workdir="./myapp")
diagnostics = lsp.get_diagnostics("src/auth.py")

# Layer 3: code intelligence as a tool
lsp_tool = LSPTool(lsp)
agent = Agent(provider=claude, tools=DEFAULT_TOOLS + [lsp_tool], loop=ReAct())

# Diagnostic injection via LoopConfig (opt-in)
loop_config = LoopConfig(lsp=lsp)

# Framework author
class MyLanguageServer(LanguageServerConfig):
    name = "my-lang"
    command = ["my-langserver", "--stdio"]
    extensions = (".mylang",)
```

### Key Classes

- `LanguageServerConfig` — dataclass: name, command, args, extensions, init options
- `LSPManager` — lifecycle management, starts/stops servers, routes by file extension
- `LSPSession` — single server connection (JSON-RPC over stdio)
- `LSPTool(BaseTool)` — exposes queries as tool calls (goToDefinition, findReferences, hover, documentSymbol, workspaceSymbol)
- `Diagnostic` — reuse existing dataclass, add richer fields

### Built-in Server Registry

Python (pyright), TypeScript (ts-server), Go (gopls), Rust (rust-analyzer). Auto-detected from file extensions. User must have servers on PATH (no auto-install).

### Diagnostic Injection

When configured via `LoopConfig(lsp=lsp)`, diagnostics are fetched after each tool call that modifies a file and appended to context. Opt-in, not automatic.

### Scope

Stdio transport only. Core LSP methods: initialize, textDocument/didOpen, didChange, didSave, publishDiagnostics, definition, references, hover, documentSymbol. No auto-install, no completion, no rename.

---

## Module 3: Project Config (`chimera/config/`)

**Layer 3 — Agent.** Loads project-level configuration: rules files, skills, structured output schemas.

### API

```python
from chimera.config import ProjectConfig, Skill, StructuredOutput

# One-liner
config = ProjectConfig.from_directory("./myapp")

# Feeds into Prompt
agent = Agent(
    provider=claude, tools=DEFAULT_TOOLS, loop=ReAct(),
    prompt=Prompt(system="You are a coding assistant.", project=config),
)

# Developer
config = ProjectConfig(
    rules=["Always use ruff for formatting"],
    rules_files=["AGENTS.md", ".cursor/rules"],
    skills_dirs=["skills/", ".claude/skills/"],
)
skill = config.get_skill("debugging")

# Structured output
schema = StructuredOutput(
    name="code_review",
    schema={"type": "object", "properties": {...}, "required": [...]},
)
response = agent.run(task="Review auth.py", output_schema=schema)

# Framework author
class VaultConfig(ConfigSource):
    def load(self) -> list[str]: ...
```

### Key Classes

- `ProjectConfig` — discovers and aggregates rules, skills, settings
- `ConfigSource` — ABC for loading rules from various sources
- `Skill` — dataclass: name, content, args, metadata (YAML frontmatter)
- `SkillRegistry` — discovers skills from directories, loads on-demand
- `StructuredOutput` — JSON schema wrapper with validation and retry (up to 3 attempts)

### Rules File Loading

Search order: `AGENTS.md` → `CLAUDE.md` → `.chimera/rules.md`. All found files concatenated. Markdown with optional YAML frontmatter.

### Skills Discovery

Walks `skills/` directories for `SKILL.md` files. YAML frontmatter for metadata. Loaded lazily.

### Structured Output

Wraps provider call with JSON schema constraint. If invalid JSON, retries with validation error in context. Provider-agnostic.

---

## Module 4: Provider Catalog (`chimera/providers/catalog.py`)

**Layer 2 — Provider.** Dynamic registry mapping model names to provider configurations.

### API

```python
from chimera.providers import ProviderCatalog, ModelConfig, create_provider

# One-liner (enhanced create_provider)
provider = create_provider("deepseek-chat")
provider = create_provider("bedrock/claude-sonnet-4")

# Developer
catalog = ProviderCatalog.default()
catalog.register(ModelConfig(
    model="my-company/internal-llm",
    provider_type="openai_compatible",
    base_url="https://llm.internal.corp/v1",
    api_key_env="INTERNAL_LLM_KEY",
    context_window=128_000,
    supports_tool_use=True,
    cost=(0.50, 1.50),
))
provider = catalog.create("my-company/internal-llm")

# From config file
catalog = ProviderCatalog.from_file("chimera.json")

# Framework author
catalog.register_provider_type("vllm", VLLMProvider)
```

### Key Classes

- `ModelConfig` — dataclass: model, provider_type, base_url, api_key_env, context_window, supports_tool_use, cost, extra kwargs
- `ProviderCatalog` — registry mapping model names → ModelConfig, creates Provider instances

### Built-in Catalog Entries

Existing: Anthropic (claude-*), OpenAI (gpt-*, o1, o3-*), Google (gemini-*), Ollama.
New: AWS Bedrock (`bedrock/*`), Azure OpenAI (`azure/*`), Groq (`groq/*`), DeepSeek (`deepseek-*`), GLM (`glm-*`).

Bedrock and Azure reuse `OpenAICompatibleProvider` with different auth/base URLs. No new Provider subclasses needed.

### Slash Routing

`bedrock/claude-sonnet-4` splits into namespace `bedrock` + model. Plain names use existing prefix matching first, catalog fallback second.

### Cost Integration

`ModelConfig.cost` auto-calls `register_model_cost()` on catalog load.

---

## Module 5: Fuzzy Edit Strategies (`chimera/tools/edit.py`)

**Layer 3 — Agent (Tools).** Add fallback matching strategies to the edit tool.

### API

```python
from chimera.tools.edit import EditStrategy, FuzzyEditor

# Default: exact match first, fallbacks automatic
# (EditFileTool gains optional editor parameter)

# Developer
editor = FuzzyEditor(strategies=[
    EditStrategy.EXACT,
    EditStrategy.STRIP_LINES,
    EditStrategy.NORMALIZE_WHITESPACE,
    EditStrategy.INDENT_FLEXIBLE,
    EditStrategy.LEVENSHTEIN,
])
edit_tool = EditFileTool(editor=editor)

# Framework author
class SemanticMatch(EditStrategy):
    def find(self, content: str, search: str) -> tuple[int, int] | None: ...
```

### Strategies

1. **EXACT** — current behavior, character-for-character
2. **STRIP_LINES** — strip each line before comparing
3. **NORMALIZE_WHITESPACE** — collapse whitespace runs to single space
4. **INDENT_FLEXIBLE** — normalize indentation to relative levels
5. **LEVENSHTEIN** — `difflib.SequenceMatcher`, threshold ratio ≥ 0.85

Each strategy returns match position in the *original* content. Only the search is fuzzy — replacement is always exact.

`ToolResult.metadata["match_strategy"]` reports which strategy succeeded.

### Scope

~80 lines new code. Existing `EditFileTool` default behavior unchanged (exact-only unless editor is provided).

---

## 3-Tier API Summary

| Module | One-liner | Developer | Framework Author |
|--------|-----------|-----------|-----------------|
| MCP | `MCPToolSource.from_stdio(cmd)` | `MCPClient` + multiple servers | Custom `MCPTransport` |
| LSP | `LSPManager.for_project(path)` | `LSPManager.add()` per language | Custom `LanguageServerConfig` |
| Config | `ProjectConfig.from_directory()` | Explicit rules/skills/schemas | Custom `ConfigSource` |
| Catalog | `create_provider("bedrock/...")` | `catalog.register(ModelConfig())` | `register_provider_type()` |
| Fuzzy Edit | Just works (auto fallback) | `FuzzyEditor(strategies=[...])` | Custom `EditStrategy` |

---

## Scope Boundaries

- **MCP:** stdio + HTTP, bearer tokens, no OAuth, no subscriptions
- **LSP:** stdio, 8 core methods, no auto-install, no completion
- **Config:** file-based discovery, no remote URLs
- **Catalog:** data-driven config, no new provider subclasses
- **Fuzzy Edit:** 5 strategies, exact replacement always
