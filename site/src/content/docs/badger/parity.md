---
title: Parity Schema Check
description: Diff the live badger agent against a declared schema (PARITY.md / .json / .yaml).
---

# Parity

Badger ships a `parity` subcommand that diffs the live agent's
behaviour against a declared schema. This translates the
upstream's PARITY.md *pattern* into a runtime check.

## Why a runtime parity check?

A static parity table goes stale the moment the live code drifts.
Encoding the contract as a schema and diffing it at runtime makes
drift detectable in CI:

```bash
chimera badger parity --against PARITY.md
echo $?   # 0 = match; 1 = drift; 2 = usage error
```

## Schema format

Three formats are accepted:

### JSON

```json
{
  "tools": ["bash", "read_file", "edit_file", "search"],
  "max_steps": 25,
  "slash_commands": ["help", "model", "tools", "parity", "rerun"],
  "model": "claude-sonnet-4-6",
  "rerun_on_failure": false
}
```

### YAML-ish

```yaml
model: claude-sonnet-4-6
max_steps: 25
rerun_on_failure: false
tools:
  - bash
  - read_file
  - edit_file
slash_commands:
  - help
  - parity
```

### PARITY.md (Markdown with a fenced block)

```
# Parity schema

```
{"max_steps": 25, "model": "claude-sonnet-4-6"}
```
```

The first fenced code block is parsed.

## Schema fields

| Field               | Type              | Meaning                                         |
| ------------------- | ----------------- | ----------------------------------------------- |
| `tools`             | `list[str]`       | Tool names that **must** be present.            |
| `max_steps`         | `int`             | Expected default `--max-steps` value.           |
| `slash_commands`    | `list[str]`       | Slash command names the REPL must expose.       |
| `model`             | `str`             | Expected default model id.                      |
| `rerun_on_failure`  | `bool`            | Expected default for the flag.                  |

Only declared fields are enforced. The diff is **asymmetric**: live
*extras* (tools or slash commands present live but absent from the
schema) are reported informationally but never fail the check.

## Output

### Text (default)

```
$ chimera badger parity --against PARITY.json
badger parity: OK (live agent matches schema)
```

```
$ chimera badger parity --against PARITY.json
badger parity: FAIL
  missing tools (1): browser
  max_steps mismatch: expected=25 actual=50
```

### JSON

```bash
chimera badger parity --against PARITY.json --output-format json
```

Returns a payload with `expected`, `live`, and `report` sub-objects so
you can wire downstream tooling (CI, dashboards) against the structured
output.

## Auto-resolve

When `--against` is omitted, badger searches the working directory for
`PARITY.md`, `PARITY.json`, `PARITY.yaml`, or `parity.md` (in that
order). Pass `--cwd` to scope the search.

## REPL: `/parity`

Inside the badger REPL, the `/parity` slash command runs the same
check:

```
> /parity
badger parity: OK (live agent matches schema)
> /parity ./PARITY.json
...
```
