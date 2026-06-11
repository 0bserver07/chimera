---
title: "Chimera Benchmark Transparency Framework"
---

# Chimera Benchmark Transparency Framework

Full transparency on what Chimera can and cannot reproduce. Every benchmark has a status, our current score, the reference score, and what's needed to close the gap.

## Agent Benchmarks

| # | Benchmark | What It Tests | Our Score | Reference Score | Status | Issue |
|---|-----------|--------------|-----------|-----------------|--------|-------|
| A1 | [SWE-bench Lite](https://www.swebench.com/) | Fix real GitHub issues (top-20 smallest patches, cherry-picked) | GLM-5.1: 10% (2/20) | GLM-5 OpenHands ref: 77.8% | GAP | [Report](2026-03-30-swebench-lite-glm51.md) |
| A2 | [SWE-bench Lite full](https://www.swebench.com/) | Full 300-instance run | NOT RUN | — | TODO | #14 |
| A3 | [Terminal-Bench 2.0](https://www.tbench.ai/) | CLI tasks (10-task subset) | 30% (3/10) — **with TB's `terminus-1` agent, not Chimera** | GLM-5: 56.2% | UNVERIFIED | [Report](2026-03-30-terminal-bench-glm5.md) |
| A4 | [FeatureBench](https://arxiv.org/abs/2602.10975) | Build full features (200 tasks) | NOT RUN | Opus 4.5: 11% | TODO | #3 |
| A5 | [Cline Bench](https://github.com/cline/cline) | Repo-based development | NOT RUN | — | TODO | #4 |
| A6 | [DPAI Arena](https://labs.scale.com/) | Multi-language dev tasks | NOT RUN | — | TODO | #5 |
| A7 | [SWT-Bench](https://github.com/) | Software testing generation | NOT RUN | — | TODO | #6 |
| A8 | [tau-bench](https://github.com/) | Tool-use + business tasks | NOT RUN | — | TODO | #7 |
| A9 | [Context-Bench](https://github.com/) | Long-running context | NOT RUN | — | TODO | #8 |
| A10 | [SWE-PolyBench](https://github.com/) | Polyglot codebases | NOT RUN | — | TODO | #9 |
| A11 | [ProgramBench](https://github.com/SWE-agent/ProgramBench) | Rebuild a codebase from binary + docs | Harness validated end-to-end (2026-06-11, glm-5.1 via Ollama bridge); agent never converges to writing within 80 steps — submissions grade empty | — | HARNESS OK / AGENT TUNING | #141 |
| A12 | [DeepSWE](https://deepswe.datacurve.ai/) (Harbor format) | Long-horizon engineering tasks (117) | Adapter parses all 117 tasks; docker provisioning verified live; no agent runs yet | — | ADAPTER READY | — |

## Controlled Comparative Matrices

Same model, same tools, same budget — agent architecture as the only
variable (`chimera bench-compare`). First run:
[react vs plan-execute on GLM-5.1](2026-06-11-first-controlled-matrix.md)
(react 3/3 @ $0.0009/task; plan-execute 0/3 @ ~16× cost, 3/3 budget
exhaustions; six valid ATIF trajectories).

## LLM Benchmarks (Code)

| # | Benchmark | What It Tests | Our Score | Reference | Status | Issue |
|---|-----------|--------------|-----------|-----------|--------|-------|
| L1 | [HumanEval](https://github.com/openai/human-eval) | Function generation (164) | **4-model run (2026-05-20):** GLM-5.1 92.7% (152/164) · Haiku 4.5 95.7% (157/164) · Sonnet 4.6 98.8% (162/164) · **Opus 4.7 100.0%** (164/164). Raw in `data/humaneval-*-results.json`. Prior GLM-5.1 66.5% was a harness extraction bug, fixed 2026-05-20 (always-prepend prompt + subprocess timeout + retry). | GLM-5: ~92% claimed | DONE | [Report](2026-03-30-humaneval-glm51.md) |
| L2 | [HumanEval+](https://github.com/evalplus/evalplus) | Extended test cases (164) | **4-model run (2026-05-25):** GLM-5.1 89.6% (147/164) · Haiku 4.5 92.1% (151/164) · Sonnet 4.6 93.9% (154/164) · **Opus 4.7 95.1%** (156/164). Stricter EvalPlus tests on the same 164 problems; ~3-5pt drop from regular HumanEval. Raw in `data/humanevalplus-*-results.json`. | — | DONE | — |
| L3 | [MBPP](https://github.com/google-research/google-research/tree/master/mbpp) | Basic programming (sanitized 427) | **4-model run (2026-05-20):** GLM-5.1 87.4% (373/427) · Haiku 4.5 87.6% (374/427) · Sonnet 4.6 94.4% (403/427) · **Opus 4.7 97.7%** (417/427). Raw in `data/mbpp-*-results.json`. | — | DONE | — |
| L4 | [LiveCodeBench](https://livecodebench.github.io/) | Contest problems (50-problem easy stdin sample) | **Haiku 4.5: 98.0%** (49/50) · **Sonnet 4.6: 98.0%** (49/50). First 50 easy stdin problems from `livecodebench/code_generation_lite` (codeforces + atcoder; leetcode functional tests excluded). Raw in `data/livecodebench-*-results.json`. | — | PARTIAL | #12 |
| L5 | [AIMO/MATH-500](https://www.kaggle.com/competitions/ai-mathematical-olympiad-progress-prize-3) | Math reasoning (HuggingFaceH4/MATH-500) | **Haiku 4.5: 89.2%** (446/500) · **Sonnet 4.6: 91.5%** (183/200, first-200 sample). Answer-extraction via `\boxed{}`; normalized LaTeX equality (degree, percent, frac brace, thin-space comma, etc.) with numeric fallback. By-level (Haiku): L1 97.7% · L2 96.7% · L3 96.2% · L4 89.8% · L5 75.4%. Raw in `data/math500-*-results.json`. | — | PARTIAL | #13 |

## LLM Benchmarks (General)

| # | Benchmark | What It Tests | Our Score | Reference | Status | Issue |
|---|-----------|--------------|-----------|-----------|--------|-------|
| G1 | [MMLU-Pro](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro) | Knowledge + reasoning | N/A (eval only) | — | OUT OF SCOPE | — |
| G2 | [GPQA Diamond](https://arxiv.org/abs/2311.12022) | PhD-level science | N/A (eval only) | — | OUT OF SCOPE | — |
| G3 | [IFEval](https://arxiv.org/abs/2311.07911) | Instruction following | N/A (eval only) | — | OUT OF SCOPE | — |

## Status Legend

- **DONE**: Benchmark run, results reported
- **PARTIAL**: Benchmark run on subset, partial results
- **GAP**: Benchmark run, significant gap vs reference
- **TODO**: Adapter/runner not yet built
- **OUT OF SCOPE**: Tests raw LLM capability, not agent framework

## Root Cause Analysis: SWE-bench Gap

Our SWE-bench score (10%) vs GLM-5's claimed 77.8% with OpenHands:

| Factor | Our Implementation | OpenHands | Impact |
|--------|-------------------|-----------|--------|
| Max iterations | 100 | 500 | HIGH |
| Action space | bash only | bash + IPython + Jupyter | HIGH |
| Condensation | Window truncation (50%) | LLM-based summarization | MEDIUM |
| str_replace tool | Via sed/python heredoc | Native openhands_aci library | MEDIUM |
| Step execution | Single action per LLM call | Queue multiple actions per call | MEDIUM |
| Prompt | ~2KB general prompt | ~9KB domain-specific prompt | LOW |
| Instance selection | Easiest 20 by patch size | All 300/500 | LOW |

## Reproducing Results

```bash
# HumanEval (full 164)
source .env
python examples/benchmarks/humaneval_full.py --count 164

# SWE-bench (Docker, proper eval)
python examples/benchmarks/swe_bench_proper.py --count 10

# SWE-bench Lite (canonical, matches published 10% resolve rate)
python examples/benchmarks/swe_bench_lite_run.py --count 20 --max-steps 50

# Terminal-Bench (requires Docker)
tb run --agent terminus --model anthropic/glm-5 --dataset terminal-bench-core==0.1.1 --n-tasks 10
```

## Next Steps

1. Run OpenHands directly with GLM-5 on our 20 instances to verify their 77.8%
2. Build adapters for FeatureBench, MBPP, LiveCodeBench
3. Implement LLM-based condensation (matching OpenHands)
4. Add IPython action space
5. Increase max iterations to 500
6. Run on full SWE-bench Verified (500 instances)
