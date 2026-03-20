---
title: "CLI & REPL API Reference"
description: "CLI & REPL API Reference"
---

::: chimera.cli.main
    options:
      show_root_heading: true
      members_order: source

::: chimera.cli.code
    options:
      show_root_heading: true
      members_order: source

## New flags (pi-mono)

### `chimera code` flags

| Flag | Description |
|------|-------------|
| `--mode interactive\|rpc\|json` | Terminal mode: `interactive` (default readline REPL), `rpc` (JSON-RPC 2.0 over stdio), `json` (newline-delimited JSON objects) |
| `--models <list>` | Comma-separated list of model names to cycle through; use `/model next` or `/model prev` in the REPL to switch |

### New slash commands

| Command | Description |
|---------|-------------|
| `/tree` | Display the full session branch tree managed by `SessionTree` |
| `/branch [name]` | Create a new named branch forked from the current session |
| `/switch <name>` | Switch to an existing named branch |
| `/model next` | Advance to the next model in the `--models` list |
| `/model prev` | Go back to the previous model in the `--models` list |
