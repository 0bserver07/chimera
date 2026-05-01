---
title: Ferret Cloud Bridge
description: chimera ferret bridge — set up an authenticated HTTPS bridge so a remote UI can drive a local ferret session.
---

# Cloud bridge — driving ferret from a remote UI

`chimera ferret bridge` is an optional HTTPS bridge. It lets a local
ferret session be driven by a remote web UI without exposing the
HTTP server publicly. The bridge mirrors the pattern of the
upstream IDE-first OpenAI-flagship coding agent's web companion: the
agent stays on your laptop, the UI lives at a public URL, and a
single authenticated tunnel relays ACP traffic between them.

This page covers the setup, the auth-token format, the remote-URL
contract, and the reconnection / resumption semantics.

## When to use the bridge

- **Remote work.** Your laptop stays on a corporate VPN; the web
  UI is on your phone or a separate machine.
- **Pair / mob programming.** A teammate can attach to your local
  session over a sharable URL.
- **On-call.** An alert fires; you respond from a tablet without a
  full dev environment in front of you.

If your goal is just "drive ferret from the same machine," use
[`ide.md`](ide.md) (ACP) or HTTP (`chimera ferret serve --http`)
instead. The bridge is only worth the auth setup when the UI is on
a different host than the agent.

## Prerequisites

- A ferret installation on the local box (`uv sync --extra openai`).
- A remote URL you control, terminating TLS, that speaks the bridge
  contract below. The reference UI implementation lives under
  `chimera/ferret/cloud_bridge.py` and the corresponding remote
  receiver under `chimera/ferret_cloud/` (separate package, not
  shipped by default).
- An auth token shared between the local ferret and the remote UI.

## Setup

```bash
export FERRET_BRIDGE_TOKEN="$(openssl rand -hex 32)"

chimera ferret bridge \
  --remote-url https://ferret.example.com \
  --auth-token "$FERRET_BRIDGE_TOKEN"
```

When the bridge starts, ferret prints a one-line status:

```text
[ferret] bridge connected: https://ferret.example.com session=ferret-20260430T1230-...
```

The remote UI now receives every `session/update` notification and
can call back into the bridge to send messages, cancel turns, and
respond to permission requests. The ferret CLI itself remains a
co-driver — you can continue typing into the local REPL while a
remote operator drives from the UI.

## Auth-token format

The token is opaque to ferret. The reference receiver expects:

- A 64-char hex string (32 bytes of entropy) — generate with
  `openssl rand -hex 32` or `python -c "import secrets;
  print(secrets.token_hex(32))"`.
- Sent on every request as `Authorization: Bearer <token>`.
- Pinned at bridge start; rotation requires reconnecting (`Ctrl-C`
  the bridge, set a new `FERRET_BRIDGE_TOKEN`, restart).

There is no built-in token-issuance flow. Generate, share via your
trust channel of choice (1Password, Signal, an env-var manager),
and rotate when a contributor leaves.

## Remote-URL contract

The remote URL is expected to terminate TLS and implement the
following endpoints. Ferret will refuse to connect over plain HTTP
unless `--insecure` is passed (development only; the CLI prints a
loud warning).

### `POST /api/sessions`

Register a new bridge attachment. The local ferret POSTs:

```json
{
  "session_id": "ferret-20260430T1230-1f3c2a8b",
  "model": "gpt-5",
  "sandbox": "workspace-write",
  "approval": "auto",
  "cwd": "/Users/.../proj"
}
```

The remote responds with a UI URL that the operator can share:

```json
{
  "ui_url": "https://ferret.example.com/s/ferret-20260430T1230-1f3c2a8b",
  "ws_url": "wss://ferret.example.com/ws/ferret-20260430T1230-1f3c2a8b"
}
```

### `WSS /ws/<session_id>`

The persistent bridge socket. Ferret sends every `session/update`
notification; the remote can send any of the standard ACP request
methods (`session/message`, `session/cancel`,
`session/setApproval`, `permission/respond`, …).

The wire format is the same newline-delimited JSON-RPC 2.0 used by
ACP — see [`ide.md`](ide.md). The bridge does not transform the
payload; it only multiplexes between local stdio and remote
WebSocket.

### `POST /api/sessions/<session_id>/heartbeat`

Sent every 30 seconds with a small JSON body
(`{"sandbox", "approval", "cost_total_usd"}`). The remote uses this
to drive a status indicator and to prune dead sessions.

### `DELETE /api/sessions/<session_id>`

Sent on `Ctrl-C` to the bridge. Tells the remote to mark the
session as detached.

## Reconnection semantics

The bridge socket is reconnected automatically on transient
network failures (5-second initial backoff, exponential up to a
60-second cap, no jitter). On reconnect, ferret replays the last
`bridge.replay_window` events (default: 50) so the remote UI can
recover from a brief disconnect without losing context.

If the remote returns 401 / 403 the bridge does not retry — the
token is presumed wrong, and ferret exits the bridge with a clear
error.

## Composition with sandbox + approval

The bridge does not change ferret's local guardrails. Every tool
call from the remote operator passes through the same
sandbox + approval pipeline as a local call. In particular:

- The remote operator cannot widen the sandbox; sandbox modes are
  fixed at process start.
- The remote operator can change the approval preset live via
  `session/setApproval`. The change is logged in the local
  eventlog and the local CLI prints a banner.
- Permission prompts (when approval is `auto` and the risk
  classifier flags an `ask` rule) are routed to **both** the local
  CLI and the remote UI. Whoever responds first wins; the other
  surface gets a cancellation event.

This is by design: the operator who controls the local sandbox
remains the final authority on the OS-layer guardrails.

## Observability

The bridge logs to the standard ferret eventlog under
`~/.chimera/eventlog/ferret-<id>/`. Every remote-originated event
carries a `bridge` field in its metadata (`{"origin": "bridge",
"remote_user": "alice"}`). The status-bar `cost_update` notifications
are sent to both surfaces so the local operator always sees the
full per-turn cost.

## Defaults from `~/.codex/config.toml`

```toml
[bridge]
remote_url = "https://ferret.example.com"
# auth_token deliberately not honored from config — env-var only
```

CLI `--remote-url` wins over config; the auth token is **only**
read from `--auth-token` or `$FERRET_BRIDGE_TOKEN`, never from a
config file on disk, to avoid checking secrets into version control.

## Troubleshooting

- **`bridge: TLS verification failed`.** Your remote URL is using a
  self-signed cert. Either trust the cert at the OS level or
  re-run with `--insecure` (dev only).
- **`bridge: 401 Unauthorized`.** The token doesn't match what the
  remote receiver expects. Confirm `$FERRET_BRIDGE_TOKEN` matches.
- **`bridge: WS closed (code=1006)`.** Network blip; ferret will
  reconnect with backoff. If it keeps closing, check that the
  remote receiver's WS upgrade path is working.
- **`bridge: replay window exceeded`.** A long disconnect dropped
  events. The remote UI will need a `session/load` to resync.

## CLI invocation

The `bridge` subcommand is wired into `chimera ferret` via
`chimera/ferret/cli.py`. Two flags drive the connection:

- `--remote-url URL` — HTTPS base URL of the remote bridge service.
  Defaults to `chimera.ferret.cloud_bridge.DEFAULT_REMOTE_URL`, which
  points at a placeholder `.invalid` domain on purpose. Operators must
  opt in to a real remote.
- `--bridge-token TOKEN` — shared-secret bearer token sent on every
  request as `Authorization: Bearer <token>`. Falls back to the
  `FERRET_BRIDGE_TOKEN` environment variable when omitted.

Minimal invocation:

```bash
chimera ferret bridge \
  --remote-url https://ferret.example.com \
  --bridge-token "$FERRET_BRIDGE_TOKEN"
```

Equivalent with the env-var fallback:

```bash
export FERRET_BRIDGE_TOKEN="$(openssl rand -hex 32)"
chimera ferret bridge --remote-url https://ferret.example.com
```

Exit codes:

- `0` — graceful shutdown (Ctrl-C after a successful handshake).
- `1` — transport-level connect failure (network unreachable, parse
  error, etc.).
- `2` — auth failure (the remote returned 401/403, or no token was
  supplied via `--bridge-token` / `$FERRET_BRIDGE_TOKEN`).

The dispatcher hands every inbound message from the remote UI to a
default `inbound_handler` that logs `[ferret bridge] inbound …` to
stderr. A future wave will swap this for a live REPL attachment so
remote prompts drive the local agent directly.

## See also

- [`ide.md`](ide.md) — the ACP wire shared with the bridge.
- [`quickstart.md`](quickstart.md) — first-run walk-through.
- [`security-and-trademarks.md`](security-and-trademarks.md) — why
  the auth token is env-var only.
- `chimera/ferret/cloud_bridge.py` — bridge client implementation.
- `chimera/ferret/cli.py` — CLI subparser + `_dispatch_bridge`.

## Managing backgrounded servers (`serve status` / `serve stop`)

When `chimera ferret serve --http` is launched in the background, the
running PID, port, and a SHA-256 of the auth token are recorded in
`~/.chimera/run/ferret-<port>.pid`. Two subcommands consume that record
so a separate shell can list and graceful-stop those servers without
parsing `ps` / `lsof` output.

### `chimera ferret serve status`

Lists every backgrounded ferret server. One line per pidfile:

```text
ferret port=5174 pid=44321 alive=yes scheme=http auth=yes /Users/you/.chimera/run/ferret-5174.pid
```

`alive=no (stale)` flags a pidfile whose process has exited without a
clean shutdown — `serve stop` reaps it idempotently.

### `chimera ferret serve stop [--port N | --all] [--serve-timeout N]`

Gracefully terminates one or every running ferret server.

- **No arguments**: if exactly one ferret pidfile exists, stop it.
  If more than one is running, exit 2 with a "disambiguate" error.
- **`--port N`**: target only the matching `ferret-<N>.pid` record.
- **`--all`**: stop every backgrounded ferret server.
- **`--serve-timeout N`**: seconds to wait between SIGTERM and the
  SIGKILL escalation. Default `10.0`.

The shutdown sequence is **graceful first**, per the project rule
(`CLAUDE.md`): `SIGTERM` → wait up to `--serve-timeout` seconds → only
escalate to `SIGKILL` when the process is still alive. `SIGKILL` is
never the first signal sent.

The pidfile schema and library API mirror the otter side — see the
[Otter Server doc](../otter/server.md#managing-backgrounded-servers-serve-status--serve-stop)
for the full shape and embedder examples. The pidfile-discovery path
is the same module (`chimera.otter.server_pidfile`) — only the
filename prefix (`ferret-` vs `otter-`) differs, so the two flavors
can coexist on a single host without colliding.

The status / stop subcommands only manage the **HTTP** transport
(`ferret serve --http`). The default ACP transport already runs over
stdio under the parent process — ACP sessions exit with the parent
shell and never need a separate stop subcommand.
