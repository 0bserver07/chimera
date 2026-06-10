#!/usr/bin/env python3
"""Run a 50-problem easy-tier sample of LiveCodeBench.

LiveCodeBench is a contamination-resistant contest-problem benchmark
(`livecodebench/code_generation_lite` on HuggingFace). Each problem is a
codeforces/atcoder/leetcode statement; rows include `public_test_cases`
and `private_test_cases` as stdin/stdout pairs.

This runner is intentionally narrow:

- It filters to the 80 easy-tier problems whose tests are pure
  stdin/stdout (codeforces + atcoder; leetcode entries use functional
  tests with method signatures and are skipped here).
- It samples the first 50 by dataset order to give a stable, reportable
  cross-model comparison without sweeping the full 400-problem dataset.

Two inference backends:

1. Direct provider (chimera anthropic / OpenAI / etc.) for cheap Haiku
   passes. chimera's anthropic provider auto-detects `sk-ant-oat01-*`
   tokens and switches to Bearer auth, so Max OAuth credentials work.
2. `claude -p --output-format=json` CLI for Sonnet/Opus passes, which
   tap the Max subscription quota + Additional Usage. Direct API calls
   for Sonnet/Opus on a Max account get 429s; the CLI is the supported
   billing channel.

Grading: a problem counts as a pass iff the candidate program completes
all public + private test cases. For each test, we run the candidate via
`python -c <code>` with the test's `input` on stdin and compare its
stdout to the expected `output` after whitespace normalization (trim
trailing whitespace per line, drop trailing blank lines). Each test gets
a 10s wall-clock timeout to bound runaway loops.

Usage:
    # Smoke (5 problems via Haiku, direct provider):
    export ANTHROPIC_API_KEY=<your sk-ant-oat01-... OAuth token>
    python examples/benchmarks/livecodebench_sample.py \\
        --model claude-haiku-4-5-20251001 --count 5

    # Full 50 via Haiku (direct provider, ~$2):
    python examples/benchmarks/livecodebench_sample.py \\
        --model claude-haiku-4-5-20251001 --count 50

    # Full 50 via Sonnet (CLI route, ~$5):
    python examples/benchmarks/livecodebench_sample.py \\
        --via-cli --model claude-sonnet-4-6 --count 50
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_CACHE = "/tmp/livecodebench.jsonl"
HF_REPO = "livecodebench/code_generation_lite"
HF_FILENAME = "test.jsonl"


def download_dataset() -> str:
    """Materialise /tmp/livecodebench.jsonl with 50 easy stdin problems.

    The upstream HuggingFace file mixes leetcode (functional tests) with
    codeforces/atcoder (stdin tests). We filter to stdin-only easy
    problems and pre-decode the `private_test_cases` field (which is
    base64+zlib+pickled JSON on the HF wire format) so the runner stays
    a flat JSONL consumer.
    """
    if os.path.exists(DATASET_CACHE):
        return DATASET_CACHE

    from huggingface_hub import hf_hub_download

    print(f"Downloading {HF_REPO}/{HF_FILENAME} from HuggingFace...")
    src = hf_hub_download(repo_id=HF_REPO, filename=HF_FILENAME, repo_type="dataset")

    rows = []
    with open(src) as f:
        for line in f:
            rows.append(json.loads(line))

    def is_easy_stdin(r: dict) -> bool:
        if r["difficulty"] != "easy":
            return False
        if r["starter_code"]:
            return False
        pub = json.loads(r["public_test_cases"])
        return bool(pub) and all(t.get("testtype") == "stdin" for t in pub)

    def decode_private(blob: str) -> list[dict]:
        if not blob:
            return []
        decoded = pickle.loads(zlib.decompress(base64.b64decode(blob.encode("utf-8"))))
        if isinstance(decoded, str):
            return json.loads(decoded)
        return decoded

    selected = [r for r in rows if is_easy_stdin(r)][:50]
    with open(DATASET_CACHE, "w") as f:
        for r in selected:
            out = {
                "question_id": r["question_id"],
                "question_title": r["question_title"],
                "platform": r["platform"],
                "contest_id": r["contest_id"],
                "difficulty": r["difficulty"],
                "question_content": r["question_content"],
                "public_test_cases": json.loads(r["public_test_cases"]),
                "private_test_cases": decode_private(r["private_test_cases"]),
            }
            f.write(json.dumps(out) + "\n")
    print(f"Wrote {len(selected)} rows to {DATASET_CACHE}")
    return DATASET_CACHE


def load_problems(path: str, count: int | None = None) -> list[dict]:
    """Load LiveCodeBench problems from the cached JSONL file."""
    problems: list[dict] = []
    with open(path) as f:
        for line in f:
            problems.append(json.loads(line))
    if count:
        problems = problems[:count]
    return problems


_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)\n```", re.DOTALL)
_TOP_LEVEL_RE = re.compile(r"^(?:def |async def |from |import |class |if __name__|#!|#)", re.MULTILINE)


def extract_program(response_text: str) -> str:
    """Extract a standalone Python program from the LLM response.

    Unlike HumanEval, LiveCodeBench problems expect a full script that
    reads from stdin and writes to stdout. We prefer the last fenced
    Python block (in case the model wrote prose, then a draft, then a
    final program). If no fence is present, take the substring starting
    at the first import / def / class / shebang.
    """
    text = response_text.strip()

    fences = _FENCE_RE.findall(text)
    if fences:
        return fences[-1].strip("\n") + "\n"

    m = _TOP_LEVEL_RE.search(text)
    if m:
        return text[m.start():].strip() + "\n"
    return text + "\n"


def normalize_output(s: str) -> str:
    """Whitespace-normalize a candidate or expected output string.

    Strip trailing whitespace from each line, drop trailing blank lines.
    Casing and inter-token whitespace are preserved — Codeforces problems
    are case-sensitive ("YES" vs "Yes" can both be correct depending on
    the problem, so we leave that to the test's expected string).
    """
    lines = [ln.rstrip() for ln in s.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def run_one_test(program: str, stdin_data: str, timeout: float = 10.0) -> tuple[bool, str, str]:
    """Run `python -c <program>` with stdin and return (ok, stdout, err).

    The program runs in a tmpdir cwd to avoid stray file writes in the
    repo if the model emits something like `open('out.txt', 'w')`. ok
    is True iff exit code is 0 and stderr is empty enough to ignore.
    """
    with tempfile.TemporaryDirectory(prefix="lcb_") as td:
        try:
            result = subprocess.run(
                [sys.executable, "-c", program],
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=td,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"TIMEOUT (>{timeout}s)"
        except Exception as e:
            return False, "", ((str(e) or type(e).__name__).splitlines()[0])[:200]

        if result.returncode != 0:
            err = (result.stderr or "").strip().splitlines()
            return False, result.stdout, (err[-1] if err else f"exit {result.returncode}")[:200]
        return True, result.stdout, ""


def grade_program(program: str, tests: list[dict], timeout: float = 10.0) -> tuple[bool, str]:
    """Run all tests; return (all_passed, first_failure_reason)."""
    if not tests:
        return False, "no tests"
    for i, t in enumerate(tests):
        stdin = t.get("input", "")
        expected = normalize_output(t.get("output", ""))
        ok, stdout, err = run_one_test(program, stdin, timeout=timeout)
        if not ok:
            return False, f"test {i}: {err}"
        got = normalize_output(stdout)
        if got != expected:
            exp_preview = expected.replace("\n", "\\n")[:60]
            got_preview = got.replace("\n", "\\n")[:60]
            return False, f"test {i}: wrong output (expected '{exp_preview}', got '{got_preview}')"
    return True, ""


def build_prompt(problem: dict) -> str:
    """Build the user message for the LLM."""
    return (
        "You are solving a competitive programming problem. Write a complete Python program "
        "that reads from standard input and writes the answer to standard output. "
        "Return only the program inside a single ```python ... ``` code block, with no other text.\n\n"
        f"Problem ({problem['platform']}, {problem.get('contest_id', '')} {problem['question_id']}):\n\n"
        f"{problem['question_content']}\n"
    )


def call_with_retry(provider, msg: str, max_tokens: int = 2048,
                    max_retries: int = 4, base_delay: float = 2.0):
    """Direct-provider call with backoff on transient errors."""
    from chimera.types import Message
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            return provider.complete([Message.user(msg)], max_tokens=max_tokens)
        except Exception as e:
            last_err = e
            es = str(e)
            if "401" in es or "403" in es or "invalid api key" in es.lower():
                raise
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
    assert last_err is not None
    raise last_err


def call_claude_cli(model: str, prompt_msg: str, timeout: int = 240) -> tuple[str, float]:
    """Invoke `claude -p --output-format=json` once with prompt on stdin.

    Strips ANTHROPIC_API_KEY from the child env so the CLI uses
    CLAUDE_CODE_OAUTH_TOKEN with Bearer auth (otherwise the OAuth token
    gets sent as x-api-key and the server rejects it).
    """
    child_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [
            "claude",
            "--model", model,
            "-p",
            "--output-format", "json",
            "--no-session-persistence",
        ],
        input=prompt_msg,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr[:200]}")

    out = result.stdout.strip()
    try:
        events = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI returned non-JSON ({len(out)} bytes): {out[:200]}")

    # CLI may emit a single object or an array of events; handle both.
    if isinstance(events, dict):
        if events.get("type") == "result":
            return events.get("result", ""), float(events.get("total_cost_usd", 0.0))
        events = [events]

    result_event = next((e for e in events if isinstance(e, dict) and e.get("type") == "result"), None)
    if not result_event:
        raise RuntimeError("no `result` event in CLI output")

    text = result_event.get("result", "")
    cost = float(result_event.get("total_cost_usd", 0.0))
    return text, cost


def main() -> int:
    p = argparse.ArgumentParser(description="LiveCodeBench (50-easy) runner")
    p.add_argument("--model", required=True, help="Model id, e.g. claude-haiku-4-5-20251001 or claude-sonnet-4-6")
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--dataset", type=str, default=None)
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--via-cli", action="store_true", help="Use the claude CLI instead of direct provider")
    p.add_argument("--call-timeout", type=int, default=240, help="per-call inference timeout (CLI mode)")
    p.add_argument("--test-timeout", type=float, default=10.0, help="per-test execution timeout (seconds)")
    args = p.parse_args()

    dataset_path = args.dataset or download_dataset()
    problems = load_problems(dataset_path, args.count)

    backend = "claude-cli" if args.via_cli else "direct-provider"
    provider = None
    if not args.via_cli:
        from chimera.providers.factory import create_provider
        provider = create_provider(model=args.model)
        model_name = provider.model_name
    else:
        model_name = args.model

    print(f"Model:    {model_name}")
    print(f"Backend:  {backend}")
    print(f"Problems: {len(problems)}")
    print()

    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start = time.time()

    for i, problem in enumerate(problems, 1):
        qid = problem["question_id"]
        prompt = build_prompt(problem)
        tests = list(problem.get("public_test_cases", [])) + list(problem.get("private_test_cases", []))

        error_msg = ""
        cost = 0.0
        success = False
        try:
            if args.via_cli:
                text, cost = call_claude_cli(args.model, prompt, timeout=args.call_timeout)
            else:
                response = call_with_retry(provider, prompt, max_tokens=2048)
                text = response.content
                if response.usage:
                    from chimera.providers.cost import calculate_cost
                    cost = calculate_cost(provider.model_name, response.usage)
            total_cost += cost
            program = extract_program(text)
            success, error_msg = grade_program(program, tests, timeout=args.test_timeout)
        except Exception as e:
            error_msg = ((str(e) or type(e).__name__).splitlines()[0])[:200]

        status = "PASS" if success else "FAIL"
        if success:
            passed += 1

        results.append({
            "question_id": qid,
            "platform": problem.get("platform"),
            "status": status,
            "tests_total": len(tests),
            "cost": cost,
            "error": error_msg,
        })

        elapsed = time.time() - start
        rate = i / elapsed if elapsed > 0 else 0
        suffix = f"  ({error_msg[:60]})" if error_msg else ""
        print(f"[{i}/{len(problems)}] {qid:12s} {status}  ({rate:.2f} p/s, ${total_cost:.2f}){suffix}")

    elapsed = time.time() - start
    pass_rate = passed / len(problems) if problems else 0.0

    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    print(f"pass@1:      {pass_rate:.3f} ({passed}/{len(problems)})")
    print(f"Total cost:  ${total_cost:.4f}")
    if problems:
        print(f"Avg cost:    ${total_cost / len(problems):.4f}")
        print(f"Time:        {elapsed:.1f}s ({elapsed / len(problems):.1f}s/problem)")

    failures = [r for r in results if r["status"] == "FAIL"]
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for r in failures[:25]:
            print(f"  {r['question_id']:12s}  {r['error'][:80]}")
        if len(failures) > 25:
            print(f"  ... +{len(failures) - 25} more")

    out_path = args.output or os.path.join(
        REPO_ROOT, "data", f"livecodebench-{model_name}-results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "model": model_name,
            "backend": backend,
            "split": "easy-stdin-first-50",
            "passed": passed,
            "total": len(problems),
            "pass_rate": pass_rate,
            "total_cost_usd": round(total_cost, 4),
            "errors": [[r["question_id"], r["error"]] for r in results if r["status"] == "FAIL"],
        }, f, indent=2)
    detail_path = out_path.replace(".json", "-detail.jsonl")
    with open(detail_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nResults saved to: {out_path}")
    print(f"Per-problem detail: {detail_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
