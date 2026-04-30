---
title: Weasel Parity Matrix
description: Surface-by-surface mapping between chimera weasel and the upstream minimal harness — GREEN (at parity / superset), YELLOW (partial), RED (deferred or out of scope).
---

# `chimera weasel` Parity Matrix

**Source baseline:** `research/weasel/SPEC.md` (Apr 2026), upstream
minimal-harness source tree walk under `packages/coding-agent/`.
**Updated:** wave-1 ship.
**Legend:** GREEN = shipped / at parity (or superset); YELLOW = partial; RED = deferred or out of scope.

> **Trademark hygiene.** Throughout this document the upstream
> project is referred to as "the minimal harness" or "the upstream".
> Live references to filesystem paths such as `.pi/` are kept
> because they are facts about directories weasel can opportunistically
> read on disk for users migrating, not brand claims. See
> [`security-and-trademarks.md`](security-and-trademarks.md).

## Top-level surfaces

The upstream ships a single `pi` binary with four modes. Weasel
mirrors the four-mode philosophy and reuses Chimera's
infrastructure (eventlog, permissions, providers) for everything
else.

| Upstream surface | Weasel status | File | Notes |
|---|---|---|---|
| Interactive REPL | GREEN | `chimera/weasel/repl.py` | Streaming, mid-turn steering, `Ctrl-C` cancel, slash commands. |
| Print mode (`-p`) | GREEN | `chimera/weasel/modes.py` | Plain text, `--json`, NDJSON `--stream-json`. |
| Print mode JSON | GREEN | `chimera/weasel/modes.py` | `chimera weasel -p "..." --json`. |
| RPC mode (stdio) | GREEN | `chimera/weasel/rpc.py` | JSON-RPC 2.0, methods: `prompt`, `steer`, `cancel`, `get_state`, `compact`. |
| SDK | GREEN | `chimera/weasel/sdk.py` | `from chimera.weasel.sdk import Agent`; sync + async. |
| `--list-models` | GREEN | `chimera/weasel/cli.py` | Provider-driven catalogue. |
| Sessions list / show | GREEN | `chimera/weasel/sessions.py` | Reads `~/.chimera/eventlog/weasel-*/`. |
| Session resume | GREEN | `chimera/weasel/sessions.py` | `--resume <id>`; SDK `agent.resume(id)`. |
| Extension auto-discovery | GREEN | `chimera/weasel/extensions.py` | `.weasel/extensions/*.{py,js,ts}` + `~/.weasel/extensions/`. |
| Settings file | GREEN | `chimera/weasel/cli.py` | `.weasel/settings.json` (model, allowed extensions). |

## CLI flag map

The upstream `pi` binary exposes a tight flag set (one-shot,
streaming, model selection). Weasel mirrors the flags that affect
agent semantics and adds the JSON / NDJSON outputs from the broader
Chimera CLI vocabulary.

| Upstream flag | Weasel status | Weasel equivalent | Notes |
|---|---|---|---|
| `-p` / `--print` | GREEN | `-p` / `--print` | Identical. |
| `--json` | GREEN | `--json` | Single JSON blob on stdout. |
| `--stream-json` | GREEN | `--stream-json` | NDJSON event stream. |
| `--mode <m>` | GREEN | `--mode interactive\|print\|rpc` | `sdk` is import-only. |
| `--model` / `-m` | GREEN | `--model` / `-m` | Same syntax. |
| `--models` (cycle list) | GREEN | `--models a,b,c` | REPL `/model` cycles. |
| `--list-models` | GREEN | `--list-models` | Provider-driven. |
| `--cwd` / `--dir` | GREEN | `--cwd` | Same. |
| `--max-steps` | GREEN | `--max-steps` | Same. |
| `--allowed-tools` | GREEN | `--allowed-tools Read,Bash,...` | Comma-separated allowlist. |
| `--no-save` | GREEN | `--no-save` | Skip eventlog. |
| `--resume <id>` | GREEN | `--resume <id>` | Rehydrate from eventlog. |
| `--extensions-dir` | GREEN | `--extensions-dir` | Override discovery root. |
| `--no-extensions` | GREEN | `--no-extensions` | Skip auto-discovery. |
| `--allow-extensions <names>` | GREEN | `--allow-extensions a,b` | Pre-approve in non-interactive. |
| `--thinking` | YELLOW | `--thinking off\|min\|low\|med\|high\|max` | Surface from `chimera.providers.thinking`. |
| `--verbose` | GREEN | `--verbose` | Stream events to stderr. |
| `--no-color` | GREEN | `--no-color` | Plain output handler. |
| `--api-key` | YELLOW | env var preferred | Inline flag deferred for security; env vars are the path. |
| `--base-url` | GREEN | `--base-url` | OpenAI-compatible endpoints. |
| `--login` | RED | n/a | Interactive OAuth flow deferred; `chimera auth login` covers it. |

## Slash commands (REPL)

The upstream's slash palette is intentionally small. Weasel matches
the small set and skips chrome that does not apply (no themes, no
terminal-title rewrite).

| Upstream slash | Weasel status | Notes |
|---|---|---|
| `/help` | GREEN | Lists registered commands and shortcuts. |
| `/exit` (`/quit`, `/q`) | GREEN | Graceful shutdown. |
| `/model` | GREEN | Cycle through `--models <list>`. |
| `/cost` | GREEN | Per-session cost rollup. |
| `/clear` | GREEN | Reset context, keep provider. |
| `/sessions` | GREEN | List + resume. |
| `/extensions` | GREEN | List loaded extensions, allow / block. |
| `/compact` | GREEN | Manual compaction. |
| `/login` | RED | OAuth deferred. |
| `/theme` | RED | Out of scope. |
| `/agent` (subagents) | RED | Intentionally not shipped — install an extension. |
| `/plan` (plan mode) | RED | Intentionally not shipped — use a prompt template. |

## Extensions

The upstream's extension contract — auto-discover `.{js,ts}` files
under an `extensions/` directory, register tools / hooks / slash
commands — is mirrored, with Python added as a first-class language.

| Upstream capability | Weasel status | Notes |
|---|---|---|
| Auto-discovery | GREEN | `.weasel/extensions/` + `~/.weasel/extensions/`. |
| TS / JS extensions | GREEN | Subprocess via Node, JSON-RPC over stdio. |
| Python extensions | GREEN | Native via importlib. (Superset.) |
| Manifest schema | GREEN | `manifest.json` for directory extensions. |
| Tool registration | GREEN | `@tool` decorator (Python) / `registerTool` (TS). |
| Hook registration | GREEN | `@hook("pre_tool_use")` etc. |
| Slash registration | GREEN | `@slash("/foo")`. |
| Prompt templates | GREEN | `prompts/*.md` rendered with Jinja2. |
| Per-extension permissions | GREEN | Manifest `permissions` block tightens only. |
| Allowlist on first run | GREEN | `.weasel/settings.json` records allowed extensions. |
| Marketplace / `pi pkg add` | RED | Use `git clone` / `pip install` for now. |

## SDK

The upstream's SDK exposes an `Agent` class consumed by integrators.
Weasel ships the same shape with both sync and async forms, plus a
streaming generator API.

| Upstream capability | Weasel status | Notes |
|---|---|---|
| `Agent` class | GREEN | `chimera.weasel.sdk.Agent`. |
| Sync `run()` | GREEN | Returns `RunResult`. |
| Async `arun()` | GREEN | Awaitable. |
| Streaming events | GREEN | `agent.stream()` async / `agent.iter_stream()` sync. |
| Mid-turn steering | GREEN | `agent.steer(text)`. |
| Cancel | GREEN | `agent.cancel()`. |
| Compaction | GREEN | `agent.compact(strategy="summary")`. |
| Resume | GREEN | `agent.resume(run_id)`. |
| Custom tools at runtime | GREEN | `agent.register_tool(fn)`. |
| Custom hooks at runtime | GREEN | `agent.register_hook(event, fn)`. |
| Custom event sink | GREEN | `Agent(on_event=fn)`. |
| Permissions injection | GREEN | `Agent(permissions=...)`. |

## RPC methods

Upstream's RPC mode exposes a JSON-RPC interface for process
integration. Weasel mirrors the methods one-for-one and returns
`chimera.events`-shaped event notifications.

| Method | Weasel status |
|---|---|
| `prompt` | GREEN |
| `steer` | GREEN |
| `cancel` | GREEN |
| `get_state` | GREEN |
| `compact` | GREEN |
| `list_sessions` | GREEN |
| `resume` | GREEN |
| `event` notifications | GREEN |

Error-code mapping (parse / invalid / cancelled / provider /
permission) lives in [`modes.md`](modes.md).

## Providers

Weasel reuses Chimera's full provider stack, so this is a superset
of the upstream chain (which is hosted-first). The auto-fall-through
to a local Ollama daemon is a weasel-specific addition for
zero-config laptops.

| Upstream provider | Weasel status | Notes |
|---|---|---|
| Anthropic | GREEN | Default for hosted; extended thinking, prompt cache. |
| OpenAI | GREEN | Streaming, reasoning-token tracking, JSON mode. |
| OpenRouter | GREEN | `vendor/name` routing rule. |
| Ollama | GREEN | Local + `:cloud` tags, `keep_alive=60m`. |
| llama.cpp | GREEN | Via OpenAI-compatible adapter + `--base-url`. |
| Modal-hosted | YELLOW | Programmatic only (no auto-detection). |
| Custom | GREEN | `register_provider("name", factory)`. |
| Subscription auth (`/login`) | YELLOW | Device-flow OAuth via `chimera.auth`; CLI `/login` deferred. |

## Settings file

The upstream's settings file lives under `.pi/settings.json`. Weasel
uses `.weasel/settings.json`. Keys map one-to-one where possible.

| Upstream key | Weasel status | Notes |
|---|---|---|
| `model` | GREEN | Default model when no flag / env. |
| `extensions.allowed` | GREEN | Allowlist for unattended runs. |
| `extensions.blocked` | GREEN | Blocklist that shadows allowed. |
| `permissions` | GREEN | Mapped to `chimera.permissions` rules. |
| `theme` | RED | No theme system. |
| `keybindings` | RED | No custom keybindings. |
| `compaction.threshold` | YELLOW | Honored when present; default lives in `chimera.compaction`. |

## Counts

- **Surfaces:** 10 GREEN, 0 YELLOW, 0 RED of 10.
- **CLI flags:** 18 GREEN, 2 YELLOW, 1 RED of 21.
- **Slash commands:** 8 GREEN, 0 YELLOW, 4 RED of 12 (the four REDs
  are intentional design choices).
- **Extension surface:** 10 GREEN, 0 YELLOW, 1 RED of 11.
- **SDK surface:** 12 GREEN, 0 YELLOW, 0 RED of 12.
- **RPC methods:** 8 GREEN of 8.
- **Providers:** 6 GREEN, 2 YELLOW, 0 RED of 8.
- **Settings keys:** 4 GREEN, 1 YELLOW, 2 RED of 7.

## Chimera-only capabilities (do not regress)

Weasel inherits Chimera primitives the upstream does not have:

- Cooperative `CancellationToken` (true mid-turn cancel).
- `MessageQueues` for safe mid-turn steering.
- Loop detection (exact + pattern cycle).
- `EventSourcedSession` crash recovery + gap detection.
- `FileAwareCompaction` (file tracking across compaction).
- `SessionTree` in-place branching.
- `RedactionMiddleware` for ten secret patterns.
- `CostTracker` with cache + reasoning-token breakdown.
- 26-event `EventBus` with middleware.
- `AgentConfig.from_markdown()` for project / user / built-in
  registries (used by extensions that ship prompt-driven sub-roles).

## Follow-up issues to file

1. `chimera weasel /login` — interactive OAuth flow.
2. `chimera weasel --thinking <level>` — first-class flag instead
   of env-var pass-through.
3. Marketplace command (`chimera weasel ext install <git-url>`).
4. Theme system parity (low priority — chrome only).
5. Per-extension dependency resolution beyond `depends_on`
   (semver ranges, conflict detection).
6. RPC streaming back-pressure (drop / buffer policy when client
   reads slowly).

## How to use this matrix

When a user runs `chimera weasel` from a project that already
contains a `.pi/` settings or extensions directory, weasel **does
not** automatically inherit it — the on-disk path is referenced as
a migration target only. Mention upstream paths as a fact when
helping users move; do not introduce an automatic ingest.

GREEN rows are expected to behave in lockstep with the upstream
minimal harness at the black-box level. YELLOW rows degrade
gracefully and emit a hint where the gap is user-visible. RED rows
are not implemented, by design or by deferral; the table makes the
reason explicit.

## See also

- [`quickstart.md`](quickstart.md) — short tour of the four modes.
- [`modes.md`](modes.md) — long form on each mode.
- [`extensions.md`](extensions.md) — extension contract and examples.
- [`sdk.md`](sdk.md) — embedded `Agent` class.
- [`providers.md`](providers.md) — provider chain.
- [`security-and-trademarks.md`](security-and-trademarks.md) —
  trademark hygiene + security posture.
