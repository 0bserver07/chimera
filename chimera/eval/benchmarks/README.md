# Benchmarks

Built-in benchmark adapters for the Chimera evaluation harness
(`chimera/eval/harness.py`).

| Benchmark   | Adapter file     | Class       | Notes                                           |
|-------------|------------------|-------------|-------------------------------------------------|
| SWE-bench   | `swe_bench.py`   | `SWEBench`  | Real GitHub issues with test verification       |
| HumanEval   | `human_eval.py`  | `HumanEval` | 164 hand-written Python problems                |
| AIMO        | `aimo.py`        | `AIMO`      | AI Mathematical Olympiad                        |
| Custom      | `custom.py`      | `Custom`    | User-defined task lists                         |

## SWE-bench

`SWEBench` loads instances from a JSON / JSONL file (or accepts them
programmatically via `add_instance()`). Each task carries an
`instance_id`, `repo`, `base_commit`, `problem_statement`, and optional
`test_patch`. `evaluate()` applies the test patch in the supplied
environment and runs the repo test suite.

### Current Baseline

| Variant              | Sample        | Resolve rate | Source                  |
|----------------------|---------------|--------------|-------------------------|
| SWE-bench Lite       | 20 instances  | **10%** (2/20) | Project memory, internal run |
| SWE-bench Verified   | not yet run   | n/a          | See issue #84           |

Reference leaders (as of Mar 2026, per issue #84): Claude Opus 4.5
80.9%, Gemini 3.1 Pro 80.6%, GLM-5 w/OpenHands 77.8%.

The 10% baseline reflects the current default scaffold
(`swebench` preset in `chimera/assembly/presets.py`):
`max_turns=30`, bash-only action space, window-truncation
compaction, single action per LLM call. See issue #84 for the
gap analysis and the planned improvement track for closing it.

### Smoke Test

```bash
uv run pytest tests/eval/test_swe_bench.py -q
```

(11 unit tests, no network or Docker required.)

### Full Run

See `examples/benchmarks/swe_bench_proper.py` and
`examples/benchmarks/swe_bench_docker.py`.
