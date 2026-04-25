# `chimera otter` LSP first-class tools

Otter promotes the Language Server Protocol from a generic peripheral
integration into a first-class tool surface. The monolithic
`chimera.lsp.LSPTool` (which routes through an `action` enum) is split
into five dedicated tools that mirror the upstream open-source coding
agent's posture: each LSP capability is its own tool with its own
schema and its own permission scope.

## Tools

| Tool name           | Capability             | Description                                            |
|---------------------|------------------------|--------------------------------------------------------|
| `lsp_diagnostics`   | textDocument/diagnostics | Fetch diagnostics for a file or open-buffer.         |
| `lsp_completion`    | textDocument/completion  | Suggest completions at a cursor position.            |
| `lsp_rename`        | textDocument/rename      | Rename a symbol across the workspace.                |
| `lsp_definition`    | textDocument/definition  | Jump to the definition of a symbol.                  |
| `lsp_references`    | textDocument/references  | List references for a symbol.                        |

Each tool takes a file path (or URI), an optional position
(line/character), and tool-specific arguments. All tools return a
`ToolResult` with structured data, even on failure.

## Tool group

```python
from chimera.otter.lsp import build_lsp_tool_group

group = build_lsp_tool_group()
# Returns a `ToolGroup` containing the five tools above.
```

The group is wired into the otter REPL automatically (when an LSP
manager is available); you can extend it with additional tools or
swap in a different provider for tests via:

```python
from chimera.otter.lsp import build_lsp_tool_group, LSPProvider

def provider() -> LSPManager | None:
    return my_test_manager

group = build_lsp_tool_group(provider=provider)
```

## Provider injection

Each LSP tool consults a provider callable to find an `LSPManager`.
The default provider lazily resolves `LSPManager.for_project(...)`
based on the agent's working directory and starts language servers
on first use. Production wiring uses `auto_detect_provider`; tests
swap in stubs.

```python
from chimera.otter.lsp import auto_detect_provider

manager = auto_detect_provider()
if manager:
    diags = manager.diagnostics(file_path)
```

## Graceful degradation

Every tool degrades gracefully:

- **No manager** — tool returns a `ToolResult` with
  `error="LSP not configured"`.
- **No language server for the file** — `error="no language server
  for <ext>"`.
- **Server crashed during call** — `error="LSP server failed:
  <reason>"`.
- **Network/IPC timeout** — `error="LSP request timed out"`.

Tools never raise into the agent loop, so the model sees a structured
error and can recover (e.g., fall back to grep for `lsp_definition`
misses).

## File address

All five tools accept file addresses in three forms:

1. **Relative path** — `chimera/otter/lsp.py`. Resolved against the
   agent's cwd.
2. **Absolute path** — `/repo/chimera/otter/lsp.py`.
3. **`file://` URI** — `file:///repo/chimera/otter/lsp.py`.

The tool normalizes the input to a `file://` URI before talking to the
language server, matching LSP's wire format.

## Position

`lsp_completion`, `lsp_rename`, `lsp_definition`, and `lsp_references`
take a position. Two formats are accepted:

- **Object form** — `{"line": 42, "character": 7}` (zero-indexed,
  the LSP convention).
- **Anchor form** — `{"text": "foo_bar", "occurrence": 1}` — finds
  the Nth occurrence of the literal text in the file. Useful when
  the agent doesn't know exact line numbers.

The anchor form is otter-specific and translates to a position
internally before the LSP request goes out.

## Permissions

Each tool registers its own permission key, so the rules in
`AGENTS.md` / project config can scope LSP usage independently:

```json
{
  "permission": {
    "lsp_diagnostics": "allow",
    "lsp_completion": "allow",
    "lsp_definition": "allow",
    "lsp_references": "allow",
    "lsp_rename": "ask"
  }
}
```

The default is `allow` for read-only tools and `ask` for `lsp_rename`,
which is the only tool that mutates files.

## Language server discovery

`LSPManager.for_project(...)` walks a small registry of known
language servers (TypeScript, Python, Go, Rust, Ruby) and starts
the appropriate one on demand. Custom servers can be registered via
the `chimera.lsp.server.LSPServer.register(...)` hook.

The discovery is cached per cwd; switching projects mid-session via
`/agent <name>` (which can change the agent's cwd) re-evaluates and
spawns fresh servers as needed.

## Wiring through the agent

The LSP tool group is added to the otter agent's tool list at
session bootstrap. Agents that declare a tool list in their
frontmatter (see `docs/otter/agents.md`) can opt out by listing only
non-LSP tools:

```yaml
tools: [bash, read, edit, grep]   # no LSP tools
```

Or opt in to a subset:

```yaml
tools: [bash, read, edit, lsp_diagnostics, lsp_definition]
```

## Diagnostics in CI

`chimera otter -p "fix the typescript errors in src/"` will pick up
`lsp_diagnostics` automatically and call it once at session start
to get a current error list, then again after each edit to verify
the fix. This matches the upstream agent's `--variant high` posture
on diagnostics-driven workflows.

## Test surface

`tests/otter/test_lsp.py` covers:

- Each tool calls the manager's corresponding method.
- The `LSPProvider` callable is invoked lazily (no manager started
  if no LSP tool is used).
- Graceful degradation when `provider()` returns `None`.
- Position anchor form translates to a numeric position correctly.
- File path normalization across relative / absolute / URI forms.
- Permission keys registered for each tool.

## See also

- `chimera/otter/lsp.py` — implementation.
- `chimera/lsp/` — LSP runtime (client, manager, language registry).
- `docs/otter/agents.md` — per-agent tool selection.
- `docs/otter/parity-matrix.md` — overall parity status.
