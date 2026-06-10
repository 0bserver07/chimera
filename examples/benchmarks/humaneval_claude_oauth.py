#!/usr/bin/env python3
"""HumanEval runner for Claude via Claude Code Max OAuth token.

Chimera's anthropic provider uses x-api-key auth, which the API rejects
for OAuth tokens (sk-ant-oat01-*). This script bypasses chimera's provider
and hits api.anthropic.com directly with `Authorization: Bearer <token>`
plus `anthropic-beta: oauth-2025-04-20` -- the auth pattern Claude Code
uses for Max subscriptions.

Reuses extract_code, run_test, and the result schema from
examples/benchmarks/humaneval_full.py so all chimera HumanEval rows
share the same data/ layout.

Usage:
    export ANTHROPIC_API_KEY=<your sk-ant-oat01-... OAuth token>
    python examples/benchmarks/humaneval_claude_oauth.py --model claude-haiku-4-5-20251001 --count 2
    python examples/benchmarks/humaneval_claude_oauth.py --model claude-sonnet-4-6 --count 164
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from humaneval_full import (  # type: ignore
    REPO_ROOT,
    download_dataset,
    extract_code,
    load_problems,
    run_test,
)

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OAUTH_BETA = "oauth-2025-04-20"

# Approximate Anthropic public pricing (USD per Mtok input/output).
# Adjust if your billing differs; affects the cost summary line and the
# per-problem cost field in the detail JSONL.
PRICING = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5":          (1.00, 5.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "claude-opus-4-7":           (5.00, 25.00),
}


def cost_for(model: str, usage: dict) -> float:
    if model not in PRICING:
        return 0.0
    p_in, p_out = PRICING[model]
    return (
        usage.get("input_tokens", 0) * p_in
        + usage.get("output_tokens", 0) * p_out
    ) / 1_000_000


def call_oauth(token: str, model: str, msg: str, max_tokens: int = 1024,
               max_retries: int = 4, base_delay: float = 2.0) -> tuple[str, dict]:
    """POST /v1/messages with OAuth Bearer auth. Retries 429/5xx/network."""
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": msg}],
    }).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": OAUTH_BETA,
        "content-type": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(API_URL, method="POST", headers=headers, data=payload)
            with urllib.request.urlopen(req, timeout=90) as r:
                body = json.loads(r.read())
            return body["content"][0]["text"], body.get("usage", {})
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()[:300] if e.fp else ""
            last_err = RuntimeError(f"HTTP {e.code}: {err_body}")
            if e.code in (401, 403):
                raise last_err
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise last_err
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
    assert last_err is not None
    raise last_err


def main() -> int:
    p = argparse.ArgumentParser(description="HumanEval against Claude via Claude Code Max OAuth")
    p.add_argument("--model", required=True, help="Claude model id")
    p.add_argument("--count", type=int, default=164)
    p.add_argument("--dataset", type=str, default=None)
    p.add_argument("--output", type=str, default=None)
    args = p.parse_args()

    token = os.environ.get("ANTHROPIC_API_KEY", "")
    if not token.startswith("sk-ant-oat01-"):
        print("error: ANTHROPIC_API_KEY must be a Claude OAuth token (sk-ant-oat01-*)", file=sys.stderr)
        print("       export ANTHROPIC_API_KEY=<sk-ant-oat01-...> first.", file=sys.stderr)
        return 2

    dataset_path = args.dataset or download_dataset()
    problems = load_problems(dataset_path, args.count)

    print(f"Model:    {args.model}")
    print(f"Problems: {len(problems)}")
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
            text, usage = call_oauth(token, args.model, msg, max_tokens=1024)
            code = extract_code(text, prompt)
            cost = cost_for(args.model, usage)
            total_cost += cost
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
        print(f"[{i}/{len(problems)}] {task_id:30s} {status}  ({rate:.2f} prob/s){suffix}")

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
        for r in failures:
            print(f"  {r['task_id']:30s}  {r['error'][:80]}")

    out_path = args.output or os.path.join(
        REPO_ROOT, "data", f"humaneval-{args.model}-results.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "model": args.model,
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
