---
title: Stoat Sessions
description: List, inspect, cost-rollup, and share persisted stoat sessions.
---

# Stoat Sessions

Every persisted stoat run journals its prompt, agent result, and event
trail under `~/.chimera/eventlog/stoat-<utc>-<uuid>/`. The on-disk
schema mirrors the other coding-agent CLIs (mink/otter/ferret/weasel/
shrew) so a single parser handles every flavour.

This page covers the four `sessions` / `share` actions and the share
sinks. Implementation lives in `chimera.stoat.sessions`; the helper
functions are stable for embedders.

## On-disk layout

```
~/.chimera/eventlog/stoat-20260430T101455-1f3c2a8b/
  summary.json
  event-000001-tool_call.json
  event-000002-tool_result.json
  event-000003-text_delta.json
  …
```

`summary.json` is the run's compact record (model, prompt, success,
cost, steps, tool-call count, error). The `event-NNNNNN-<type>.json`
files are an ordered append-only log written as the run streams.

## `chimera stoat sessions list`

```bash
chimera stoat sessions list
```

```text
STARTED           ID                                    OK   STEPS  PROMPT
--------------------------------------------------------------------------------
2026-04-30 10:14  stoat-20260430T101455-1f3c2a8b        yes      4  list the top-level files
2026-04-30 09:33  stoat-20260430T093312-aa90c2e0        yes      2  ship it

2 session(s)
```

Add `--json` to the global stoat args to render a JSON array (one entry
per session), suitable for piping into `jq`.

## `chimera stoat sessions show <id>`

```bash
chimera stoat sessions show stoat-20260430T101455-1f3c2a8b
```

```text
session  stoat-20260430T101455-1f3c2a8b
path     /Users/me/.chimera/eventlog/stoat-20260430T101455-1f3c2a8b
started  2026-04-30T10:14:55Z
ended    2026-04-30T10:15:30Z
model    kimi-k2.6
success  True
steps    4

prompt:
list the top-level files and read the README

events: 12 record(s)
```

`--json` renders the full `{summary, events, path}` object (round-trips
with `chimera stoat share --share-format json`).

## `chimera stoat sessions cost`

The cost rollup is shared with mink / weasel / shrew so dashboards stay
one-parser.

```bash
chimera stoat sessions cost
chimera stoat sessions cost --since 7d --cost-format json
chimera stoat sessions cost --cost-model kimi --cost-format csv
```

Flags:

| Flag | Meaning |
|---|---|
| `--since` | Drop sessions older than this cutoff. Accepts `7d` / `24h` / `30m` / ISO-8601. |
| `--cost-model` | Case-insensitive substring filter on model name. `all` (or omit) = every model. |
| `--cost-format` | `text` (default) / `json` / `csv`. |
| `--cost-limit` | Cap rows considered (newest first). No cap by default. |

The JSON / CSV / text body is byte-identical to `chimera mink runs cost`
and `chimera weasel sessions cost`.

## `chimera stoat share <id>`

Render a session for offline review or attachment to a ticket.

```bash
chimera stoat share stoat-20260430T101455-1f3c2a8b
chimera stoat share stoat-20260430T101455-1f3c2a8b --share-format md
chimera stoat share stoat-20260430T101455-1f3c2a8b --share-sink stdout
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--share-sink` | `file` | `file` writes to `~/.chimera/shares/stoat-<id>.<ext>`. `stdout` prints the body and exits. |
| `--share-format` | `json` | `json` round-trips with `sessions show --json`. `md` is GitHub-flavored Markdown. |

HTTP / HTML sinks are intentionally omitted — stoat keeps the share
surface small (mirrors weasel). The share file is written with
`mkdir -p` semantics; the directory is created on first use.

## Embedding the API

The same helpers power the CLI and embedders:

```python
from chimera.stoat.sessions import iter_sessions, get_session, cmd_share

for record in iter_sessions():
    print(record.session_id, record.cost_usd)

detail = get_session("stoat-20260430T101455-1f3c2a8b")
print(detail.summary["model"], len(detail.events))

cmd_share("stoat-20260430T101455-1f3c2a8b", sink="file", fmt="md")
```

Every helper accepts an `eventlog_root` / `shares_dir` override so
tests can isolate against a temp directory.

## See also

- [`quickstart.md`](quickstart.md) — install + first run.
- [`shell-mode.md`](shell-mode.md) — the toggle.
- `chimera/stoat/sessions.py` — the source.
- [`chimera mink runs`](../mink/runs.md) — shared rollup machinery.
