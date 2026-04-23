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

Both scripts default to `kimi-k2.6:cloud` at `http://localhost:11434` with auth
token `ollama`. Cloud models worth trying: `kimi-k2.6:cloud`, `glm-5.1:cloud`,
`qwen3.5:cloud`, `minimax-m2.7:cloud`.

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
  [`cegis_synthesis.py`](cegis_synthesis.py),
  [`sketch_synthesis.py`](sketch_synthesis.py),
  [`spec_inference_demo.py`](spec_inference_demo.py) — Synthesize code from
  specs, examples, sketches, or a CEGIS loop.
- [`oracle_demo.py`](oracle_demo.py),
  [`mutation_testing_demo.py`](mutation_testing_demo.py),
  [`validation_split.py`](validation_split.py) — Oracle / mutation /
  validation flows for synthesized code.

## Benchmarks

- [`humaneval_full.py`](humaneval_full.py) — Run the full HumanEval suite.
- [`swe_bench_lite_run.py`](swe_bench_lite_run.py),
  [`swe_bench_proper.py`](swe_bench_proper.py),
  [`swe_bench_docker.py`](swe_bench_docker.py) — SWE-bench Lite runs with
  various isolation strategies.

## Misc

- [`think_and_ask.py`](think_and_ask.py) — `think` + `ask_user` tool usage.
- [`flow_skills.py`](flow_skills.py) — Mermaid flowchart -> agent prompt.
- [`wire_monitoring.py`](wire_monitoring.py) — Monitor a running agent over
  Chimera's Wire channel.
- [`dmail_context_rewind.py`](dmail_context_rewind.py) — DMail context
  rollback for long runs.
- [`impact_analysis_demo.py`](impact_analysis_demo.py),
  [`fault_localization_demo.py`](fault_localization_demo.py),
  [`incremental_demo.py`](incremental_demo.py),
  [`tuner_demo.py`](tuner_demo.py),
  [`synthesis_with_diagnostics.py`](synthesis_with_diagnostics.py) — Tooling
  around synthesis and debugging.
- [`workflow_verification.py`](workflow_verification.py) — End-to-end
  verification of a workflow.
- [`run_all.py`](run_all.py) — Batch runner for smoke-testing every example.
