---
title: "chimera.lsp"
description: "Reference for chimera.lsp — Language Server Protocol client, sessions, diagnostics, completion, rename."
---

`chimera.lsp` provides a stdlib-only LSP client so tools can ask a
language server for diagnostics, completions, hovers, and renames.

## Top-level exports

```python
from chimera.lsp import (
    LSPClient,
    LSPSession,
    LSPManager,
    LSPTool,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `LSPClient` | `chimera.lsp.base` | JSON-RPC client over server stdio. Methods: `initialize`, `did_open`, `definition`, `references`, `rename`, `diagnostics`, `completion`, `hover`. |
| `LSPSession` | `chimera.lsp.session` | One server's lifecycle (`initialize` → requests → `shutdown`). |
| `LSPManager` | `chimera.lsp.manager` | Registry of `LSPSession` keyed by language id; routes requests to the right server. |
| `LSPTool` | `chimera.lsp.tool` | `BaseTool` wrapper exposing `definition_lookup`, `find_references`, `rename_symbol` to the agent. |

## Bundled servers (`chimera.lsp.servers`)

Per-language launch configurations: `pylsp` (Python), `typescript-language-server` (TS/JS),
`rust-analyzer` (Rust), `gopls` (Go), `clangd` (C/C++).

## See also

- [`chimera.tools`](/reference/tools/) — `definition_lookup` is the
  tool-level surface that wraps `LSPTool`.
