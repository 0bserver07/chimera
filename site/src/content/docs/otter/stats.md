---
title: chimera otter stats — usage stats rollup
description: stats aggregates the eventlog across both mink- and otter- runs to surface token totals, per-model breakdown, cost percentiles, and a since-window slice in one go.
---

# `chimera otter stats` — usage stats rollup

`stats` aggregates the eventlog across both `mink-*` and `otter-*` runs
to surface token totals, per-model breakdown, cost percentiles, and a
since-window slice in one go.

## Usage

```bash
chimera otter stats [--stats-since 7d] [--stats-model NAME] [--stats-format text|json]
```

Aliases: `--since` and `--sessions-model` (already wired by O3) flow
through here as fallbacks, so the same flags you use with
`chimera otter sessions cost` work with `stats` too.

## Output

### Text (default)

```
otter stats — since 7d
======================
runs                42
  success / fail    40 / 2
total cost          $1.2300
avg cost / run      $0.0292
p50 cost            $0.0150
p95 cost            $0.0900

tokens
  input             125000
  output            45000
  cache             18000
  total             188000

by model
  glm-5                   30 runs   $0.7100        110000 tokens
  claude-sonnet-4-6       12 runs   $0.5200         78000 tokens
```

### JSON

```bash
chimera otter stats --stats-format json
```

Returns a flat shape:

```json
{
  "total_runs": 42,
  "successful_runs": 40,
  "failed_runs": 2,
  "total_cost_usd": 1.23,
  "total_tokens": 188000,
  "total_input_tokens": 125000,
  "total_output_tokens": 45000,
  "total_cache_tokens": 18000,
  "avg_cost_usd": 0.0293,
  "p50_cost_usd": 0.015,
  "p95_cost_usd": 0.09,
  "by_model": {
    "glm-5": {"runs": 30, "cost_usd": 0.71, "tokens": 110000}
  },
  "since": "7d",
  "model_filter": null
}
```

## How it works

`chimera/otter/stats.py` walks `~/.chimera/eventlog/` for `mink-*` and
`otter-*` directories, lifts each `summary.json` into a `RunRecord`,
and routes the stream through
[`chimera.mink.cost.compute_summary`][compute] so the JSON shape is a
strict superset of `chimera mink runs cost --format json`.

[compute]: https://0bserver07.github.io/chimera/api/mink/#compute_summary

## Filters

`--stats-since` accepts the same shorthand as `parse_since`:

* `7d` / `24h` / `30m` — relative window from now.
* ISO-8601 dates — `2026-04-20`, `2026-04-20T12:00:00Z`.

`--stats-model` does a case-insensitive substring match on the model
name (`claude` matches `claude-sonnet-4-6`, `claude-haiku-4-5`, …).
