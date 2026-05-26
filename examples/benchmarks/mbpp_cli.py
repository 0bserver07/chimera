#!/usr/bin/env python3
"""MBPP sanitized runner via the `claude` CLI.

Same rationale as humaneval_cli.py: routes inference through the `claude` CLI
so Sonnet/Opus calls are billed against Max + Additional Usage (raw API
calls return 429 on this account).

Reuses load_problems + run_mbpp_test from mbpp_full.py, extract_code
from humaneval_full.py, and call_claude_cli from humaneval_cli.py.

Usage:
    python examples/benchmarks/mbpp_cli.py --model claude-sonnet-4-6 --count 427
    python examples/benchmarks/mbpp_cli.py --model claude-opus-4-7 --count 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from humaneval_cli import call_claude_cli  # type: ignore
from humaneval_full import REPO_ROOT, extract_code  # type: ignore
from mbpp_full import DEFAULT_DATASET, load_problems, run_mbpp_test  # type: ignore


def main() -> int:
    p = argparse.ArgumentParser(description="MBPP sanitized via claude CLI (Max + Additional Usage)")
    p.add_argument("--model", required=True, help="Claude model id, e.g. claude-sonnet-4-6")
    p.add_argument("--count", type=int, default=427)
    p.add_argument("--dataset", type=str, default=DEFAULT_DATASET)
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--call-timeout", type=int, default=180)
    args = p.parse_args()

    if not os.path.exists(args.dataset):
        print(f"error: MBPP dataset not found at {args.dataset}", file=sys.stderr)
        return 2

    problems = load_problems(args.dataset, args.count)
    print(f"Model:    {args.model}")
    print(f"Problems: {len(problems)}")
    print("Backend:  claude CLI (Max + Additional Usage)")
    print()

    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start = time.time()

    for i, problem in enumerate(problems, 1):
        task_id = problem["task_id"]
        prompt_text = problem["prompt"]
        test_imports = problem.get("test_imports", []) or []
        test_list = problem["test_list"]
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
            text, cost = call_claude_cli(args.model, msg, timeout=args.call_timeout)
            total_cost += cost
            code = extract_code(text, "")
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

        elapsed = time.time() - start
        rate = i / elapsed if elapsed > 0 else 0
        suffix = f"  ({error_msg[:60]})" if error_msg else ""
        print(f"[{i}/{len(problems)}] mbpp/{task_id}  {status}  ({rate:.2f} p/s, ${total_cost:.2f}){suffix}")

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
            print(f"  mbpp/{r['task_id']}  {r['error'][:80]}")
        if len(failures) > 25:
            print(f"  ... +{len(failures) - 25} more")

    out_path = args.output or os.path.join(
        REPO_ROOT, "data", f"mbpp-{args.model}-results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "model": args.model,
            "backend": "claude-cli",
            "benchmark": "mbpp-sanitized",
            "passed": passed,
            "total": len(problems),
            "pass_rate": pass_rate,
            "total_cost_usd": round(total_cost, 4),
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
