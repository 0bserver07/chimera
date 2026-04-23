# Archived Examples

This directory holds older iterations of benchmark scripts that were kept for
historical reference. They are **not** the recommended entry points and are
not guaranteed to run against the current codebase.

The canonical entry points live one directory up in `examples/`:

- **SWE-bench Lite (canonical)** — `examples/swe_bench_lite_run.py`
  Matches the published 10% resolve rate in
  `data/swebench-lite-glm51-results.jsonl`.
- **SWE-bench (official eval methodology)** — `examples/swe_bench_proper.py`
  Source-only patch + FAIL_TO_PASS / PASS_TO_PASS verification.
- **SWE-bench (Docker isolation)** — `examples/swe_bench_docker.py`
  Per-instance Docker containers for clean environments.
- **HumanEval (full 164)** — `examples/humaneval_full.py`
  Downloads the official dataset and runs the full suite.

## What's here and why it was archived

| File | Reason archived |
|------|-----------------|
| `swe_bench_chimera.py` | Early composition sketch; superseded by `swe_bench_lite_run.py`. |
| `swe_bench_lite_v2.py` | v2 iteration on the Lite runner. |
| `swe_bench_lite_v3.py` | v3 iteration on the Lite runner. |
| `swe_bench_openhands_style.py` | OpenHands-style prompt experiment. |
| `swe_bench_run.py` | Generic SWE-bench-style runner, pre-Lite-focus. |
| `swe_bench_toolcall.py` | Structured-tool-calling experiment. |
| `swe_bench_v4.py` | Anti-hesitation scaffold experiment. |
| `swe_bench_coding_agent.py` | CodingAgent assembly variant, superseded. |
| `humaneval_run.py` | Smaller HumanEval-style runner; use `humaneval_full.py`. |

If you find something useful in an archived file, prefer porting the idea into
the canonical scripts rather than reviving the archive.
