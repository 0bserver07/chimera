# Chimera Examples

Runnable scripts that exercise Chimera end-to-end. Each script is standalone
and self-documenting (read the docstring at the top). Grouped by intent below.

```
examples/
├── provider/            — smallest "does it connect?" demos
├── agent/               — basic agents, presets, and coding-agent references
├── real_world/          — practical tools: review, commit, explain, CI-fix
├── composition/         — pipelines and supervisor topologies
├── synthesis/           — test-driven convergence and CEGIS
├── function_synthesis/  — compile specs into portable .chi bundles
├── benchmarks/          — HumanEval and SWE-bench runners
└── _archive/            — older iterations kept for reference
```

## provider/

- [`quickstart_provider.py`](provider/quickstart_provider.py) — Connect to any
  Anthropic-compatible provider (Claude, GLM-5 via z.ai, OpenAI-compatible) and
  run three smoke tests.
- [`ollama_quickstart.py`](provider/ollama_quickstart.py) — Same idea, pointed
  at Ollama's Anthropic-compatible endpoint (local daemon or
  `https://ollama.com`). Runs plain text, tool-use, and multi-turn demos.
- [`streaming_agent.py`](provider/streaming_agent.py) — Stream agent output
  token by token.

## agent/

- [`agent_with_tools.py`](agent/agent_with_tools.py) — Build an agent by
  composing a custom tool set.
- [`coding_agent.py`](agent/coding_agent.py) — Full coding agent: 24 tools,
  interactive REPL, project-rules loading.
- [`coding_agent_minimal.py`](agent/coding_agent_minimal.py) — Smallest
  possible coding loop, no bells or whistles.
- [`ollama_coding_agent.py`](agent/ollama_coding_agent.py) — Full `CodingAgent`
  driven by an Ollama cloud model; pre-flight checks endpoint + context window.
- [`build_full_preset_agent.py`](agent/build_full_preset_agent.py) — Assemble
  a full-featured coding agent from Chimera primitives via the `claude_code`
  preset key on `CodingAgent.from_preset()`.
- [`build_codex_clone.py`](agent/build_codex_clone.py) — Codex-style preset.

## real_world/

Everyday tools you can point at your own codebase.

- [`ollama_code_review.py`](real_world/ollama_code_review.py) — Pipe `git diff`
  into a model and get a VERDICT / SUMMARY / ISSUES review. Verified against
  `kimi-k2.6`.
- [`ollama_commit_message.py`](real_world/ollama_commit_message.py) — Generate
  a Conventional Commits message from your staged diff. Stdout is pipeable into
  `git commit -F -`.
- [`ollama_explain.py`](real_world/ollama_explain.py) — Hand a file to the
  model and get a structured explanation (WHAT IT IS / PURPOSE / KEY PIECES /
  HOW IT FITS / GOTCHAS).
- [`ci_fix.py`](real_world/ci_fix.py) — `CIFixWorkflow`: parse CI logs, prompt
  the agent, retry until green.
- [`session_persistence.py`](real_world/session_persistence.py) — Save and
  resume agent sessions across restarts.

## composition/

- [`composition_pipeline.py`](composition/composition_pipeline.py) — Pipeline
  and Ensemble composition patterns.
- [`supervisor_delegation.py`](composition/supervisor_delegation.py) —
  Supervisor agent delegating to worker agents.

## synthesis/

- [`quickstart_synthesize.py`](synthesis/quickstart_synthesize.py) — Smallest
  end-to-end synthesis pipeline.
- [`cegis_synthesis.py`](synthesis/cegis_synthesis.py) — Counterexample-guided
  inductive synthesis loop.
- [`sketch_synthesis.py`](synthesis/sketch_synthesis.py) — Sketch-based
  synthesis (fill in holes in a partial program).
- [`validation_split.py`](synthesis/validation_split.py) — Train/val split to
  detect overfitting of synthesized programs.

## function_synthesis/

Compile natural-language specs into portable `.chi` bundles.

- [`function_synthesis_quickstart.py`](function_synthesis/function_synthesis_quickstart.py)
  — Smallest "compile a spec, call it" demo (mock compiler).
- [`function_synthesis_full_demo.py`](function_synthesis/function_synthesis_full_demo.py)
  — Full lifecycle: compile, save, load, invoke, uninstall.
- [`function_synthesis_real_e2e.py`](function_synthesis/function_synthesis_real_e2e.py)
  — Real PEFT fine-tuning on Qwen2-0.5B via `LocalCompiler` and
  `TransformersBackend`.
- [`function_synthesis_real_llamacpp.py`](function_synthesis/function_synthesis_real_llamacpp.py)
  — Real llama.cpp inference on a TinyLlama GGUF, streaming included.

## benchmarks/

- [`humaneval_full.py`](benchmarks/humaneval_full.py) — Full HumanEval suite.
- [`swe_bench_lite_run.py`](benchmarks/swe_bench_lite_run.py) — Canonical
  SWE-bench Lite runner (matches the 10% resolve rate in `data/`).
- [`swe_bench_proper.py`](benchmarks/swe_bench_proper.py) — Official SWE-bench
  eval flow (`FAIL_TO_PASS` / `PASS_TO_PASS`).
- [`swe_bench_docker.py`](benchmarks/swe_bench_docker.py) — SWE-bench with
  per-instance Docker isolation.

## Codename CLI quickstarts

One runnable quickstart per Chimera CLI codename. Each script calls
`chimera <cli>` via `subprocess.run` so the example mirrors what a shell
user would actually type. All scripts skip gracefully when the
underlying credentials or daemons are missing — they print a friendly
message and exit 0 instead of crashing.

- [`mink_quickstart.py`](mink_quickstart.py) — TUI-first CLI. One-shot
  `-p` plus `chimera mink runs list`. Skips when no provider credential
  is set.
  - Run: `python examples/mink_quickstart.py --model glm-5`
  - Output: streamed agent output, then a table of persisted runs.

- [`otter_quickstart.py`](otter_quickstart.py) — Multi-session HTTP CLI.
  One-shot `-p`, then spawns `chimera otter serve --port 5173
  --auth-token test-token`, sends `POST /session` and
  `POST /session/<id>/message`, and graceful-stops the server.
  Prefers the per-session token (B8) when present, falls back to the
  master token. Skips when no credential is set.
  - Run: `python examples/otter_quickstart.py`
  - Output: HTTP exchange dump + clean teardown.

- [`ferret_quickstart.py`](ferret_quickstart.py) — Sandbox-first CLI.
  Demonstrates `--sandbox` and `--approval`: a `read-only` listing then
  a `workspace-write` no-op echo. Skips when no credential is set.
  - Run: `python examples/ferret_quickstart.py`
  - Output: two short transcripts showing the sandbox escalation.

- [`weasel_quickstart.py`](weasel_quickstart.py) — RPC + SDK CLI. Always
  runs `--mode sdk` (no LLM call). Adds a `-p` one-shot when a
  credential is set. See [`weasel_sdk_quickstart.py`](weasel_sdk_quickstart.py)
  for in-process embedding and [`weasel_live_smoke.py`](weasel_live_smoke.py)
  for the full RPC test.
  - Run: `python examples/weasel_quickstart.py`

- [`shrew_quickstart.py`](shrew_quickstart.py) — Small-models CLI. Pins
  `--max-steps 30` and `--allowed-tools "Read,Write,Edit,Bash"`; skills
  are auto-discovered from `chimera/shrew/skills/` and
  `~/.shrew/skills/` at startup. Always runs `--list-models`; the `-p`
  demo is skipped when neither a local Ollama daemon (port 11434) nor
  a remote credential is available.
  - Run: `python examples/shrew_quickstart.py`

- [`stoat_quickstart.py`](stoat_quickstart.py) — Shell-first CLI. Drives
  the `-p` and `-p --json` surfaces. Documents why `--shell-mode` is
  not exercised here (interactive REPL is hard to script reliably).
  Skips when no credential is set.
  - Run: `python examples/stoat_quickstart.py`

- [`badger_quickstart.py`](badger_quickstart.py) — Strict / parity CLI.
  Always runs `chimera badger parity --against PARITY.json` against a
  temp schema (no LLM call). Adds a `-p --rerun-on-failure --max-reruns
  1` demo when a credential is set.
  - Run: `python examples/badger_quickstart.py`

For deeper reading on any CLI, follow the corresponding doc tree:
`docs/mink/`, `docs/otter/`, `docs/ferret/`, `docs/weasel/`,
`docs/shrew/`, `docs/stoat/`, `docs/badger/`.

## Ollama setup notes

All `ollama_*` scripts default to `kimi-k2.6` against `https://ollama.com`
using your Ollama API token as `ANTHROPIC_AUTH_TOKEN`. For a local daemon,
pass `--base-url http://localhost:11434` and ensure the model is pulled
(`ollama pull kimi-k2.6:cloud`). See the top-level
[Ollama guide](https://0bserver07.github.io/chimera/guides/use-with-ollama/)
for prerequisites, recommended models, and troubleshooting.
