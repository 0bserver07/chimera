---
title: "Mink Runs"
description: "Inspect and share persisted one-shot mink runs that live under ~/.chimera/eventlog/."
---

# `chimera mink runs`

Inspect and share persisted one-shot mink runs that live under
`~/.chimera/eventlog/mink-<utc>-<uuid>/`. Every `chimera mink -p PROMPT`
invocation journals its prompt, agent result, tool calls, and cost data
to a fresh directory there.

## List

```
chimera mink runs list [--limit N] [--runs-model NAME] [--success-only | --failed-only]
```

Renders a fixed-column table, newest first.

## Show

```
chimera mink runs show <run-id> [--no-events]
```

Prints metadata plus the full event transcript. Use `--no-events` to
restrict output to the summary block.

## Sharing

Package a run into a portable token you can email, paste, or hand off to
a teammate. Three sinks are supported:

```
chimera mink runs share <run-id> --sink file     # default
chimera mink runs share <run-id> --sink gist
chimera mink runs share <run-id> --sink base64
```

* `file` writes `~/.chimera/exports/<run-id>.tar.gz` and prints the
  absolute path. Works offline; no auth required.
* `gist` shells out to `gh gist create -p <tarball>`. Requires the
  GitHub CLI (`brew install gh`) and an active `gh auth login` session.
  Prints the resulting gist URL.
* `base64` returns a `data:application/x-mink-session;base64,...` URI
  suitable for inline pastes (chat, email, SMS). The payload is the
  same gzipped tarball — just encoded.

### Importing a shared run

```python
from chimera.sessions.share import import_from_url

# Accepts a gist URL, local file path, or data: URI.
run_id = import_from_url("https://gist.github.com/<owner>/<id>")
# run_id is now extracted under ~/.chimera/eventlog/<run_id>/
```

After import you can `chimera mink runs show <run-id>` to inspect it
locally, or resume it via
`EventSourcedSession.resume(eventlog_root, run_id, agent=...)`.

The export format is a gzip-compressed tar archive of the run's
eventlog directory (`summary.json` + every `event-*.json` file). It is
self-describing and stable across chimera versions as long as the
eventlog schema does not change.

## Cost

Aggregate spend across persisted runs. Walks the same
`~/.chimera/eventlog/mink-*/summary.json` corpus the rest of the `runs`
subcommand uses, so cost rollups are always consistent with what
`runs list` shows.

```
chimera mink runs cost [--since 7d|24h|<ISO>] [--runs-model NAME|all]
                       [--format text|json|csv] [--limit N]
```

Default output is a human-readable table (rich when installed, plain
ASCII otherwise) with totals, p50 / p95, and a per-model breakdown:

```
mink runs cost
========================================
  runs:                  37
  success / fail:        34 / 3
  total cost:            $1.2345
  avg cost / run:        $0.0334
  p50 cost:              $0.0210
  p95 cost:              $0.1520
  total tokens:          412305
  input/output/cache:    312000 / 88000 / 12305

by model:
  MODEL                       RUNS        COST      TOKENS
  GLM-5                         22     $0.9100      280000
  kimi-k2.6:cloud               15     $0.3245      132305
```

Flags:

* `--since 7d` — accepts `Nd`, `Nh`, `Nm` shorthand or any ISO-8601
  date (`2026-04-20`, `2026-04-20T12:00:00Z`). Drops runs whose
  `started_at` is older than the cutoff.
* `--runs-model NAME` — case-insensitive substring match against the
  run's `model` field. Use `all` (or omit) to include every model.
  This is the same flag `runs list` uses.
* `--format text|json|csv` — `json` returns a machine-readable summary
  (`totals` + `by_model` + per-run `rows`); `csv` emits per-run rows
  for spreadsheet ingest.
* `--limit N` — cap to the N most-recent runs after `--since` /
  `--runs-model` have been applied. `0` (default) means no cap.

Stdlib only — no extra deps required even when the `mink` extra is not
installed.
