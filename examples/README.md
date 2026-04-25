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

## Ollama setup notes

All `ollama_*` scripts default to `kimi-k2.6` against `https://ollama.com`
using your Ollama API token as `ANTHROPIC_AUTH_TOKEN`. For a local daemon,
pass `--base-url http://localhost:11434` and ensure the model is pulled
(`ollama pull kimi-k2.6:cloud`). See the top-level
[Ollama guide](https://0bserver07.github.io/chimera/guides/use-with-ollama/)
for prerequisites, recommended models, and troubleshooting.
