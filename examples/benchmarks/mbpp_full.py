#!/usr/bin/env python3
"""Run the MBPP sanitized benchmark (427 problems) through chimera.

Mirrors examples/benchmarks/humaneval_full.py but for MBPP. The dataset
is the hand-verified ``sanitized-mbpp.json`` from Google Research
(427 entries); each problem has a natural-language ``prompt``, a
``test_imports`` list, and a ``test_list`` of assert statements that
must all pass.

Reuses extract_code from humaneval_full (MBPP responses are typically
fenced or raw code -- the always-prepend behavior with an empty prompt
just returns the candidate). Uses its own subprocess-with-timeout
runner because MBPP tests are direct asserts, not check(entry_point).

Usage:
    # GLM-5.1 via z.ai
    source .env
    python examples/benchmarks/mbpp_full.py --count 427

    # Claude via Max OAuth (after the OAuth provider patch in 2ec8329)
    source /tmp/claude_oauth_env.sh
    python examples/benchmarks/mbpp_full.py --model claude-haiku-4-5-20251001 --count 427
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from humaneval_full import REPO_ROOT, extract_code, call_with_retry  # type: ignore

DEFAULT_DATASET = "/tmp/sanitized-mbpp.json"


def load_problems(path: str, count: int | None = None) -> list[dict]:
    """Load MBPP sanitized records."""
    with open(path) as f:
        data = json.load(f)
    if count:
        data = data[:count]
    return data


def run_mbpp_test(code: str, test_imports: list[str], test_list: list[str],
                  timeout: float = 10.0) -> tuple[bool, str]:
    """Run candidate + tests in a subprocess with a hard timeout.

    MBPP tests are direct asserts (no check(entry_point) wrapper), and
    ``test_imports`` carries setup like ``import math``.
    """
    test_block = "\n".join((test_imports or []) + test_list)
    full_code = f"{code}\n\n{test_block}\n"
    try:
        result = subprocess.run(
            [sys.executable, "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, ""
        err = (result.stderr or "").strip().splitlines()
        return False, (err[-1] if err else f"exit {result.returncode}")[:200]
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT (>{timeout}s)"
    except Exception as e:
        return False, ((str(e) or type(e).__name__).splitlines()[0])[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MBPP sanitized benchmark")
    parser.add_argument("--count", type=int, default=427, help="Problems (default: 427 = full sanitized)")
    parser.add_argument("--model", type=str, default=None, help="Model name (passed to chimera provider)")
    parser.add_argument("--dataset", type=str, default=DEFAULT_DATASET, help="Path to sanitized-mbpp.json")
    parser.add_argument("--output", type=str, default=None, help="Override results JSON path")
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        print(f"error: MBPP dataset not found at {args.dataset}", file=sys.stderr)
        print("  curl -sL -o /tmp/sanitized-mbpp.json https://raw.githubusercontent.com/google-research/google-research/master/mbpp/sanitized-mbpp.json", file=sys.stderr)
        return 2

    from chimera.providers.factory import create_provider

    provider = create_provider(model=args.model)
    problems = load_problems(args.dataset, args.count)

    print(f"Model:    {provider.model_name}")
    print(f"Problems: {len(problems)}")
    print()

    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start_time = time.time()

    for i, problem in enumerate(problems, 1):
        task_id = problem["task_id"]
        prompt_text = problem["prompt"]
        test_imports = problem.get("test_imports", []) or []
        test_list = problem["test_list"]

        # Build a focused message. Include one test as a usage example so
        # the model emits matching signatures + return shapes.
        first_test = test_list[0] if test_list else ""
        msg = (
            f"Write a Python function for the following problem. "
            f"Return ONLY the function implementation (plus any necessary imports), "
            f"no explanation, no markdown.\n\n"
            f"Problem: {prompt_text}\n\n"
            f"Example test that must pass:\n{first_test}\n"
        )

        error_msg = ""
        cost = 0.0
        success = False
        try:
            response = call_with_retry(provider, msg, max_tokens=1024)
            # MBPP responses are typically full code; passing "" as prompt
            # makes extract_code just return the candidate verbatim.
            code = extract_code(response.content, "")
            if response.usage:
                from chimera.providers.cost import calculate_cost
                cost = calculate_cost(provider.model_name, response.usage)
            total_cost += cost
            success, error_msg = run_mbpp_test(code, test_imports, test_list)
        except Exception as e:
            error_msg = ((str(e) or type(e).__name__).splitlines()[0])[:200]

        status = "PASS" if success else "FAIL"
        if success:
            passed += 1

        results.append({
            "task_id": task_id,
            "status": status,
            "cost": cost,
            "error": error_msg,
        })

        elapsed = time.time() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        suffix = f"  ({error_msg[:60]})" if error_msg else ""
        print(f"[{i}/{len(problems)}] mbpp/{task_id}  {status}  ({rate:.2f} prob/s){suffix}")

    elapsed = time.time() - start_time
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
        for r in failures[:30]:
            print(f"  mbpp/{r['task_id']}  {r['error'][:80]}")
        if len(failures) > 30:
            print(f"  ... +{len(failures) - 30} more")

    out_path = args.output or os.path.join(
        REPO_ROOT, "data", f"mbpp-{provider.model_name}-results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "model": provider.model_name,
            "benchmark": "mbpp-sanitized",
            "passed": passed,
            "total": len(problems),
            "pass_rate": pass_rate,
            "errors": [[r["task_id"], r["error"]] for r in results if r["status"] == "FAIL"],
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
