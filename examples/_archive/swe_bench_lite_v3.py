#!/usr/bin/env python3
"""SWE-bench Lite v3: proper repo setup with full clone + dependency install.

Fixes from v2:
- Full clone (not shallow) to reach old base commits
- Installs project dependencies before running tests
- Verifies tests FAIL before agent runs (confirms reproducibility)
- Verifies tests PASS after agent runs (confirms fix)

Usage:
    source .env
    python examples/swe_bench_lite_v3.py --count 5 --max-steps 50
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

# Repos we can handle (have simple test setup)
SUPPORTED_REPOS = {
    "pytest-dev/pytest",
    "pylint-dev/pylint",
    "sympy/sympy",
    "psf/requests",
    "pallets/flask",
    "scikit-learn/scikit-learn",
}

SWE_PROMPT = """You are an expert software engineer fixing a bug in an open-source project.

Relevant files from investigation:
{investigation}

Instructions:
1. Read the relevant files identified above
2. Understand the bug from the problem description
3. Make the MINIMAL code change to fix the issue
4. Do NOT modify test files
5. After editing, read back the file to verify your change is correct
"""


def load_instances(path: str, count: int, repos: set[str] | None = None) -> list[dict]:
    instances = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if repos and d["repo"] not in repos:
                continue
            instances.append(d)
    instances.sort(key=lambda d: len(d["patch"].splitlines()))
    return instances[:count]


def setup_repo(repo: str, base_commit: str, workdir: str) -> bool:
    """Full clone + checkout + install dependencies."""
    try:
        # Full clone (needed for old commits)
        r = subprocess.run(
            ["git", "clone", f"https://github.com/{repo}.git", workdir],
            capture_output=True, timeout=300,
        )
        if r.returncode != 0:
            print(f"    Clone failed: {r.stderr.decode()[:200]}")
            return False

        # Checkout base commit
        r = subprocess.run(
            ["git", "checkout", base_commit],
            capture_output=True, cwd=workdir, timeout=30,
        )
        if r.returncode != 0:
            print(f"    Checkout failed: {r.stderr.decode()[:200]}")
            return False

        # Install dependencies
        if os.path.exists(os.path.join(workdir, "setup.py")) or os.path.exists(os.path.join(workdir, "pyproject.toml")):
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", ".", "-q", "--no-deps"],
                capture_output=True, cwd=workdir, timeout=120,
            )
            if r.returncode != 0:
                # Try without -e
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", ".", "-q", "--no-deps"],
                    capture_output=True, cwd=workdir, timeout=120,
                )

        return True
    except Exception as e:
        print(f"    Setup error: {e}")
        return False


def run_tests(workdir: str, fail_to_pass: list[str]) -> tuple[int, int, str]:
    """Run tests and return (passed, total, output)."""
    passed = 0
    total = len(fail_to_pass)
    outputs = []

    for test_id in fail_to_pass:
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", test_id, "-x", "--tb=short", "-q"],
                capture_output=True, text=True,
                cwd=workdir, timeout=120,
                env={**os.environ, "PYTHONPATH": workdir},
            )
            outputs.append(f"{test_id}: exit={r.returncode}\n{r.stdout[-200:]}\n{r.stderr[-200:]}")
            if r.returncode == 0:
                passed += 1
        except Exception as e:
            outputs.append(f"{test_id}: error={e}")

    return passed, total, "\n".join(outputs)


def investigate(provider, workdir: str, problem: str, fail_to_pass: list[str]) -> str:
    try:
        repo_map = generate_repo_map(workdir, max_tokens=2000, depth="file")
    except Exception:
        repo_map = ""

    from chimera.types import Message
    msg = (
        f"Given this repo and bug, identify the 3-5 source files most likely to need changes.\n\n"
        f"Repo structure:\n{repo_map[:3000]}\n\n"
        f"Bug:\n{problem[:1500]}\n\n"
        f"Failing tests: {', '.join(fail_to_pass[:5])}\n\n"
        f"Return ONLY file paths, one per line."
    )
    try:
        response = provider.complete([Message.user(msg)], max_tokens=500)
        return response.content.strip()
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Lite v3")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--all-repos", action="store_true", help="Try all repos, not just supported ones")
    args = parser.parse_args()

    if not os.path.exists(DATASET_PATH):
        print(f"Dataset not found: {DATASET_PATH}")
        sys.exit(1)

    provider = create_provider(model=args.model)
    repos = None if args.all_repos else SUPPORTED_REPOS
    instances = load_instances(DATASET_PATH, args.count, repos)

    print(f"Model:       {provider.model_name}")
    print(f"Instances:   {len(instances)}")
    print(f"Max steps:   {args.max_steps}")
    print(f"Repos:       {'all' if args.all_repos else ', '.join(sorted(SUPPORTED_REPOS))}")
    print()

    results = []
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

        workdir = tempfile.mkdtemp(prefix=f"swe3_{instance_id[:30]}_")

        # Setup
        print("  Cloning + installing...")
        if not setup_repo(repo, base_commit, workdir):
            print("  SKIP (setup failed)")
            results.append({"instance_id": instance_id, "status": "SKIP", "reason": "setup"})
            shutil.rmtree(workdir, ignore_errors=True)
            continue

        # Verify tests fail BEFORE fix
        pre_passed, pre_total, pre_output = run_tests(workdir, fail_to_pass)
        if pre_passed == pre_total and pre_total > 0:
            print("  SKIP (tests already pass — can't reproduce bug)")
            results.append({"instance_id": instance_id, "status": "SKIP", "reason": "already_passes"})
            shutil.rmtree(workdir, ignore_errors=True)
            continue
        if pre_total == 0:
            print("  SKIP (no tests to run)")
            results.append({"instance_id": instance_id, "status": "SKIP", "reason": "no_tests"})
            shutil.rmtree(workdir, ignore_errors=True)
            continue

        print(f"  Pre-fix: {pre_passed}/{pre_total} pass (confirmed bug exists)")

        # Investigate
        print("  Investigating...")
        investigation = investigate(provider, workdir, problem, fail_to_pass)

        # Run agent
        env = LocalEnvironment(workdir=workdir)
        env.setup()

        prompt = Prompt.from_string(SWE_PROMPT.format(investigation=investigation))
        agent = Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=ReAct(max_steps=args.max_steps),
            prompt=prompt,
        )

        task = (
            f"Fix this bug:\n\n{problem[:2000]}\n\n"
            f"Make these test(s) pass:\n{chr(10).join(fail_to_pass[:5])}\n\n"
            f"Read the relevant files, understand the bug, make the minimal fix. Do NOT edit test files."
        )

        try:
            result = agent.run(task, env=env)
            cost = result.cost
            total_cost += cost

            # Verify tests pass AFTER fix
            post_passed, post_total, _ = run_tests(workdir, fail_to_pass)
            success = post_passed == post_total and post_total > 0

            status = "RESOLVED" if success else "FAILED"
            if success:
                resolved += 1

            print(f"  {status} (pre: {pre_passed}/{pre_total} → post: {post_passed}/{post_total}, cost: ${cost:.4f}, steps: {result.steps})")
            results.append({
                "instance_id": instance_id, "status": status,
                "pre_tests": f"{pre_passed}/{pre_total}",
                "post_tests": f"{post_passed}/{post_total}",
                "cost": cost, "steps": result.steps,
            })
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"instance_id": instance_id, "status": "ERROR", "error": str(e)})
        finally:
            env.cleanup()
            shutil.rmtree(workdir, ignore_errors=True)

    elapsed = time.time() - start
    print()
    print("=" * 72)
    print("SWE-BENCH LITE v3 RESULTS")
    print("=" * 72)
    for r in results:
        s = r.get("status", "?")
        extra = ""
        if s == "RESOLVED" or s == "FAILED":
            extra = f" pre={r.get('pre_tests','')} post={r.get('post_tests','')} steps={r.get('steps','')} cost=${r.get('cost',0):.4f}"
        elif s == "SKIP":
            extra = f" ({r.get('reason','')})"
        print(f"  {r['instance_id']:40s} {s}{extra}")
    print()
    attempted = sum(1 for r in results if r["status"] in ("RESOLVED", "FAILED"))
    print(f"Attempted:    {attempted}/{len(instances)}")
    print(f"Resolve rate: {resolved}/{attempted} ({100*resolved/attempted:.1f}%)" if attempted else "")
    print(f"Total cost:   ${total_cost:.4f}")
    print(f"Time:         {elapsed:.0f}s")

    results_path = f"/tmp/swebench_lite_v3_{provider.model_name}.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results: {results_path}")


if __name__ == "__main__":
    main()
