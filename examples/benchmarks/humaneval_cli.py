#!/usr/bin/env python3
"""HumanEval runner that uses the `claude` CLI for inference.

Why this script exists: the Claude Code Max subscription's quota for
Sonnet/Opus is only billable through Claude Code itself, not direct
/v1/messages API calls. The `claude -p --no-session-persistence` CLI
taps the Max quota first and then spills into the Additional Usage
credit automatically; raw API calls with the same OAuth token return
bare 429s for Sonnet/Opus on this account.

This script wraps the CLI per-problem so HumanEval results land in the
same data/ layout as the API-based runner, and reuses extract_code +
run_test from humaneval_full.py for the post-processing and grading.

Usage:
    python examples/benchmarks/humaneval_cli.py --model claude-sonnet-4-6 --count 164
    python examples/benchmarks/humaneval_cli.py --model claude-opus-4-7 --count 20

Cost note: each invocation spawns a fresh CLI process which reloads the
Claude Code system context (~13K tokens). At Sonnet rates that adds
~$0.05/call regardless of completion size; full 164 costs ~$8-10.
The CLI's --output-format=json gives back the true `total_cost_usd`
per call, which we sum and report.
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

from humaneval_full import (  # type: ignore
    REPO_ROOT,
    download_dataset,
    extract_code,
    load_problems,
    run_test,
)


def call_claude_cli(model: str, prompt_msg: str, timeout: int = 180) -> tuple[str, float]:
    """Invoke `claude -p --output-format=json` once with prompt on stdin.

    Returns (response_text, total_cost_usd). Raises on non-zero exit or
    unparseable output.
    """
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
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr[:200]}")

    out = result.stdout.strip()
    try:
        events = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI returned non-JSON ({len(out)} bytes): {out[:200]}")

    # The CLI emits a JSON array; find the terminal "result" event.
    result_event = next((e for e in events if isinstance(e, dict) and e.get("type") == "result"), None)
    if not result_event:
        raise RuntimeError("no `result` event in CLI output")

    text = result_event.get("result", "")
    cost = float(result_event.get("total_cost_usd", 0.0))
    return text, cost


def main() -> int:
    p = argparse.ArgumentParser(description="HumanEval via claude CLI (taps Max + Additional Usage)")
    p.add_argument("--model", required=True, help="Claude model id, e.g. claude-sonnet-4-6 or claude-opus-4-7")
    p.add_argument("--count", type=int, default=164)
    p.add_argument("--dataset", type=str, default=None)
    p.add_argument("--output", type=str, default=None)
    p.add_argument("--call-timeout", type=int, default=180, help="per-CLI-call timeout in seconds")
    args = p.parse_args()

    dataset_path = args.dataset or download_dataset()
    problems = load_problems(dataset_path, args.count)
    print(f"Model:    {args.model}")
    print(f"Problems: {len(problems)}")
    print("Backend:  claude CLI (Max subscription + Additional Usage)")
    print()

    results: list[dict] = []
    passed = 0
    total_cost = 0.0
    start = time.time()

    for i, problem in enumerate(problems, 1):
        task_id = problem["task_id"]
        prompt = problem["prompt"]
        test_code = problem["test"]
        entry_point = problem["entry_point"]

        msg = (
            f"Complete the following Python function. Return ONLY the complete function implementation, "
            f"nothing else. No explanation, no markdown, just the Python code.\n\n{prompt}"
        )

        error_msg = ""
        cost = 0.0
        success = False
        try:
            text, cost = call_claude_cli(args.model, msg, timeout=args.call_timeout)
            total_cost += cost
            code = extract_code(text, prompt)
            success, error_msg = run_test(code, test_code, entry_point)
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
        print(f"[{i}/{len(problems)}] {task_id:30s} {status}  ({rate:.2f} p/s, ${total_cost:.2f}){suffix}")

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
            print(f"  {r['task_id']:30s}  {r['error'][:80]}")
        if len(failures) > 25:
            print(f"  ... +{len(failures) - 25} more")

    out_path = args.output or os.path.join(
        REPO_ROOT, "data", f"humaneval-{args.model}-results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "model": args.model,
            "backend": "claude-cli",
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
