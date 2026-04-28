---
title: "Mink MCP Advanced"
description: "Advanced MCP layer additions in chimera mink: namespacing, transport, and server lifecycle."
---

# MCP — Advanced (M5-A)

This page covers the M5-A additions to Chimera's MCP layer:
SSE and WebSocket transports, OAuth2 PKCE flow, the `mcp__server__tool`
naming convention, the 3-scope `.mcp.json` merge, and the 11-phase
lifecycle state machine.

## Modules

| File | Role |
|------|------|
| `chimera/mcp/client.py` | Top-level MCP client; tool discovery + dispatch. |
| `chimera/mcp/transport.py` | Transport ABC + stdio implementation. |
| `chimera/mcp/sse_transport.py` | Server-Sent Events transport (HTTP long-poll). |
| `chimera/mcp/ws_transport.py` | WebSocket transport. |
| `chimera/mcp/lifecycle.py` | 11-phase server lifecycle (connect, initialize, list-resources, list-tools, ready, …, disconnect). |
| `chimera/mcp/oauth.py` | OAuth2 PKCE flow with on-disk token store (`0o600` perms). |
| `chimera/mcp/tools.py` | `MCPTool`, `ListMcpResourcesTool`, `ReadMcpResourceTool`, `McpAuthTool`. |

## Tool naming

Discovered MCP tools are surfaced to the model as `mcp__<server>__<tool>`
(double-underscore separator, matching CC). Permission rules accept either
the prefixed form (`Tool(mcp__filesystem__read_file)`) or the server
shorthand (`Tool(mcp__filesystem)`) to allow the entire server.

The inverse mapping is applied automatically when dispatching: when the
model emits `mcp__filesystem__read_file`, the client routes to the
`filesystem` server's `read_file` tool.

## Config scopes

`chimera/mcp/config.py:load_mcp_config(cwd, home)` merges three CC-style
configuration files in this precedence (later wins on key collisions):

1. `~/.claude/.mcp.json` (user)
2. `<project>/.claude/.mcp.json` (project, git-tracked)
3. `<project>/.claude/.mcp.local.json` (local override, gitignored)

Server names are merge keys; each server is replaced wholesale (not
deep-merged) so a project entry can swap a server's command/args without
inheriting stray env vars from the user-scope entry. Both `mcpServers`
(CC) and `servers` (legacy Chimera) keys are accepted on input.

The mink CLI additionally loads two top-of-project locations on
``chimera mink -p`` invocations (see `chimera/mink/cli.py:_load_mcp_tools`):
`<project>/.mcp.json` and `~/.chimera/mcp.json`. The same file at the
top of project (no `.claude/` prefix) is the most common community
convention; it is read for that one entry-point only.

## OAuth2 PKCE

Implemented in `chimera/mcp/oauth.py`. The flow:

1. Discover the authorization endpoint from the MCP server's metadata.
2. Generate a PKCE code verifier + challenge (SHA-256, base64url).
3. Open the user's browser to the auth URL; spin up a localhost callback.
4. Exchange the returned code for an access token + refresh token.
5. Persist the token bundle to `~/.chimera/mcp/tokens/<server>.json`
   with mode `0o600`.
6. On 401, automatically refresh using the refresh token before retrying.

On macOS, optional Keychain storage is gated behind
`CHIMERA_MCP_USE_KEYCHAIN=1` — see the module docstring.

## SSE and WebSocket

- **SSE** (`sse_transport.py`): used by long-running MCP servers that need
  to stream events back without a WebSocket upgrade. The transport keeps a
  persistent HTTP connection open and yields parsed `event:` blocks as
  JSON-RPC frames.
- **WebSocket** (`ws_transport.py`): bidirectional JSON-RPC over a single
  WS connection. Lower overhead than SSE for chatty servers.

Both transports plug into the same `MCPClient` interface used by stdio,
so consumer code does not change.

## Lifecycle phases

The 11-phase state machine in `chimera/mcp/lifecycle.py` (with per-phase
timeouts):

```
disconnected → connecting → initializing → handshaking
→ listing_resources → listing_tools → listing_prompts
→ ready → executing → cleaning_up → disconnected
```

Per-phase timeouts default to 5–30s and can be overridden in
`.mcp.json` via `phase_timeouts: { initializing: 10, ... }`.

## Cross-references

- Inline docs in each module above.
- Parity matrix row 7 in [`parity-matrix.md`](./parity-matrix.md).
- CC concept origin: `research/mink/07-cc-mcp.md`,
  `research/mink/18-chimera-mcp-plugins.md`.

## Limitations

1. **`ManagedProxy` transport** (CC's CCR proxy) is **not** implemented;
   community MCPs do not depend on it.
2. **Token storage on Linux** uses a flat 0o600 file; integration with
   `libsecret` / `gnome-keyring` is a follow-up.
3. **Discovery via mDNS** is out of scope; users must list servers in
   `.mcp.json`.
