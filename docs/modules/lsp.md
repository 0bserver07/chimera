# LSP (Language Server Protocol)

Chimera's LSP module connects to language servers to provide code intelligence -- diagnostics, go-to-definition, completions, rename, and more. This gives agents structured feedback from real compilers and type checkers, enabling more accurate code edits than text-based heuristics alone.

## Quick Start

```python
from chimera.lsp import LSPManager

lsp = LSPManager.for_project("./myapp")
lsp.start("./myapp")

diagnostics = lsp.get_diagnostics("src/main.py")
for d in diagnostics:
    print(d.to_feedback_str())

lsp.stop()
```

## Key Classes

| Class | Description |
|-------|-------------|
| `LSPClient` | Abstract base class for language server protocol clients. Defines `initialize()`, `diagnostics()`, and `shutdown()`. |
| `Diagnostic` | Dataclass representing a single diagnostic (file, line, column, severity, message, source, code). |
| `Severity` | Enum with levels: `ERROR` (1), `WARNING` (2), `INFORMATION` (3), `HINT` (4). |
| `LSPManager` | Manages multiple language server lifecycles and routes requests by file extension. |
| `LSPSession` | A single LSP server connection over stdin/stdout with JSON-RPC Content-Length framing and background reader thread. |
| `LSPTool` | Wraps `LSPManager` as a Chimera `BaseTool` with 8 actions: `go_to_definition`, `find_references`, `hover`, `document_symbols`, `diagnostics`, `completion`, `rename`, `code_action`. |
| `LanguageServerConfig` | Dataclass holding server configuration: name, command, extensions, initialization_options. |
| `BUILTIN_SERVERS` | Pre-configured `LanguageServerConfig` list for Python (pyright), TypeScript, Go (gopls), and Rust (rust-analyzer). |

## Usage

### Auto-detect and start language servers

`LSPManager.for_project()` scans for available language servers on `PATH` and configures them automatically:

```python
from chimera.lsp import LSPManager

lsp = LSPManager.for_project("./myapp")
lsp.start("./myapp")

# Get diagnostics for a Python file
diagnostics = lsp.get_diagnostics("src/main.py")
for d in diagnostics:
    print(d.to_feedback_str())
    # [error] src/main.py:10:5: Cannot find name "foo" (Pyright) [reportUndefinedVariable]

lsp.stop()
```

`LSPManager` supports context manager usage:

```python
with LSPManager.for_project("./myapp") as lsp:
    lsp.start("./myapp")
    diagnostics = lsp.get_diagnostics("src/main.py")
```

### Manually configure language servers

```python
from chimera.lsp import LSPManager

lsp = LSPManager()
lsp.add("python", ["pyright-langserver", "--stdio"], extensions=(".py",))
lsp.add("typescript", ["typescript-language-server", "--stdio"], extensions=(".ts", ".tsx"))
lsp.start("/path/to/project")

# Route requests automatically by file extension
py_diags = lsp.get_diagnostics("src/app.py")       # Uses pyright
ts_diags = lsp.get_diagnostics("src/index.ts")      # Uses typescript-language-server

lsp.stop()
```

### Using LSPSession directly

For lower-level control, use `LSPSession` to interact with a single language server:

```python
from chimera.lsp import LSPSession
from pathlib import Path

session = LSPSession(["pyright-langserver", "--stdio"])
session.start("/path/to/project")

uri = Path("src/main.py").resolve().as_uri()
text = Path("src/main.py").read_text()

# Open a file
session.did_open(uri, "python", text)

# Go to definition
locations = session.definition(uri, line=10, character=5)

# Find references
refs = session.references(uri, line=10, character=5)

# Get hover information
info = session.hover(uri, line=10, character=5)

# Get completions
items = session.completion(uri, line=10, character=5)

# Rename a symbol
edit = session.rename(uri, line=10, character=5, new_name="new_func")

# Get code actions for a range
actions = session.code_action(uri, start_line=10, start_char=0, end_line=15, end_char=0)

# Get document symbols
symbols = session.document_symbols(uri)

# Get cached diagnostics (populated from publishDiagnostics notifications)
diagnostics = session.get_diagnostics(uri)

session.stop()
```

### Adding LSP as an agent tool

`LSPTool` wraps `LSPManager` as a Chimera `BaseTool`, giving agents access to code intelligence:

```python
from chimera.lsp import LSPManager, LSPTool
from chimera.core.agent import Agent
from chimera.core.tool_group import DEFAULT_TOOLS

lsp = LSPManager.for_project("./myapp")
lsp.start("./myapp")

lsp_tool = LSPTool(lsp=lsp)
agent = Agent(tools=list(DEFAULT_TOOLS) + [lsp_tool])
result = agent.run("Check src/main.py for type errors and fix them")

lsp.stop()
```

The `LSPTool` accepts these actions via its `action` parameter:

| Action | Required args | Description |
|--------|--------------|-------------|
| `go_to_definition` | `file`, `line`, `character` | Jump to where a symbol is defined |
| `find_references` | `file`, `line`, `character` | Find all usages of a symbol |
| `hover` | `file`, `line`, `character` | Get type/documentation info |
| `document_symbols` | `file` | List all symbols in a file |
| `diagnostics` | `file` | Get errors/warnings from the language server |
| `completion` | `file`, `line`, `character` | Get autocomplete suggestions |
| `rename` | `file`, `line`, `character`, `new_name` | Rename a symbol across files |
| `code_action` | `file`, `line`, `character`, `end_line`, `end_character` | Get suggested fixes for a code range |

### Built-in server configurations

The `BUILTIN_SERVERS` list provides pre-configured setups for common language servers:

| Language | Server command | Extensions |
|----------|---------------|------------|
| Python | `pyright-langserver --stdio` | `.py` |
| TypeScript | `typescript-language-server --stdio` | `.ts`, `.tsx`, `.js`, `.jsx` |
| Go | `gopls serve` | `.go` |
| Rust | `rust-analyzer` | `.rs` |

## Integration

- **Agent tools**: `LSPTool` extends `BaseTool` and can be added to any agent's tool list. The agent calls it with an `action` parameter to perform LSP queries.
- **Multi-language**: `LSPManager` handles routing to the correct language server based on file extension, supporting polyglot projects.
- **Diagnostic feedback**: `Diagnostic.to_feedback_str()` formats diagnostics as human-readable strings suitable for agent context, e.g. `[error] src/main.py:10:5: Cannot find name "foo" (Pyright)`.
- **Auto-detection**: `LSPManager.for_project()` only registers servers whose commands are found on `PATH`, so it works out of the box without manual configuration.

## Import Reference

```python
from chimera.lsp import (
    LSPClient,
    Diagnostic,
    Severity,
    LSPManager,
    LSPSession,
    LSPTool,
    LanguageServerConfig,
    BUILTIN_SERVERS,
)
```
