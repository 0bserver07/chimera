---
title: Stoat Parity Matrix
description: Surface-by-surface parity status against the upstream shell-mode-toggle harness.
---

# Stoat Parity Matrix

Stoat mirrors a real-world coding-agent harness in the **shell-mode-
toggle** tradition — the upstream lets users press a chord to flip the
prompt buffer between an LLM agent and a direct shell, with both modes
driven by the same context. We never name the upstream brand here
(see [`security-and-trademarks.md`](security-and-trademarks.md)); this
page documents which surface ports we ship, which we skip, and why.

The "Status" column uses the same vocabulary as the other parity
matrices in this repo:

* **Tier 1 (live-driven)** — implemented, exercised by tests against
  the live framework, surface-stable.
* **Tier 2 (scaffolded)** — surface present and dispatched, body still
  comes from a follow-up agent.
* **Skipped** — out of scope for stoat (or covered by a sibling CLI).

## Headline ergonomic

| Surface | Status | Notes |
|---|---|---|
| Shell-mode toggle (`/shell`) | Tier 1 | Slash command + `--shell-mode` flag flip the REPL between agent mode (`stoat>`) and shell mode (`stoat$`). |
| `Ctrl-X` chord | Tier 2 | Documented, but the slash form is the default — chord binding is per-terminal config. |
| Mode-tagged history | Tier 1 | `/history` renders `>` for agent lines, `$` for shell lines. |
| Persistent cwd across shell turns | Skipped | Each shell command runs in its own `bash -c` subprocess; `cd` is intentionally not honored. Use `--cwd` to pin the working directory. |

## CLI surface

| Surface | Status | Notes |
|---|---|---|
| `chimera stoat --version` | Tier 1 | Prints `chimera stoat 0.5.0`. |
| `chimera stoat -p PROMPT` | Tier 1 | One-shot agent turn. Honors `--allowed-tools`, `--max-steps`, `--cwd`, `--json`. |
| `chimera stoat --mode rpc` | Tier 1 | Delegates to `chimera.cli.code` in JSON-RPC mode. |
| `chimera stoat --mode print` (without `-p`) | Tier 1 | Usage error, exit 2. |
| `chimera stoat sessions list` | Tier 1 | Walks `~/.chimera/eventlog/stoat-*/`. |
| `chimera stoat sessions show <id>` | Tier 1 | Loads `summary.json` + every `event-*.json`. |
| `chimera stoat sessions cost` | Tier 1 | Re-uses `chimera.mink.cost.compute_summary`. |
| `chimera stoat share <id>` | Tier 1 | JSON / Markdown render to file or stdout. |
| `chimera stoat agents list/show` | Tier 2 | Handler stub; eventual integration with shared agent registry. |
| `chimera stoat bench <suite>` | Tier 2 | Handler stub; HumanEval / tau-bench wiring follows shrew/ferret. |
| `chimera stoat serve` | Tier 2 | ACP / IDE-extension server is a follow-up. Use `chimera ferret serve --http` for the cross-CLI HTTP transport. |

## Provider chain

| Surface | Status | Notes |
|---|---|---|
| Kimi-first via `$MOONSHOT_API_KEY` | Tier 1 | Default model `kimi-k2.6`, OpenAI-compatible against `api.moonshot.ai/v1`. |
| `$STOAT_MODEL` env override | Tier 1 | Pin a model id without setting any provider key. |
| Anthropic / OpenAI fallback | Tier 1 | Per `chimera.providers.factory.create_provider` prefix inference. |
| OpenRouter via `vendor/name` ids | Tier 1 | `$OPENROUTER_API_KEY` + slash-shaped id. |
| Ollama via `name:tag` ids | Tier 1 | `$OLLAMA_HOST` / `OLLAMA_API_KEY`. |
| Auto-resolved model probe (no key) | Tier 1 | Friendly `ValueError` listing every supported env var. |

See [`providers.md`](providers.md) for the full chain table.

## Slash palette

Stoat ships a deliberately small palette to keep the toggle the focus.

| Slash | Status | Notes |
|---|---|---|
| `/help` | Tier 1 | Renders the palette. |
| `/exit` / `/quit` | Tier 1 | Break the loop. |
| `/clear` | Tier 1 | Drops conversation history. |
| `/model` | Tier 1 | Show or set the active model id. |
| `/shell` | Tier 1 | The headline toggle. |
| `/cost` | Tier 1 | Running USD rollup. |
| `/history` | Tier 1 | Mode-tagged review. |
| `/init`, `/agent`, `/checkpoint`, `/yolo`, `/branch`, `/switch` | Skipped | Available via `chimera code` for users who want the full 19-slash palette. |

## Sessions / share

| Surface | Status | Notes |
|---|---|---|
| Eventlog at `~/.chimera/eventlog/stoat-*/` | Tier 1 | Mirrors weasel/shrew/otter. |
| Cost rollup parity (text/JSON/CSV) | Tier 1 | Same body as mink/weasel/shrew. |
| Share to `~/.chimera/shares/stoat-*.*` | Tier 1 | JSON + Markdown. |
| Share via HTTP / HTML | Skipped | mink/otter ship the full sink palette; stoat stays minimal. |

## What we explicitly do not ship

* **VS Code extension parity** — stoat does not bundle an editor
  extension. The cross-CLI ACP / HTTP transports live under ferret /
  otter; users who want IDE integration can drive ferret's ACP server
  pointed at a stoat-flavored agent factory.
* **Zsh shell plugin** — stoat does not ship a shell plugin. The
  toggle is internal to the stoat REPL.
* **MCP `mcp` sub-command group** — MCP integration is reachable via
  the cross-CLI plugin loader (see `chimera plugins`). Stoat's
  per-CLI surface stays small.
* **OAuth login flow** — stoat reads `$MOONSHOT_API_KEY` from the env
  (filesystem path `~/.kimi/config.json` is referenced in upstream docs
  as a fact about the host CLI's config layout, not something stoat
  reads). Operators wanting OAuth-issued tokens can use the shared
  `chimera.auth` flow.

## See also

- [`quickstart.md`](quickstart.md)
- [`shell-mode.md`](shell-mode.md)
- [`providers.md`](providers.md)
- [`security-and-trademarks.md`](security-and-trademarks.md)
