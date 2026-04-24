#!/usr/bin/env python3
"""Run SWE-bench Lite instances through a Chimera agent.

Clones repos, checks out the base commit, runs the agent to fix the issue,
then verifies by running the failing tests.

Requires: git, python3 in PATH. Uses local environments (no Docker).

Usage:
    source .env
    python examples/swe_bench_lite_run.py --count 5
    python examples/swe_bench_lite_run.py --count 5 --max-steps 20
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.tool_group import AGENT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider

DATASET_PATH = "/tmp/swe-bench-lite.jsonl"


def load_instances(path: str, count: int) -> list[dict]:
    """Load SWE-bench instances, sorted by patch size (easiest first)."""
    instances = []
    with open(path) as f:
        for line in f:
            instances.append(json.loads(line))
    # Sort by patch size
    instances.sort(key=lambda d: len(d["patch"].splitlines()))
    return instances[:count]


def clone_and_checkout(repo: str, base_commit: str, workdir: str) -> bool:
    """Clone repo and checkout the base commit."""
    repo_url = f"https://github.com/{repo}.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "100", repo_url, workdir],
            capture_output=True, timeout=120,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            capture_output=True, cwd=workdir, timeout=30,
        )
        return True
    except Exception:
        return False


def run_failing_tests(workdir: str, fail_to_pass: list[str]) -> tuple[int, int]:
    """Run the failing tests and return (passed, total)."""
    if not fail_to_pass:
        return 0, 0

    passed = 0
    total = len(fail_to_pass)

    for test_id in fail_to_pass:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", test_id, "-x", "--tb=no", "-q"],
                capture_output=True, text=True,
                cwd=workdir, timeout=120,
            )
            if result.returncode == 0:
                passed += 1
        except Exception:
            pass

    return passed, total


def main():
    parser = argparse.ArgumentParser(description="Run SWE-bench Lite")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--dataset", type=str, default=DATASET_PATH)
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        print(f"Dataset not found: {args.dataset}")
        print("Download with: python -c \"from datasets import load_dataset; ...\"")
        sys.exit(1)

    provider = create_provider(model=args.model)
    instances = load_instances(args.dataset, args.count)

    print(f"Model:     {provider.model_name}")
    print(f"Instances: {len(instances)}")
    print(f"Max steps: {args.max_steps}")
    print()

    results: list[dict] = []
    resolved = 0
    total_cost = 0.0
    start = time.time()

    for i, inst in enumerate(instances, 1):
        instance_id = inst["instance_id"]
        repo = inst["repo"]
        base_commit = inst["base_commit"]
        problem = inst["problem_statement"]
        fail_to_pass = json.loads(inst["FAIL_TO_PASS"]) if isinstance(inst["FAIL_TO_PASS"], str) else inst["FAIL_TO_PASS"]

        print(f"[{i}/{len(instances)}] {instance_id}")
        print(f"  repo: {repo}, patch: {len(inst['patch'].splitlines())} lines")

        # Clone repo
        workdir = tempfile.mkdtemp(prefix=f"swebench_{instance_id}_")
        cloned = clone_and_checkout(repo, base_commit, workdir)
        if not cloned:
            print("  SKIP (clone failed)")
            results.append({"instance_id": instance_id, "status": "SKIP", "reason": "clone_failed"})
            continue

        # Run agent
        env = LocalEnvironment(workdir=workdir)
        env.setup()

        agent = Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=ReAct(max_steps=args.max_steps),
        )

        task = (
            f"Fix the following issue in this repository.\n\n"
            f"Issue: {problem[:2000]}\n\n"
            f"The fix should make the following test(s) pass:\n"
            f"{chr(10).join(fail_to_pass[:5])}\n\n"
            f"Edit the source code to fix the bug. Do not modify test files."
        )

        try:
            result = agent.run(task, env=env)
            cost = result.cost
            total_cost += cost

            # Verify: run the failing tests
            passed, total = run_failing_tests(workdir, fail_to_pass)
            success = passed == total and total > 0

            status = "RESOLVED" if success else "FAILED"
            if success:
                resolved += 1

            print(f"  {status} (tests: {passed}/{total}, cost: ${cost:.4f}, steps: {result.steps})")
            results.append({
                "instance_id": instance_id,
                "status": status,
                "tests_passed": passed,
                "tests_total": total,
                "cost": cost,
                "steps": result.steps,
            })

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"instance_id": instance_id, "status": "ERROR", "error": str(e)})

        finally:
            env.cleanup()
            shutil.rmtree(workdir, ignore_errors=True)

    # Report
    elapsed = time.time() - start
    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    for r in results:
        print(f"  {r['instance_id']:40s} {r['status']}")
    print()
    print(f"Resolve rate: {resolved}/{len(instances)} ({100 * resolved / len(instances):.1f}%)")
    print(f"Total cost:   ${total_cost:.4f}")
    print(f"Time:         {elapsed:.0f}s")

    # Save
    results_path = f"/tmp/swebench_lite_results_{provider.model_name}.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results saved to: {results_path}")


if __name__ == "__main__":
    main()
