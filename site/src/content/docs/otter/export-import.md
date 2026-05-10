---
title: chimera otter export / import — round-trip a session
description: Pack a session's summary.json + every event-*.json into a single file (json / md / html); replay the JSON form on another machine or after ~/.chimera/eventlog/ has been pruned.
---

# `chimera otter export` / `import` — round-trip a session

Pack a session's `summary.json` + every `event-*.json` into a single
file (`json` / `md` / `html`); replay the JSON form on another machine
or after `~/.chimera/eventlog/` has been pruned.

## Usage

```bash
chimera otter export <SESSION_ID> [--export-format json|md|html] [--export-output PATH]
chimera otter import <FILE> [<NEW_ID>] [--import-overwrite]
```

* `<SESSION_ID>` — directory name under `~/.chimera/eventlog/`
  (e.g. `otter-20260507T120000-71032a5e`).
* `<FILE>` — JSON envelope produced by `export --export-format json`.
  Markdown / HTML are read-only — `import` only accepts the JSON form
  because it is the lossless representation.
* `<NEW_ID>` — optional rename target on `import` so a clone can land
  with a fresh id without mutating the source file.

When `--export-output` is omitted the rendered output prints to stdout
so you can pipe it to `gist`, `pbcopy`, or your favorite share sink.

## Examples

```bash
# Export the last otter session as Markdown.
chimera otter export $(ls -t ~/.chimera/eventlog | grep '^otter-' | head -1) \
  --export-format md --export-output /tmp/transcript.md

# Round-trip a session to a coworker.
chimera otter export otter-20260507T120000-71032a5e \
  --export-output session.json
scp session.json coworker:~/
ssh coworker "chimera otter import ~/session.json"

# Replay it locally under a new id (e.g. so you can A/B compare two
# versions of the same prompt).
chimera otter import session.json otter-replay-1
```

## Schema

JSON envelope:

```json
{
  "schema": "chimera.otter.session/1",
  "summary": { ...summary.json contents... },
  "events":  [ ...event-*.json contents... ],
  "exported_at": "2026-05-07T12:34:56Z"
}
```

Round-trip guarantee: `import_session(export_session(<id>))` recreates
the same on-disk layout (modulo the `exported_at` envelope key).

## Renderers

* **`json`** — lossless. Re-importable.
* **`md`** — readable transcript. Heads with the summary as a
  bullet-list, then walks events as fenced code blocks. Falls back to
  `json` blocks for events without a textual `text` / `content` field.
* **`html`** — single self-contained `<!doctype html>` document
  wrapping the same transcript inside a `<pre>` block; opens in any
  browser without a build step.

## How it works

`chimera/otter/export_import.py` reuses
[`chimera.otter.sessions.get_session`][get_session] to load a session
and `chimera.otter.sessions.default_eventlog_root` so a custom eventlog
root (test fixtures, mounted volumes) is honored end-to-end. Imports
write `summary.json` + `event-NNNNNN.json` so the existing
`otter sessions show <id>` inspection works out of the box.

[get_session]: https://0bserver07.github.io/chimera/api/otter/sessions/#get_session
