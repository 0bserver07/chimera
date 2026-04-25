---
title: Otter Share
description: Package and share a persisted otter session via file, HTTP, or stdout sinks; HTML, JSON, and Markdown formats.
---

# `chimera otter share`

`chimera otter share <session-id>` packages a persisted session into a
self-contained transcript and routes it to one of three sinks. It is the
otter analogue of [`chimera mink runs share`](../mink/runs.md#sharing),
but with a different default endpoint policy: otter never points at a
third-party hosted share service. The default HTTP target is a local
placeholder (`http://localhost:5174/api/shares`); pointing it elsewhere
is your call.

For the underlying session data this command renders, see
[`sessions.md`](sessions.md).

## Quick reference

```bash
chimera otter share <session-id>                          # default: file sink, HTML format
chimera otter share <session-id> --sink file --format md
chimera otter share <session-id> --sink stdout --format json | jq .
chimera otter share <session-id> --sink http
chimera otter share <session-id> --sink http --url https://collector.internal/shares
```

`<session-id>` is the directory name from
`~/.chimera/eventlog/otter-*` (e.g.
`otter-20260425T091201-71032a5e`).

## Sinks

| Sink | What it does | Auth |
|---|---|---|
| `file` (default) | Writes `~/.chimera/shares/otter-<id>.<ext>` and prints the absolute path. | none — local disk |
| `http` | POSTs the rendered body to `--url` / `$OTTER_SHARE_URL` / the local default. | whatever your endpoint enforces |
| `stdout` | Writes the rendered body to stdout. | none |

### `file` sink

Writes `~/.chimera/shares/otter-<session-id>.<ext>` and prints the
absolute path on stdout. The shares directory is created lazily.

```bash
chimera otter share otter-20260425T091201-71032a5e
# /Users/yad/.chimera/shares/otter-20260425T091201-71032a5e.html

chimera otter share otter-20260425T091201-71032a5e --format md
# /Users/yad/.chimera/shares/otter-20260425T091201-71032a5e.md
```

The HTML file is **self-contained** — no external CSS, no fonts, no
script tags — so it round-trips through email or a static file host
without losing fidelity.

### `http` sink

POSTs the rendered body to a URL. The URL is resolved with this
precedence:

1. `--url <URL>` flag.
2. `$OTTER_SHARE_URL` environment variable.
3. `http://localhost:5174/api/shares` (the local placeholder default).

Trademark hygiene note: the default is **deliberately local**. Otter
will never POST your session to a third-party brand's share endpoint
without an explicit opt-in. If you want a hosted target, set it
yourself:

```bash
export OTTER_SHARE_URL=https://collector.example.com/api/shares
chimera otter share <id> --sink http
```

Headers sent by the client:

| Header | Value |
|---|---|
| `Content-Type` | `text/html; charset=utf-8` (or `text/markdown; charset=utf-8` / `application/json`) |
| `User-Agent` | `chimera-otter-share/1` |

The HTTP sink uses stdlib `urllib.request` (no `httpx` required). On
2xx, the endpoint's response body is written to stdout — collectors
typically reply with a JSON document carrying the canonical share URL,
so a CI pipeline can pipe the result downstream. On non-2xx, the CLI
exits 1 with the status code and the first response line on stderr.

### `stdout` sink

Writes the rendered body directly to stdout, no file, no network. Handy
for piping into `less`, archiving into a tarball with other artifacts,
or feeding into another tool:

```bash
chimera otter share <id> --sink stdout --format md | less
chimera otter share <id> --sink stdout --format json \
  | jq '.events[] | select(.type == "tool_call")'
```

## Formats

| Format | Extension | Content-Type | Use when |
|---|---|---|---|
| `html` (default) | `.html` | `text/html` | sharing with humans; rendering in a browser. |
| `md` | `.md` | `text/markdown` | pasting into a PR / issue / Slack canvas. |
| `json` | `.json` | `application/json` | feeding into another tool / programmatic consumer. |

### HTML format

The HTML body is a single `<html>` document with inline `<style>`. The
top is a metadata table (model, started/ended, steps, tool calls, cost,
success, error). Then the prompt in a `<pre>` block. Then one
`<div class="event">` per persisted event, each rendering its
`metadata` as a JSON code block. Even viewers that strip `<style>`
produce readable text.

### Markdown format

GitHub-flavored. Top section: a metadata bullet list and a fenced
prompt block. Then `## Events (<n>)`, followed by per-event sections
(`### \`<event-type>\``) with the metadata in `json` fenced blocks.

### JSON format

Mirrors `chimera otter sessions show --json`:

```json
{
  "session_id": "otter-20260425T091201-71032a5e",
  "summary": { "model": "claude-sonnet-4-6", "...": "..." },
  "events": [
    { "id": "evt-...", "type": "user_message", "metadata": { "...": "..." } },
    { "id": "evt-...", "type": "tool_call",   "metadata": { "...": "..." } }
  ]
}
```

Use this format when the consumer is another program. Pretty-printed
two-space indent; UTF-8.

## Share schema (the shape on the wire)

The `http` sink POSTs the body verbatim — the rendered transcript is
the entire payload. The collector decides what to do with it: store it,
reply with a canonical URL, attach metadata, etc. Otter does not assume
any particular response schema. The CLI prints whatever the endpoint
sends back so callers can pipe it into a follow-up step.

A reasonable collector contract (recommended, not required) when you
roll your own:

```http
POST /api/shares
Content-Type: application/json
Body: {"session_id": "...", "summary": {...}, "events": [...]}

200 OK
Content-Type: application/json
Body: {"url": "https://collector.example.com/s/<short-id>"}
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. File path / endpoint response on stdout. |
| `1` | Runtime failure: HTTP non-2xx, network error, disk error. |
| `2` | Usage failure: missing `<session-id>`, unknown `--sink` / `--format`, session id not found. |

## Examples

### Share to a teammate via local file

```bash
chimera otter share otter-20260425T091201-71032a5e
# /Users/yad/.chimera/shares/otter-20260425T091201-71032a5e.html
open ~/.chimera/shares/otter-20260425T091201-71032a5e.html
```

### Push to an internal collector

```bash
export OTTER_SHARE_URL=https://collector.internal/api/shares
chimera otter share otter-20260425T091201-71032a5e --sink http --format json
# {"url": "https://collector.internal/s/Ab1Cd2Ef"}
```

### Stream the most recent session into a Markdown clipboard

```bash
LAST=$(chimera otter sessions list --limit 1 --json | jq -r '.[0].session_id')
chimera otter share "$LAST" --sink stdout --format md | pbcopy
```

### Bulk-archive every session from the last week

```bash
chimera otter sessions list --since 7d --json \
  | jq -r '.[].session_id' \
  | while read sid; do
      chimera otter share "$sid" --format md > /dev/null
    done
ls ~/.chimera/shares/
```

## Privacy and trademark notes

- Persisted sessions can carry sensitive content — file paths, prompts,
  tool args, secrets your tools touched. Sharing externally is your
  decision; otter's default is to write locally.
- Otter never auto-shares. The upstream open-source agent has an
  "auto-share" mode; otter deliberately ships only manual sharing.
- The default `OTTER_SHARE_URL` is **localhost**. Otter will never
  silently POST to a third-party hosted endpoint.
- Secret redaction at write-time is on the roadmap. For now, treat the
  shared transcript as it appears on disk: assume what you see is what
  the recipient gets.

## See also

- [`sessions.md`](sessions.md) — what's in `summary.json` and the
  `event-*.json` files that share renders.
- [`server.md`](server.md) — the `GET /sessions/{id}/transcript`
  endpoint exposes the same renderings over HTTP.
- [`docs/mink/runs.md#sharing`](../mink/runs.md#sharing) — sibling
  command for mink runs (different sinks: `file` / `gist` / `base64`).
