---
title: Ferret Subcommands
description: Codex-style subcommands shipped in W14-1 — apply, review, fork, mcp-server, mcp.
---

# Ferret Subcommands (W14-1)

The `chimera ferret` CLI ships five additional subcommands plus a small
`mcp` management surface, paralleling the IDE-first OpenAI-flagship
coding agent's command set. Each one is a thin adapter over an existing
Chimera primitive — keeping ferret a pure routing layer rather than a
re-implementation.

| Subcommand | Backed by | One-liner |
|------------|-----------|-----------|
| `ferret apply [--last]` | `chimera.tools.apply_patch` + `git apply` | Apply the latest agent diff to the working tree. |
| `ferret review <target>` | `chimera.review.orchestrator.ReviewOrchestrator` | Non-interactive code review with multi-perspective feedback. |
| `ferret fork <id> [--last] [--all]` | `chimera.sessions.eventlog` | Fork an existing eventlog session into a new run id. |
| `ferret mcp-server` | `chimera/mcp_servers/` JSON-RPC pattern | Run ferret as an MCP server other agents can drive. |
| `ferret mcp {add,list,remove}` | `~/.chimera/ferret/mcp_servers.json` | Manage local MCP server launchers. |

Run `chimera ferret --help-long` to see the per-flag details; the
short `--help` stays under 50 lines and lists the subcommand names but
delegates the long descriptions to `--help-long`.

## `chimera ferret apply [--last]`

Walks `~/.chimera/eventlog/ferret-*` newest-first looking for any tool
event that emitted a unified diff (most commonly the `apply_patch`
tool's envelope), writes the diff to a temp file, and shells out to
`git apply` from the current working directory.

```bash
# Walk every ferret session until a patch is found, then apply it.
chimera ferret apply

# Apply only from the most-recent session (fail fast otherwise).
chimera ferret apply --last
```

Exit codes:

* `0` — patch applied successfully.
* `1` — patch was found but `git apply` returned non-zero (the patch
  body is preserved in the temp file path printed on stderr).
* `2` — no patch found anywhere in the eventlog, or the eventlog root
  is missing.

## `chimera ferret review <target>`

Threads `ReviewOrchestrator` (from `chimera/review/orchestrator.py`)
into ferret's surface so a user can request a non-interactive review
of any path or git rev-spec without spinning up the interactive REPL:

```bash
# File: review the current contents of one path.
chimera ferret review src/foo.py

# Directory: review every readable file (capped at 64 entries).
chimera ferret review chimera/ferret/

# Git rev-spec: review the diff between two commits.
chimera ferret review HEAD~1..HEAD
```

Resolution order for `<target>`:

1. Looks like a rev-spec (`..`, `...`, `@`-prefix) → run `git diff <target>`.
2. Existing path with pending git changes → run `git diff -- <path>`.
3. Existing path with no pending changes → render as a "new file"
   pseudo-diff so the perspectives still see structured input.

The review output is printed to stdout in plain text:

```
[ferret review] target='src/foo.py' approved=False comments=2
  - [warning] Unhandled exception path on line 42
  - [info] Missing tests for the cleanup branch
```

Exit codes:

* `0` — orchestrator returned `approved=True`.
* `1` — at least one perspective flagged a comment.
* `2` — usage / resolve error (missing target, empty diff, provider
  build failure).

## `chimera ferret fork <session-id> [--last] [--all]`

Copies an existing ferret eventlog session into a fresh
`ferret-<UTC>-<uuid>` directory, rewriting `summary.json` with a
`parent_id` pointer. The fork is immediately resumable via
`chimera ferret -p '<prompt>' --resume <new-id>`.

Selector semantics:

* Bare `ferret fork <id>` — fork that exact session.
* `ferret fork --last` — fork the newest session under the current
  working directory (cwd-scoped).
* `ferret fork --all` — fork the newest session anywhere in the
  eventlog (cross-cwd picker).
* Combining `<id>` with `--last` or `--all` is a usage error
  (`rc=2`).

The new id is printed to stdout:

```
[ferret fork] ferret-20260507T010101-aaaa -> ferret-20260507T015000-bbbb
resume with: chimera ferret -p '<prompt>' --resume ferret-20260507T015000-bbbb
```

## `chimera ferret mcp-server`

Runs ferret as an MCP server on stdio, exposing two tools:

* `ferret_run(prompt, model?, max_steps?)` — fire one ferret turn.
  Returns the assistant text. Equivalent to `ferret -p PROMPT`.
* `ferret_apply(last?, cwd?)` — apply the latest agent diff via the
  same code path `ferret apply` uses.

The protocol is JSON-RPC 2.0 over newline-delimited stdin/stdout —
the same shape every other Chimera MCP server (`search_server.py`,
`review_server.py`, etc.) speaks. Configure it in any MCP host's
`.mcp.json`:

```json
{
  "mcpServers": {
    "chimera-ferret": {
      "command": "chimera",
      "args": ["ferret", "mcp-server"]
    }
  }
}
```

## `chimera ferret mcp {add,list,remove}`

Persists a launcher registry at `~/.chimera/ferret/mcp_servers.json`
in the standard `mcpServers` envelope so ferret (and tools that
co-operate via the same file) can spawn the MCP servers a user has
opted into. The file is written with `0o600` permissions because
operators often store API keys inside `args`.

```bash
# Register a launcher (whitespace-separated command).
chimera ferret mcp add chimera-search \
  "python -m chimera.mcp_servers.search_server"

# Show every registered launcher.
chimera ferret mcp list
# chimera-search  python -m chimera.mcp_servers.search_server

# Drop an entry.
chimera ferret mcp remove chimera-search
```

`add` overwrites an existing entry under the same name. `remove`
returns `rc=2` when the name is unknown so shell scripts can detect
"already removed" without parsing stderr.

## Internals (for contributors)

Each subcommand lives in its own module under
`chimera/ferret/subcommands/`:

```
chimera/ferret/subcommands/
├── __init__.py        # HANDLERS dict (subcommand → dispatcher)
├── apply.py           # find_latest_diff + git apply wrapper
├── review.py          # target → diff + ReviewOrchestrator adapter
├── fork.py            # resolve_fork_source + fork_session
├── mcp_server.py      # FerretMCPServer JSON-RPC stdio server
└── mcp_manage.py      # add/list/remove + atomic JSON persistence
```

`chimera/ferret/cli.py` registers them late-bound via
`_register_w14_subcommands()` so the import cost of `--help` /
`--version` stays unchanged. The cli's existing `subcommand` /
`sub_action` / `sub_target` positional layout was extended with a
fourth `sub_extra` slot to support `ferret mcp add <name> <command>`,
and a hidden `--last` flag was added for the `apply` and `fork`
selectors.
