---
title: Otter Sessions
description: Inspect, list, and show persisted otter sessions; on-disk eventlog layout under ~/.chimera/eventlog/otter-*.
---

# `chimera otter sessions`

Every `chimera otter -p PROMPT` invocation, every REPL session, and
every server-driven turn journals its prompt, agent result, tool calls,
and cost data to a fresh directory under
`~/.chimera/eventlog/otter-<utc>-<uuid>/`. This page documents the
on-disk layout, the `sessions list` and `sessions show` subcommands, and
how the data flows back into related surfaces (`share`, the HTTP server,
the REPL `/sessions` command).

For sharing a session with a teammate, see [`share.md`](share.md).
For the server's session endpoints, see [`server.md`](server.md).

## On-disk layout

A persisted otter session lives at:

```
~/.chimera/eventlog/otter-<UTC>-<uuid>/
├── summary.json
├── event-000001-<id>.json
├── event-000002-<id>.json
├── event-000003-<id>.json
└── …
```

The directory name itself is the session id. The `<UTC>` portion is a
compact `YYYYMMDDTHHMMSS` timestamp; the `<uuid>` portion is an 8-char
hex suffix (`uuid4().hex[:8]`) so concurrent invocations never collide.
Lexical sort order on the directory name equals chronological order, so
a plain `ls -1` walks newest-last.

### `summary.json`

Written once per session by `chimera/otter/cli.py:_write_run_summary`
(or its server-side equivalent). Schema:

```json
{
  "run_id": "otter-20260425T091201-71032a5e",
  "session_id": "otter-20260425T091201-71032a5e",
  "agent": "otter",
  "model": "claude-sonnet-4-6",
  "prompt": "list files then read README",
  "title": "Investigate flaky test #4711",
  "cwd": "/Users/yad/repos/chimera",
  "started_at": "2026-04-25T09:12:01Z",
  "ended_at": "2026-04-25T09:12:14Z",
  "steps": 3,
  "tool_calls_total": 4,
  "success": true,
  "cost_usd": 0.0241,
  "total_tokens": 0,
  "error": null
}
```

Notes:

- `session_id` is preferred. `run_id` is kept as a backwards-compatible
  alias because the early scaffold used it; `_summary_to_record` accepts
  either (`chimera/otter/sessions.py:_summary_to_record`).
- `agent: "otter"` lets a future cross-agent viewer (otter + mink) tell
  flavors apart with one read.
- `title` is optional. It is written by `chimera otter -p --title "..."`
  and `chimera otter sessions rename`. Listings fall back to the
  truncated `prompt` when the field is absent or blank.
- `cost_usd: 0.0` does not mean "free" — it means the provider did not
  surface pricing. See [`models.md`](models.md) for which providers
  emit cost.
- `error` is null on success and a short string on failure.

### `event-NNNNNN-<id>.json`

One file per persisted event, written append-style by the underlying
`EventLog` (`chimera/sessions/eventlog/log.py`). The filename's
zero-padded counter preserves chronological order without relying on
filesystem mtimes; the trailing `<id>` is a per-event uuid for uniqueness
under concurrent appenders.

Each file is one event, schema close to:

```json
{
  "id": "evt-7c8a4f1e",
  "type": "tool_call",
  "metadata": {
    "tool": "Read",
    "args": {"path": "README.md"},
    "result": "# Chimera\n…"
  },
  "ts": "2026-04-25T09:12:08Z"
}
```

Common `type` values you'll see: `user_message`, `agent_result`,
`tool_call`, `tool_result`, `text_delta`, `step_start`, `step_end`,
`turn_start`, `turn_end`, `error`. The full enumeration lives in
`chimera/events/`.

### `~/.chimera/shares/`

When you run `chimera otter share <id> --sink file`, the rendered
transcript is written to
`~/.chimera/shares/otter-<id>.<ext>` (extension `.html` / `.md` / `.json`
depending on `--format`). See [`share.md`](share.md) for details.

## `chimera otter sessions list`

Walk `~/.chimera/eventlog/`, filter, and render a fixed-column table
newest-first:

```bash
chimera otter sessions list
```

Example output:

```text
SESSION_ID                              DATE              MODEL              STEPS  COST     OK   PROMPT
otter-20260425T091201-71032a5e          2026-04-25 09:12  claude-sonnet-4-6  3      $0.0241  yes  list files then read README
otter-20260424T184005-2d11c7e0          2026-04-24 18:40  gpt-4o             2      $0.0030  no   generate tests for foo.py
otter-20260423T150010-9f0ab412          2026-04-23 15:00  qwen3:32b          5      $0.0000  yes  refactor module
```

Filters:

```bash
chimera otter sessions list --limit 5
chimera otter sessions list --since 7d
chimera otter sessions list --since 24h
chimera otter sessions list --since 2026-04-01
chimera otter sessions list --model claude-sonnet-4-6
chimera otter sessions list --json
```

`--since` accepts the same shorthand as the rest of Chimera: `Ns`,
`Nm`, `Nh`, `Nd`, `Nw` for relative durations, or any ISO-8601 stamp
for an absolute cutoff (`2026-04-01`, `2026-04-01T12:00:00Z`).
`parse_since()` (`chimera/otter/sessions.py:parse_since`) raises a
clear `ValueError` on bad input; the CLI catches it and exits 2.

`--model` is an exact match against `summary.json`'s `model` field
post-fallback (so if otter fell back to a different tag during the
session, the post-fallback id is what you filter against).

`--json` emits an array of per-session dicts (`SessionRecord.to_dict()`)
for piping into `jq` or another tool:

```bash
chimera otter sessions list --json --since 7d \
  | jq '[.[] | select(.success == false)]'
```

## `chimera otter sessions show`

Print a single session's `summary.json` plus the per-event transcript:

```bash
chimera otter sessions show otter-20260425T091201-71032a5e
```

Flags:

- `--json` emits the full record as a single JSON object (`session_id`,
  `path`, `summary`, `events`).
- `--full` is the human-readable equivalent: every event is printed
  inline rather than summarized. Without `--full` the transcript is
  capped to the first ~30 events for terminal sanity.

Errors:

- Unknown id → exit 2 with `error: session not found: <id>`.
- Session has no `summary.json` (aborted before write) → exit 2 with
  `error: session <id> has no summary.json`.

The id format the loader accepts is the **directory name**:
`otter-20260425T091201-71032a5e`. The leading `otter-` is required so
the loader can disambiguate from mink runs in the same eventlog root.

## `chimera otter sessions cost`

Aggregate `cost_usd` and `total_tokens` across persisted otter sessions
under `~/.chimera/eventlog/otter-*/`. The same rollup the
[`GET /runs/cost`](server.md) HTTP route exposes, served from the CLI:

```bash
chimera otter sessions cost
```

Flags (all optional):

```bash
chimera otter sessions cost --since 7d
chimera otter sessions cost --since 24h
chimera otter sessions cost --since 2026-04-01
chimera otter sessions cost --sessions-model glm-5.1
chimera otter sessions cost --sessions-limit 50
chimera otter sessions cost --format json
chimera otter sessions cost --format csv
```

`--since`, `--sessions-model`, and `--sessions-limit` mirror
`sessions list` semantics so users only learn the filter once. `--format`
selects the output renderer:

- `text` (default) — a totals overview plus a `by model` breakdown.
  Uses `rich` when installed, falls back to a plain ASCII table.
- `json` — the same JSON shape as `chimera mink runs cost --format json`
  and `GET /runs/cost`: `totals`, `filters`, `by_model`, `rows`. Each
  `rows[*].run_id` carries the full `otter-<utc>-<uuid>` id.
- `csv` — per-session rows only (no totals); columns match `CostRow`
  so spreadsheet pivots can re-derive aggregates locally.

Aggregation reuses `chimera.mink.cost.compute_summary` (the same engine
behind `mink runs cost` and the HTTP route), pointed at otter session
dirs through the new `iter_session_run_records()` helper. Schema-wise
the `summary.json` fields read are exactly those `_write_run_summary`
writes today: `run_id` / `session_id`, `started_at`, `model`, `cost_usd`,
`total_tokens`, `success`, `steps`. Optional token-breakdown fields
(`input_tokens`, `output_tokens`, `cache_tokens`) are surfaced when the
schema reports them and default to zero otherwise.

Errors:

- Unparseable `--since` → exit 2 with a clear stderr message naming the
  expected forms (`7d`, `24h`, `30m`, or ISO-8601).
- Unknown `--format` → exit 2 listing the supported values.

Empty corpora return exit 0 with a zero-row totals block (text) or an
empty `rows: []` array (json/csv) so scripts can poll without special
casing "no sessions yet".

## Naming a session

By default `chimera otter sessions list` displays the truncated prompt
in the `TITLE` column — the same heuristic mink uses. For long-lived
sessions (or when the prompt is a generated transcript-prefix that
isn't useful at a glance) you can attach a hand-authored label.

### `--title` on a one-shot run

```bash
chimera otter -p "trace the bug in foo.py" \
  --title "Investigate flaky test #4711"
```

The label is written into `summary.json` under the `title` key
alongside the existing `prompt` field. `sessions list` surfaces it in
the new `TITLE` column; `sessions show` adds a `title:` line to the
summary block. When `--title` is unset (the default), `summary.json`
omits the key and the listing falls back to the truncated prompt — so
existing fixtures and pre-O4 sessions render identically.

### `chimera otter sessions rename <id> <title...>`

Already-saved sessions can be re-titled in place:

```bash
chimera otter sessions rename otter-20260425T091201-71032a5e \
  Refactor the cost rollup
```

The new title can be multi-word without quoting (the variadic
positional joins with spaces). Pass an empty string to clear the
title and revert to the prompt-fallback heuristic:

```bash
chimera otter sessions rename otter-20260425T091201-71032a5e ""
```

Errors:

- Unknown id → exit 2 with `error: session not found: <id>` plus a
  hint to run `chimera otter sessions list`.
- Session has no `summary.json` (aborted before write) → exit 2.
- Malformed `summary.json` → exit 2 with the JSON parse error.

The implementation is a single in-place rewrite of `summary.json`
(`chimera/otter/sessions.py:rename_session`); no event files are
touched and the directory name is preserved so existing share links
and resume ids continue to work.

## REPL `/sessions` slash command

Inside the REPL, `/sessions` calls into the same loader. The default
view is a paged table (last 20 sessions); arguments forward to the
underlying `sessions list` filter:

```
/sessions
/sessions --since 7d
/sessions --model gpt-4o --limit 5
```

`/session` (singular) shows the **active** REPL session's id and
message count. `/sessions show <id>` opens a previous one in
read-only mode.

## Resuming a session

Otter event logs are designed to be replayable. The cross-cutting
`SessionTree` and `EventSourcedSession` primitives in
`chimera/sessions/` already understand the same on-disk schema mink
uses, so:

```python
from chimera.sessions.eventlog import EventSourcedSession
session = EventSourcedSession.resume(
    log_dir="~/.chimera/eventlog/otter-20260425T091201-71032a5e",
    session_id="otter-20260425T091201-71032a5e",
    agent=my_agent,
)
```

A first-class `chimera otter --resume <id>` flag is on the roadmap; for
now, resume is exercised through the HTTP server (`POST /sessions/{id}/turns`
extends an existing session) and the REPL slash commands.

## Persistence opt-out

Pass `--no-save` on a one-shot run to skip eventlog persistence
entirely:

```bash
chimera otter -p "ad-hoc, don't journal" --no-save
```

The agent still streams + tools to stdout, but no `summary.json` or
`event-*.json` files are written. The REPL has no equivalent flag —
REPL sessions are always persisted because the slash-command surface
(`/undo`, `/share`, `/sessions show`) needs them.

## Pruning old sessions

Everything is local plaintext. To purge:

```bash
rm -rf ~/.chimera/eventlog/otter-*
```

To purge older than 30 days:

```bash
find ~/.chimera/eventlog -maxdepth 1 -type d -name 'otter-*' -mtime +30 -exec rm -rf {} +
```

A periodic GC subcommand (`chimera otter sessions gc --older-than 30d`)
is a follow-up tracked in the parity matrix.

## Cross-flavor coexistence

Otter and mink share `~/.chimera/eventlog/`. They never collide because
the directory prefix disambiguates: `mink-*` for mink, `otter-*` for
otter. `chimera mink runs list` skips `otter-*`; `chimera otter sessions
list` skips `mink-*` by default — but the `--all-clis` flag (B9-W11)
opts into a combined view.

## Cross-CLI sessions

By default `chimera otter sessions list` filters to `otter-*` directories,
matching the historic per-CLI scope. Pass `--all-clis` to fold in every
other Chimera CLI's sessions (`mink-`, `ferret-`, `weasel-`, `shrew-`,
`stoat-`, `badger-`) into one chronological view; the table grows an
`ORIGIN` column so you can tell which CLI wrote each row, and `--json`
output adds a top-level `cli_origin` field per record. `sessions show
<id>` already resolves a session by directory name regardless of which
CLI created it, so an otter operator can pull up a ferret session
transcript with the id alone — no flag required. The shared walker
lives at `chimera/sessions/eventlog/cross_cli.py` and is consumed by
every per-CLI `sessions.py` via `iter_sessions(all_clis=True)`.

## See also

- [`share.md`](share.md) — package a session into a portable HTML/JSON/MD
  transcript.
- [`server.md`](server.md) — `GET /sessions` and `GET /sessions/{id}`
  endpoints expose the same data over HTTP + SSE.
- [`docs/mink/runs.md`](../mink/runs.md) — sibling viewer for mink
  runs; same on-disk schema.
- [`docs/mink/sessions.md`](../mink/sessions.md) — deeper write-up of the
  shared `SessionTree`, `EventSourcedSession`, compaction, and resume
  primitives. Otter inherits all of them.
