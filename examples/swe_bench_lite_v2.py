#!/usr/bin/env python3
"""SWE-bench Lite v2: with investigator agent + more steps.

Improvement over v1:
- Runs an InvestigatorAgent first to find relevant files
- Injects investigation results into the agent's context
- 50 steps default (vs 15)
- Uses repo map for codebase awareness

Usage:
    source .env
    python examples/swe_bench_lite_v2.py --count 5 --max-steps 50
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool_group import AGENT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider
from chimera.context.repo_map import generate_repo_map

DATASET_PATH = "/tmp/swe-bench-lite.jsonl"

SWE_AGENT_PROMPT = """You are an expert software engineer fixing a bug in a large open-source project.

{investigation}

Instructions:
1. Read the relevant files identified above
2. Understand the bug from the problem description
3. Make the minimal code change to fix the issue
4. Do NOT modify test files
5. Focus on the specific bug — don't refactor unrelated code
"""


def load_instances(path: str, count: int) -> list[dict]:
    instances = []
    with open(path) as f:
        for line in f:
            instances.append(json.loads(line))
    instances.sort(key=lambda d: len(d["patch"].splitlines()))
    return instances[:count]


def clone_and_checkout(repo: str, base_commit: str, workdir: str) -> bool:
    try:
        subprocess.run(
            ["git", "clone", "--depth", "100", f"https://github.com/{repo}.git", workdir],
            capture_output=True, timeout=180,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            capture_output=True, cwd=workdir, timeout=30,
        )
        return True
    except Exception:
        return False


def investigate(provider, workdir: str, problem: str, fail_to_pass: list[str]) -> str:
    """Run a lightweight investigation to find relevant files."""
    # Generate a repo map (file-level only for speed)
    try:
        repo_map = generate_repo_map(workdir, max_tokens=2000, depth="file")
    except Exception:
        repo_map = "(repo map unavailable)"

    # Ask the model to identify relevant files
    from chimera.types import Message
    msg = (
        f"Given this repository structure and bug report, identify the 3-5 most relevant "
        f"source files (NOT test files) that likely need to be modified.\n\n"
        f"Repository structure:\n{repo_map[:3000]}\n\n"
        f"Bug report:\n{problem[:1500]}\n\n"
        f"Failing tests:\n{chr(10).join(fail_to_pass[:5])}\n\n"
        f"Return ONLY a list of file paths, one per line. No explanation."
    )
    try:
        response = provider.complete([Message.user(msg)], max_tokens=500)
        return response.content.strip()
    except Exception:
        return "(investigation failed)"


def run_failing_tests(workdir: str, fail_to_pass: list[str]) -> tuple[int, int]:
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
    parser = argparse.ArgumentParser(description="SWE-bench Lite v2 (with investigator)")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--dataset", type=str, default=DATASET_PATH)
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        print(f"Dataset not found: {args.dataset}")
        sys.exit(1)

    provider = create_provider(model=args.model)
    instances = load_instances(args.dataset, args.count)

    print(f"Model:       {provider.model_name}")
    print(f"Instances:   {len(instances)}")
    print(f"Max steps:   {args.max_steps}")
    print(f"Investigator: enabled")
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

        workdir = tempfile.mkdtemp(prefix=f"swebench_{instance_id}_")
        cloned = clone_and_checkout(repo, base_commit, workdir)
        if not cloned:
            print(f"  SKIP (clone failed)")
            results.append({"instance_id": instance_id, "status": "SKIP"})
            continue

        # Phase 1: Investigate
        print(f"  Investigating...")
        investigation = investigate(provider, workdir, problem, fail_to_pass)
        print(f"  Files identified: {investigation[:100]}...")

        # Phase 2: Fix
        env = LocalEnvironment(workdir=workdir)
        env.setup()

        prompt = Prompt.from_string(SWE_AGENT_PROMPT.format(investigation=f"Relevant files from investigation:\n{investigation}"))

        agent = Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=ReAct(max_steps=args.max_steps),
            prompt=prompt,
        )

        task = (
            f"Fix the following bug:\n\n"
            f"{problem[:2000]}\n\n"
            f"The fix should make these test(s) pass:\n"
            f"{chr(10).join(fail_to_pass[:5])}\n\n"
            f"Read the relevant files first, understand the bug, then make the minimal fix."
        )

        try:
            result = agent.run(task, env=env)
            cost = result.cost
            total_cost += cost

            # Phase 3: Verify
            test_passed, test_total = run_failing_tests(workdir, fail_to_pass)
            success = test_passed == test_total and test_total > 0

            status = "RESOLVED" if success else "FAILED"
            if success:
                resolved += 1

            print(f"  {status} (tests: {test_passed}/{test_total}, cost: ${cost:.4f}, steps: {result.steps})")
            results.append({
                "instance_id": instance_id,
                "status": status,
                "tests_passed": test_passed,
                "tests_total": test_total,
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
    print("SWE-BENCH LITE v2 RESULTS (with investigator)")
    print("=" * 72)
    for r in results:
        status = r.get("status", "?")
        steps = r.get("steps", "?")
        cost = r.get("cost", 0)
        print(f"  {r['instance_id']:40s} {status:10s} steps={steps} cost=${cost:.4f}" if isinstance(cost, float) else f"  {r['instance_id']:40s} {status}")
    print()
    print(f"Resolve rate: {resolved}/{len(instances)} ({100 * resolved / len(instances):.1f}%)")
    print(f"Total cost:   ${total_cost:.4f}")
    print(f"Time:         {elapsed:.0f}s")

    results_path = f"/tmp/swebench_lite_v2_results_{provider.model_name}.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results saved to: {results_path}")


if __name__ == "__main__":
    main()
