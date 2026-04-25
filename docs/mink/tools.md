# M3 Tools — NotebookEdit, Worktree, Persistent Todo, MCP Prefixing

This page covers the four tool surfaces added/changed in milestone M3
to bring `chimera mink` to CC-parity.

## NotebookEdit

`chimera/tools/notebook_edit.py` — `NotebookEditTool(BaseTool)`.

Insert, replace, or delete cells in a Jupyter notebook via `nbformat`.
Requires the `notebook` extra: `pip install 'chimera-run[notebook]'`.

| Field           | Type    | Notes                                                |
|-----------------|---------|------------------------------------------------------|
| `notebook_path` | string  | Path to `.ipynb` (required).                         |
| `action`        | enum    | `insert` / `replace` / `delete` (required).          |
| `cell_index`    | int     | 0-based; either this or `cell_id` is required.       |
| `cell_id`       | string  | nbformat-v4 cell id alternative to `cell_index`.     |
| `content`       | string  | Cell source for `insert` / `replace`.                |
| `cell_type`     | enum    | `code` (default), `markdown`, or `raw`.              |

Replace preserves the cell `id`. Insert without `cell_index` appends.

## EnterWorktree / ExitWorktree

`chimera/tools/worktree_tool.py` — `EnterWorktreeTool`, `ExitWorktreeTool`.

Both shell out to `git worktree`. The new worktree lives at
`<repo_root>/../worktrees/<name>`.

`enter_worktree`:

| Field         | Type   | Notes                                          |
|---------------|--------|------------------------------------------------|
| `name`        | string | Branch + directory leaf name (required).       |
| `base_branch` | string | Defaults to `HEAD`.                            |

`exit_worktree`:

| Field           | Type   | Notes                                                              |
|-----------------|--------|--------------------------------------------------------------------|
| `worktree_path` | string | Path returned by `enter_worktree` (required).                      |
| `action`        | enum   | `remove` (refuses if dirty), `merge`, or `abandon` (force-remove). |

## Persistent TodoWrite

`chimera/tools/todo.py` — `TodoTool` (existing API; now file-backed).

Each mutation atomically rewrites two JSON files:

- `<cwd>/.chimera/todo.json` — project-scoped (committed-or-ignored at user discretion).
- `~/.chimera/projects/<sha256(cwd)[:16]>/todo.json` — user-scope mirror so the
  list survives even when the project dir is read-only.

`TodoTool.load_at_session_start(session_id, cwd)` rehydrates state for `/resume`.

The tool's JSON schema is unchanged — `{action: add|complete|list, task: str?}`.

## MCP Tool Prefixing

`chimera/mcp/tools.py` — `MCPTool`, `mcp_normalize`, `mcp_prefix`, `mcp_unprefix`.

Discovered tools are exposed as `mcp__<server>__<tool>` (CC-compatible).
Both fragments are normalized: lowercased; non-`[A-Za-z0-9_-]` becomes `_`.
The original upstream name is preserved on the wrapper as `original_name`
so dispatch back to the server still routes correctly.

Permission rules already understand server-level shorthand:
`mcp__filesystem` matches every tool from the `filesystem` server.

### 3-scope `.mcp.json` merge

`chimera/mcp/config.py:load_mcp_config(cwd, home)` merges:

1. `~/.claude/.mcp.json` (user)
2. `<cwd>/.claude/.mcp.json` (project — overrides user)
3. `<cwd>/.claude/.mcp.local.json` (local — overrides project)

Server names are merge keys; each server config is replaced wholesale (not
deep-merged) so a project entry can swap a command/args without inheriting
stray env from the user scope. Both `mcpServers` (CC) and `servers`
(legacy Chimera) keys are accepted.
