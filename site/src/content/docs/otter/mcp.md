---
title: Otter MCP Discovery
description: How otter discovers and loads MCP servers from .opencode/mcp.json and equivalent config files.
---

# `chimera otter` MCP discovery

Otter discovers Model Context Protocol (MCP) servers from the
filesystem layout used by the open-source coding-agent ecosystem,
then hands the normalized configuration to Chimera's MCP runtime
(`chimera/mcp/`) for live connection management.

## Sources

The loader (`chimera/otter/mcp.py`) reads servers from these paths
in increasing precedence order:

1. `~/.opencode/config.json` — user-level, the `mcp` key.
2. `<project_root>/.opencode/config.json` — project-level, the `mcp` key.
3. `<project_root>/.opencode/mcp.json` — project-level, dedicated file.

Project entries override user entries on name conflict. Servers
present only at the user level are still loaded so users can pin
"always-on" servers in their home directory.

## Schema

Both transports honored by the upstream agent are mirrored:

```json
{
  "mcp": {
    "playwright": {
      "type": "local",
      "command": ["npx", "@modelcontextprotocol/server-playwright"],
      "env": { "PLAYWRIGHT_HEADLESS": "1" },
      "enabled": true,
      "timeout_ms": 30000
    },
    "github": {
      "type": "remote",
      "url": "https://mcp.example.com/github",
      "headers": { "Authorization": "Bearer ${GITHUB_TOKEN}" },
      "enabled": true
    }
  }
}
```

Recognized keys (per server entry):

- **`type`** — `"local"` (stdio subprocess) or `"remote"` (HTTP).
- **`command`** — array of strings; required for `local`. First element
  is the executable, rest are args.
- **`env`** — environment variable map for the subprocess; merged on
  top of the parent process environment.
- **`url`** — required for `remote`. The base URL of the HTTP MCP
  endpoint.
- **`headers`** — header map for `remote`. `${VAR}` substitution against
  the current environment is honored.
- **`enabled`** — boolean. Defaults to `true`. When `false`, the
  server is loaded but not started.
- **`timeout_ms`** — request timeout. Defaults to 30000.

## Public API

```python
from chimera.otter.mcp import load_mcp_servers, MCPServerConfig

# Walk all four sources and merge.
servers: list[MCPServerConfig] = load_mcp_servers(
    project_root="/path/to/project",
    home="/optional/home/override",
)

for s in servers:
    print(s.name, s.transport, s.command if s.transport == "stdio" else s.url)
```

`MCPServerConfig` is the otter-local dataclass. It is a strict
superset of `chimera.plugins.MCPServerConfig` (which is stdio-only):

```python
@dataclass
class MCPServerConfig:
    name: str
    transport: Literal["stdio", "http"]
    enabled: bool = True
    timeout_ms: int = 30_000
    # stdio-only:
    command: list[str] | None = None
    env: dict[str, str] | None = None
    # http-only:
    url: str | None = None
    headers: dict[str, str] | None = None
```

## Live connection

The dataclass produced by the loader is forwarded to
`chimera.mcp.client.MCPClient` via `chimera.mcp.from_config(...)`. The
runtime supports four transports:

- **stdio** — `command` is spawned as a subprocess and JSON-RPC flows
  over stdin/stdout.
- **http (streamable)** — JSON-RPC over chunked HTTP; the default for
  modern MCP servers.
- **sse** — JSON-RPC over Server-Sent Events; honored when the
  endpoint advertises it.
- **websocket** — JSON-RPC over WebSocket; honored when the URL uses
  `ws://` / `wss://`.

The transport choice is auto-detected from the URL scheme; otter does
not need a separate config knob.

## Tool prefix

Every MCP-provided tool is registered with the prefix `mcp__<server-name>__`
to keep the namespace clean. For example, the `playwright` server's
`browser_navigate` tool becomes `mcp__playwright__browser_navigate`. This
matches the convention used by `chimera mink` (so a project's permissions
file works against both subcommands without rewriting tool names).

## Authentication

Remote MCP servers that require OAuth2 are handled by
`chimera.mcp.oauth`. The flow is the same as in `chimera mink`:

1. The first connect attempt fails with a `needs_auth` status.
2. A device code is printed; the user visits the URL in their
   browser.
3. The token is cached under `~/.chimera/mcp-tokens/<server>.json`
   with `0o600` perms.
4. Subsequent connects succeed.

Auth state can be inspected via `/mcp` in the REPL. Re-authentication
is `chimera otter mcp auth <name>` — wired in wave-2.

## REPL surface

- `/mcp` (or `/mcps`) — list configured servers, their connect status
  (`connected` / `disabled` / `failed` / `needs_auth`), and the live
  tool list per server.

The TUI dialog used by the upstream agent ("Toggle MCPs") maps to
`/mcps` in the REPL. Mid-session enable/disable of servers without a
REPL restart is on the wave-2 follow-up list.

## Filesystem-fact path notice

The path `~/.opencode/config.json` is referenced as a filesystem fact
(otter reads from it; the user can put their existing MCP config
there and it works), not as a brand claim. See
`docs/otter/security-and-trademarks.md` for the policy.

## Test surface

`tests/otter/test_mcp.py` covers nineteen scenarios:

- User-only / project-only / both configured.
- Stdio + http transport schemas.
- `${VAR}` env-var substitution in headers.
- Project-overrides-user precedence on name conflict.
- `disabled: false` propagation.
- Malformed JSON → loader returns empty list and warns (does not raise).
- Symlinked config files are followed.

## See also

- `chimera/otter/mcp.py` — loader.
- `chimera/otter/mcp_cli.py` — `mcp list` / `mcp add` / `mcp auth` handlers.
- `chimera/mcp/` — runtime (client, lifecycle, OAuth, transports).
- `docs/otter/plugins.md` — plugins can also contribute MCP servers.
- `docs/otter/parity-matrix.md` — overall parity status.

## CLI surface (wave 9)

`chimera otter mcp` is the write-side counterpart to the loader. Where
the loader (`chimera/otter/mcp.py`) only reads `~/.opencode/config.json`
and the project-level `.opencode/{config,mcp}.json` to materialize the
agent's MCP tool group, the CLI (`chimera/otter/mcp_cli.py`) edits
those files and runs an OAuth device flow against an HTTP MCP server.

### `chimera otter mcp list`

Print known MCP servers across user + project scopes:

```sh
$ chimera otter mcp list
NAME                 TRANSPORT  CONNECT                                  SOURCE
fs                   stdio      fs-server --root /tmp                    project
weather              http       https://example.com/mcp                  user
```

Output is plain (no rich/colour) so it pipes cleanly into `grep` /
`awk`. The `SOURCE` column is one of `user`, `project`, `user+project`,
followed by `(disabled)` for `enabled: false` entries.

### `chimera otter mcp add <name> <command...>`

Append a new stdio MCP server entry to the chosen scope's
`config.json`:

```sh
# Project scope (default) — writes <project>/.opencode/config.json
$ chimera otter mcp add fs -- fs-server --root /tmp
About to add MCP server 'fs' (project scope):
  file:  <project>/.opencode/config.json
  entry: {"type": "local", "command": ["fs-server", "--root", "/tmp"]}
Write this entry? [y/N]: y
wrote <project>/.opencode/config.json

# User scope — writes ~/.opencode/config.json
$ chimera otter mcp add fs --user -- fs-server --root /tmp

# Skip confirmation (CI):
$ chimera otter mcp add fs --yes -- fs-server

# HTTP MCP server (no command):
$ chimera otter mcp add weather \
    --mcp-http https://example.com/mcp \
    --mcp-header "Authorization=Bearer abc"

# stdio with environment overrides:
$ chimera otter mcp add fs --mcp-env LOG=1 --mcp-env QUIET=true -- fs-server
```

Notes:

- The `--` separator is recommended whenever the trailing `<command...>`
  contains its own flags, so argparse doesn't try to consume them as
  otter flags.
- Adding a name that already exists in the chosen scope's file is
  refused — remove it manually before re-adding.
- The write is atomic (`tmp` + rename) and the file is `chmod 0o600`.

### `chimera otter mcp auth <name>`

Initiate an OAuth device flow against an HTTP MCP server:

```sh
$ chimera otter mcp auth secured
Starting OAuth device flow for MCP server 'secured'.
  device endpoint: https://issuer.example/device
  token endpoint:  https://issuer.example/token

Visit: https://issuer.example/device
Enter code: ABCD-1234

saved credential for mcp:secured to credential store.
```

Resolution order:

1. Look up `<name>` via `load_mcp_servers` (project + user merge).
2. Reject the call if the entry is not HTTP transport — stdio MCP
   servers authenticate via per-process env vars, not OAuth.
3. If the entry has an `oauth` block carrying `client_id`,
   `device_authorization_endpoint` (or `device_auth_url`), and
   `token_endpoint` (or `token_url`), hand it to
   `chimera.auth.OAuthDeviceFlow` and persist the resulting
   `Credential` under `mcp:<name>` in
   `~/.chimera/credentials.json` (`0o600`).
4. Otherwise, fall back to a manual "open this URL, paste the token
   back" UX.

### Argparse layout

`mcp` is registered as a top-level subcommand alongside `serve`,
`sessions`, `share`, `agents`, `bench`. The shared positional layout is:

| slot         | dest          | example                              |
|--------------|---------------|--------------------------------------|
| SUBCOMMAND   | `subcommand`  | `mcp`                                |
| ACTION       | `sub_action`  | `list` / `add` / `auth`              |
| TARGET       | `sub_target`  | server name (`add` / `auth`)         |
| MCP_EXTRA    | `mcp_extra`   | trailing executable + args (`add`)   |

Flags (all optional; defaults cover the common case):

| flag              | dest          | meaning                                  |
|-------------------|---------------|------------------------------------------|
| `--user`          | `agents_user` | write to `~/.opencode` instead of `<project>` |
| `--mcp-http URL`  | `mcp_http`    | http transport (mutually exclusive with command) |
| `--mcp-header K=V`| `mcp_header`  | repeatable HTTP header                   |
| `--mcp-env K=V`   | `mcp_env`     | repeatable subprocess env override       |
| `--yes`           | `mcp_yes`     | skip the y/N confirmation                |
