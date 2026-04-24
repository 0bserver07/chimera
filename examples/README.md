# Chimera Examples

Runnable scripts that exercise Chimera end-to-end. Each script is standalone and
self-documenting (read the docstring at the top).

## Ollama (local / cloud)

Run Chimera against any Ollama model through its Anthropic-compatible endpoint.
First make sure Ollama is running and a model is pulled:

```sh
ollama serve
ollama pull kimi-k2.6:cloud
```

Then:

- [`ollama_quickstart.py`](ollama_quickstart.py) — Smallest possible "it works"
  demo: pre-flight check, plain text completion, single-tool tool-use, and a
  multi-turn conversation. Prints tokens and cost after each step. CLI flags:
  `--model`, `--base-url`, `--auth-token`, `--skip-tool-use`,
  `--skip-multi-turn`.

- [`ollama_coding_agent.py`](ollama_coding_agent.py) — Full `CodingAgent` loop
  driven by an Ollama cloud model. Pre-flight checks the endpoint and the
  model's context length (>=64k recommended), then streams every loop event as
  the agent scans the current directory and summarizes Python files. CLI flags:
  `--model`, `--task`, `--max-steps`, `--base-url`, `--auth-token`.

- [`ollama_code_review.py`](ollama_code_review.py) — Pipe `git diff` (or
  `--staged`, or a patch file) into a model and get a structured review with
  VERDICT / SUMMARY / ISSUES by severity. Verified end-to-end against
  `kimi-k2.6`.

- [`ollama_commit_message.py`](ollama_commit_message.py) — Generate a
  Conventional Commits message from your staged diff. Supports `--type`,
  `--scope`, `--breaking`, `--copy` (pbcopy / wl-copy / xclip). Stdout is
  pipeable into `git commit -F -`.

- [`ollama_explain.py`](ollama_explain.py) — Hand a file to the model and get
  a structured explanation (WHAT IT IS / PURPOSE / KEY PIECES / HOW IT FITS /
  GOTCHAS). Supports `--symbol` to isolate one class or function, `--focus` to
  steer the explanation, and `--depth overview|detailed|line-by-line`.

Both scripts default to `kimi-k2.6:cloud` at `http://localhost:11434` with auth
token `ollama`. Cloud models worth trying: `kimi-k2.6:cloud`, `glm-5.1:cloud`,
`qwen3.5:cloud`, `minimax-m2.7:cloud`. For cloud usage, set
`ANTHROPIC_BASE_URL=https://ollama.com` and use your Ollama API token as
`ANTHROPIC_AUTH_TOKEN`.

## Generic quickstarts

- [`quickstart_provider.py`](quickstart_provider.py) — Connect to any
  Anthropic-compatible provider (Claude, GLM-5 via z.ai, OpenAI-compatible) and
  run three smoke tests.
- [`quickstart_synthesize.py`](quickstart_synthesize.py) — Smallest synthesis
  pipeline end-to-end.

## Coding agents

- [`coding_agent.py`](coding_agent.py) — Full coding agent with all 14 tools,
  interactive REPL mode, and project-rules loading.
- [`coding_agent_minimal.py`](coding_agent_minimal.py) — The smallest possible
  coding loop, no bells or whistles.
- [`agent_with_tools.py`](agent_with_tools.py) — Build an agent by composing a
  custom tool set.
- [`streaming_agent.py`](streaming_agent.py) — Stream agent output token by
  token.
- [`session_persistence.py`](session_persistence.py) — Save and resume agent
  sessions.

## Workflows

- [`ci_fix.py`](ci_fix.py) — CIFixWorkflow: parse CI logs, prompt the agent,
  retry until green.
- [`build_claude_code_clone.py`](build_claude_code_clone.py),
  [`build_codex_clone.py`](build_codex_clone.py) — Assemble Claude-Code-like
  and Codex-like agents from Chimera primitives.
- [`supervisor_delegation.py`](supervisor_delegation.py) — Supervisor agent
  delegating to worker agents.
- [`composition_pipeline.py`](composition_pipeline.py) — Pipeline + Ensemble
  composition patterns.

## Synthesis

- [`function_synthesis_quickstart.py`](function_synthesis_quickstart.py),
  [`function_synthesis_full_demo.py`](function_synthesis_full_demo.py),
  [`function_synthesis_real_e2e.py`](function_synthesis_real_e2e.py),
  [`function_synthesis_real_llamacpp.py`](function_synthesis_real_llamacpp.py) —
  Compile specs into portable `.chi` bundles, backed by real runtimes.
- [`cegis_synthesis.py`](cegis_synthesis.py),
  [`sketch_synthesis.py`](sketch_synthesis.py),
  [`validation_split.py`](validation_split.py) — CEGIS loop, sketch-based
  synthesis, train/val split for overfit detection.

## Benchmarks

- [`humaneval_full.py`](humaneval_full.py) — Full HumanEval suite.
- [`swe_bench_lite_run.py`](swe_bench_lite_run.py) — Canonical SWE-bench Lite
  runner (matches the published 10% resolve rate).
- [`swe_bench_proper.py`](swe_bench_proper.py) — Official SWE-bench eval flow
  (FAIL_TO_PASS / PASS_TO_PASS).
- [`swe_bench_docker.py`](swe_bench_docker.py) — SWE-bench with per-instance
  Docker isolation.
