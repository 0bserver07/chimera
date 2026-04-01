#!/usr/bin/env python3
"""Run SWE-bench Lite using the new CodingAgent assembly.

This uses the full Phase 1-9 architecture instead of the old ReAct stack.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="..."
    export ANTHROPIC_MODEL="glm-5.1"
    python examples/swe_bench_coding_agent.py --count 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.assembly.coding_agent import CodingAgent
from chimera.core.loop_events import LoopEventType

DATASET_PATH = "/tmp/swe-bench-lite.jsonl"


def load_instances(path: str, count: int) -> list[dict]:
    """Load SWE-bench instances, sorted by patch size (easiest first)."""
    if not os.path.exists(path):
        print(f"Dataset not found at {path}. Downloading...")
        download_dataset(path)

    instances = []
    with open(path) as f:
        for line in f:
            instances.append(json.loads(line))
    instances.sort(key=lambda d: len(d.get("patch", "").splitlines()))
    return instances[:count]


def download_dataset(path: str) -> None:
    """Download SWE-bench Lite from HuggingFace."""
    try:
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        with open(path, "w") as f:
            for item in ds:
                f.write(json.dumps(dict(item)) + "\n")
        print(f"Downloaded {len(ds)} instances to {path}")
    except ImportError:
        print("pip install datasets  # required for download")
        sys.exit(1)


def clone_and_checkout(repo: str, base_commit: str, workdir: str) -> bool:
    """Clone repo and checkout base commit."""
    repo_url = f"https://github.com/{repo}.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "500", repo_url, workdir],
            capture_output=True, timeout=600, check=True,
        )
        # Fetch the specific commit if shallow clone missed it
        subprocess.run(
            ["git", "fetch", "--depth", "500", "origin", base_commit],
            capture_output=True, cwd=workdir, timeout=120,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            capture_output=True, cwd=workdir, timeout=30, check=True,
        )
        return True
    except Exception as e:
        print(f"  Clone failed: {e}")
        return False


def verify_fix(workdir: str, test_patch: str) -> bool:
    """Apply test patch and run tests to verify the fix."""
    if not test_patch:
        return False
    try:
        # Apply test patch
        proc = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=test_patch, capture_output=True, text=True, cwd=workdir, timeout=30,
        )
        if proc.returncode != 0:
            # Try with --3way
            proc = subprocess.run(
                ["git", "apply", "-"],
                input=test_patch, capture_output=True, text=True, cwd=workdir, timeout=30,
            )

        subprocess.run(
            ["git", "apply", "-"],
            input=test_patch, capture_output=True, text=True, cwd=workdir, timeout=30,
        )

        # Run pytest
        result = subprocess.run(
            ["python", "-m", "pytest", "--tb=short", "-q", "--timeout=60"],
            capture_output=True, text=True, cwd=workdir, timeout=120,
        )
        return result.returncode == 0
    except Exception:
        return False


async def run_instance(instance: dict, model: str, max_turns: int) -> dict:
    """Run CodingAgent on a single SWE-bench instance."""
    instance_id = instance["instance_id"]
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    problem = instance["problem_statement"]

    print(f"\n{'='*60}")
    print(f"Instance: {instance_id}")
    print(f"Repo: {repo}")
    print(f"Problem: {problem[:100]}...")

    # Clone and checkout
    workdir = tempfile.mkdtemp(prefix="swebench_")
    try:
        if not clone_and_checkout(repo, base_commit, workdir):
            return {"instance_id": instance_id, "resolved": False, "error": "clone_failed"}

        # Build the task prompt
        task = (
            f"You are working in the repository '{repo}'. "
            f"Fix the following issue:\n\n{problem}\n\n"
            f"Make the minimal changes needed to fix this issue. "
            f"Edit the relevant source files directly."
        )

        # Run CodingAgent
        start = time.time()
        agent = CodingAgent(
            model=model,
            preset="claude_code",
            project_dir=workdir,
        )
        # Disable streaming to avoid API timeout, bump turns for complex repos
        from chimera.assembly.presets import AssemblyConfig
        agent._config = AssemblyConfig(
            name="swebench",
            description="SWE-bench benchmark run",
            tool_set="coding",
            permissions=False,
            hooks=False,
            transcripts=False,
            content_replacement=False,
            compaction=False,
            streaming=False,
            max_turns=30,
        )

        output_parts = []
        tool_calls = 0
        async for event in agent.run(task):
            if event.type == LoopEventType.assistant:
                content = getattr(event.data, "content", "")
                if content.strip():
                    output_parts.append(content)
            elif event.type == LoopEventType.tool_result:
                tool_calls += 1
                tc, result = event.data if isinstance(event.data, tuple) else (None, event.data)
                tool_name = getattr(tc, "name", "?") if tc else "?"
                print(f"  [{tool_name}]", end="", flush=True)
            elif event.type == LoopEventType.result:
                reason = event.data.reason
                turns = event.data.turn_count

        elapsed = time.time() - start
        output = "\n".join(output_parts)

        # Get the diff (what the agent changed)
        diff_proc = subprocess.run(
            ["git", "diff"], capture_output=True, text=True, cwd=workdir, timeout=30,
        )
        agent_patch = diff_proc.stdout

        # Verify fix
        resolved = verify_fix(workdir, instance.get("test_patch", ""))

        result = {
            "instance_id": instance_id,
            "resolved": resolved,
            "turns": turns,
            "tool_calls": tool_calls,
            "elapsed_s": round(elapsed, 1),
            "patch_lines": len(agent_patch.splitlines()) if agent_patch else 0,
            "reason": reason,
        }

        status = "PASS" if resolved else "FAIL"
        print(f"\n  {status} — {turns} turns, {tool_calls} tools, {elapsed:.0f}s, {result['patch_lines']} patch lines")

        return result

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5, help="Number of instances")
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument("--output", default="data/swebench-coding-agent-results.jsonl", help="Output file")
    args = parser.parse_args()

    model = args.model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    print(f"SWE-bench Lite — CodingAgent ({model})")
    print(f"Running {args.count} instances\n")

    instances = load_instances(DATASET_PATH, args.count)
    results = []
    passed = 0

    for i, inst in enumerate(instances, 1):
        print(f"\n[{i}/{args.count}]", end="")
        result = await run_instance(inst, model, max_turns=20)
        results.append(result)
        if result.get("resolved"):
            passed += 1

        # Write results incrementally
        with open(args.output, "a") as f:
            f.write(json.dumps(result) + "\n")

    # Summary
    total = len(results)
    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{total} resolved ({100*passed/total:.0f}%)")
    print(f"Output: {args.output}")

    for r in results:
        status = "PASS" if r["resolved"] else "FAIL"
        print(f"  {status} {r['instance_id']} ({r.get('elapsed_s', 0)}s)")


if __name__ == "__main__":
    asyncio.run(main())
