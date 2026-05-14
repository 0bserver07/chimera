---
title: Shrew Quickstart
description: Install shrew, run small-model code sessions, learn the output_parser/quality_monitor layer, the model_profiles config, the 21 bundled skills, and the knowledge-axis skill scoring.
---

# `chimera shrew` Quickstart

`chimera shrew` is the fifth Chimera coding-agent CLI and the first
one tuned **explicitly for small local models**. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent,
[`chimera otter`](../otter/quickstart.md) mirrors a server-first
multi-client agent, and [`chimera ferret`](../ferret/quickstart.md)
mirrors an IDE-first sandboxed agent, shrew mirrors a small-model
coding agent — a thin layer on top of
[`chimera weasel`](../weasel/quickstart.md) that pins three
small-model defaults, ships a curated 21-skill bundle, layers an
`output_parser` + `quality_monitor` quality net, exposes a per-model
`model_profiles` config, and adds a benchmark harness for Aider
Polyglot and GAIA.

Headline thesis: most "the model can't code" complaints are really
"the scaffold is too rich for this model". Shrew exists to make a
9B–35B parameter local model feel like a competent coding collaborator
by tightening the harness around it.

Deeper dives:

- [`small-model-setup.md`](small-model-setup.md) — llama.cpp build.
- [`skills.md`](skills.md) — bundled skill set + axis scoring.
- [`extensions.md`](extensions.md) — `moe_offload`, `scaffold_fit`,
  `tool_filter`.
- [`benchmarks.md`](benchmarks.md) — Aider Polyglot + GAIA setup.
- [`parity-matrix.md`](parity-matrix.md) — surface mapping.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of:
  - A running **llama.cpp** HTTP server on `127.0.0.1:8888`.
  - A running **Ollama** daemon on `localhost:11434`, or an Ollama
    Cloud account.
  - A cloud provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or
    `OPENROUTER_API_KEY`) — used as fallback.

```bash
uv --version                          # >= 0.4
uv sync --extra dev                   # core only
```

## Provider configuration

Shrew **inverts** the priority order weasel uses. A reachable local
server is the default; cloud is the fallback. The full chain:

1. `--model <id>` on the CLI.
2. `$SHREW_MODEL` environment variable.
3. **llama.cpp** at `$LLAMACPP_BASE_URL` — probed via `/health`.
4. **Ollama** at `$OLLAMA_BASE_URL` — probed via `/api/tags`.
5. `$ANTHROPIC_API_KEY` → `claude-sonnet-4-6`.
6. `$OPENAI_API_KEY` → `gpt-4o`.
7. `$OPENROUTER_API_KEY` → `openai/gpt-4o`.
8. `$OLLAMA_API_KEY` → Ollama Cloud (`:cloud` tags).
9. Friendly error pointing at every supported source.

The local-first ordering is deliberate: shrew exists to prove that
small local models are good enough for real coding work; reaching for
a cloud key should be a last resort, not the default.

## First one-shot turn

```bash
chimera shrew -p "list the top-level files and read the README"
```

Expected output shape (truncated):

```text
shrew: skills=21 mounted; scaffold=off; tools=4 (dropped 0); context_window=8192; model=qwen3.6-35b-a3b
I'll list the repo first, then read the README.

▶ list_files(path=".")
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(path="README.md")
# Chimera
A composable coding agent framework
...

[shrew] run saved as shrew-20260514T141802-2c8f9a3b at /Users/.../.chimera/eventlog/shrew-20260514T141802-2c8f9a3b/
```

The `shrew: skills=...` banner reports how many skills mounted, whether
the scaffold-fit extension trimmed the tool list, and the resolved
context window for this model.

## The three flags that matter most

```bash
chimera shrew --model qwen3.5-9b -p "..."           # dense 9B local
chimera shrew --vram-gb 24 -p "audit the repo"      # workstation GPU
chimera shrew --no-skills --model gpt-4o -p "..."   # frontier — skip skill bundle
```

Defaults pinned on top of weasel:

| Flag | Shrew default | Why |
|---|---|---|
| `--model` | `qwen3.6-35b-a3b` | Local MoE; runs on 32-64 GB Mac at 4-bit quant. |
| `--max-steps` | `30` | Smaller than mink/otter's 50 — small models loop on long horizons. |
| `--allowed-tools` | `Read,Write,Edit,Bash` | Minimal high-leverage toolkit. |
| `--vram-gb` | `8` | Used by `moe_offload` to pick a safe context window. |

## Drop into the REPL

```bash
chimera shrew
chimera shrew --model qwen3.5-9b
```

```text
shrew — Chimera coding agent (small-model tuned)
model: qwen3.6-35b-a3b  ·  skills: 21  ·  context: 32768
shrew> /skills
Discovered 21 skill(s):
  context_window_discipline   How to avoid spilling past the model's window.
  edit_before_write           Prefer Edit over Write when patching code.
  test_first_python           Write the test first, then the fix.
  …
shrew> /tools
Read, Write, Edit, Bash  (dropped: 16 via scaffold_fit)
shrew> refactor src/util.py to remove duplication
…
```

Type `/help` for the live palette.

## The bundled 21 skills

Shrew ships three skill axes under `chimera/shrew/skills/`. Each
`.md` is mounted as a system-prompt overlay when the agent triggers
the skill's heuristic.

| Axis | Count | Examples |
| --- | --- | --- |
| **knowledge** | 7 | `python_idioms`, `git_aware_context`, `loop_detection_signals`, `scaffold_model_fit`, `escalation_signals`, `tool_budget_vs_prose_budget`, `context_window_discipline` |
| **protocols** | 8 | `edit_before_write`, `test_first_python`, `read_tests_before_fixing`, `incremental_edits`, `dry_run_before_commit`, `bisect_on_failure`, `error_recovery`, `one_focused_question` |
| **tools** | 6 | `core_tools`, `find_vs_grep_vs_rg`, `multi_file_edits`, `bash_pipelines_with_care`, `python_subprocess_vs_bash`, `grep_vs_ls` |

To skip the bundle entirely for a frontier-model run:

```bash
chimera shrew --no-skills -p "..."
```

To add your own skill, drop a `SKILL.md` with YAML frontmatter under
`~/.shrew/skills/` — it's auto-discovered on every run.

## Knowledge-axis skill scoring

Skills aren't all mounted on every turn; the `skill_injector` scores
each one against the current prompt and recent tool history, mounting
only the top-K most relevant. Scoring axes:

| Axis | What it weighs |
| --- | --- |
| **lexical** | Keyword overlap between the prompt and the skill's `triggers` list. |
| **structural** | Whether the prompt names a file type / tool the skill targets. |
| **historical** | Whether a recent tool call invoked something the skill governs. |

Tune the budget:

```bash
shrew> /skills budget 5         # mount at most 5 skills per turn
shrew> /skills budget 0         # mount nothing (same as --no-skills)
shrew> /skills score "refactor src/util.py to use httpx instead of requests"
top-3 scored skills:
  edit_before_write       lexical=0.62 structural=0.71  →  0.66
  incremental_edits       lexical=0.55 structural=0.34  →  0.45
  python_idioms           lexical=0.21 structural=0.62  →  0.41
```

Full scoring rules in [`skills.md`](skills.md).

## `output_parser` + `quality_monitor`

Two quality-net components small models need:

`output_parser` (`chimera/shrew/output_parser.py`) extracts structured
tool calls from un-grammar models that emit them as fenced JSON in
prose. If the model invents a malformed tool call, the parser rejects
it and retries with a system-prompt nudge.

`quality_monitor` (`chimera/shrew/quality_monitor.py`) watches for:

- repeated identical tool calls (loop signal),
- escalating retry-rates (escalation signal),
- tool calls outside the `--allowed-tools` list (scope signal),
- empty assistant text after 3 consecutive tool calls (drift signal).

When a signal fires, the monitor inserts a steering message and
optionally widens the rerun budget. To see signals:

```bash
chimera shrew --output-format stream-json -p "fix this test" \
  | jq 'select(.event=="quality_monitor")'
```

Sample event:

```json
{"event":"quality_monitor","signal":"loop","tool":"Read","count":3,"action":"steered"}
```

## `model_profiles` config

`~/.chimera/shrew/settings.json` controls per-model knobs. Shape:

```json
{
  "default_model_profile": {
    "max_tokens": 4096,
    "context_limit": 32768,
    "temperature": 0.3,
    "thinking_budget": 2048,
    "skill_token_budget": 300
  },
  "model_profiles": {
    "qwen3.6-35b-a3b": {
      "max_tokens": 6144,
      "temperature": 0.2,
      "benchmark_overrides": {
        "terminal_bench": {"thinking_budget": 3000, "max_turns": 40},
        "gaia":           {"thinking_budget": 2000, "context_limit": 65536}
      }
    }
  }
}
```

Lookup order on each request: benchmark override > model profile >
default profile > hard-coded fallbacks.

## Sessions / persistence

```bash
chimera shrew sessions list
chimera shrew sessions show shrew-20260514T141802-2c8f9a3b
chimera shrew sessions cost --since 7d
chimera shrew --resume shrew-20260514T141802-2c8f9a3b   # explicit
chimera shrew -c                                        # newest in cwd
```

## Run a benchmark

```bash
chimera shrew bench aider-polyglot --bench-limit 5
chimera shrew bench gaia --bench-limit 5
```

When the dataset isn't staged, shrew prints a setup hint and exits
with code `3`. See [`benchmarks.md`](benchmarks.md).

## Choose your model

Recommended models for the small-model-tuned scaffold:

| Backend | Tag | Why for shrew |
|---|---|---|
| llama.cpp | `qwen3.6-35b-a3b` | Default; local MoE, 32-64 GB Mac. |
| Ollama Cloud | `gpt-oss:120b-cloud` | Free w/ Ollama account; strong baseline. |
| Ollama local | `qwen3:32b` | 131k context, runs on a 24 GB GPU. |
| Anthropic | `claude-sonnet-4-6` | Cloud fallback when local isn't reachable. |

See [the Ollama Cloud recipe](../use-with-ollama.md).

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `SHREW_MODEL` | (unset) | Default model id. |
| `SHREW_VRAM_GB` | `8` | VRAM budget for `moe_offload`. |
| `LLAMACPP_BASE_URL` | `http://127.0.0.1:8888/v1` | llama.cpp base. |
| `LLAMACPP_API_KEY` | (unset) | Optional auth. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama daemon base. |
| `OLLAMA_API_KEY` | (unset) | Ollama Cloud (`:cloud` tags). |
| `ANTHROPIC_API_KEY` | (unset) | Anthropic fallback. |
| `OPENAI_API_KEY` | (unset) | OpenAI fallback. |
| `OPENROUTER_API_KEY` | (unset) | OpenRouter fallback. |
| `NO_COLOR` | (unset) | Plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/shrew-<id>/` | Per-run event stream + summary. |
| `~/.chimera/shrew/settings.json` | Per-model profiles. |
| `~/.chimera/datasets/aider-polyglot/` | Aider Polyglot root. |
| `~/.shrew/skills/` | User-owned skill overlay. |

Everything is local-only. Purge with `rm -rf ~/.chimera/eventlog/shrew-*`.

## Where to go next

- [Small-model setup](./small-model-setup.md) — llama.cpp build.
- [Skills](./skills.md) — bundled set + axis scoring.
- [Extensions](./extensions.md) — `moe_offload` / `scaffold_fit` / `tool_filter`.
- [Benchmarks](./benchmarks.md).
- [Parity Matrix](./parity-matrix.md).
- [Security and Trademarks](./security-and-trademarks.md).

---

### Verified (2026-05-14)

Two commands from this quickstart, against Ollama Cloud:

```text
$ OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=*** \
    chimera shrew -p "Hello, please reply with one word: hello" \
                  --model gpt-oss:120b-cloud --max-steps 2
shrew: skills=21 mounted; scaffold=off; tools=4 (dropped 0); context_window=8192; model=gpt-oss:120b-cloud; size_b=120.0
hello

$ chimera shrew --version
chimera shrew 0.7.0
```
