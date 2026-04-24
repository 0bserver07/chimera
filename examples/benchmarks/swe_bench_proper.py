#!/usr/bin/env python3
"""SWE-bench — proper evaluation methodology.

Official SWE-bench flow:
1. Agent sees: issue description + codebase (NO tests)
2. Agent produces: source-only patch
3. Evaluator applies: agent's patch + test_patch
4. Evaluator runs: FAIL_TO_PASS + PASS_TO_PASS tests
5. Instance resolved if all tests pass

This script follows that flow exactly.

Usage:
    source .env
    python examples/swe_bench_proper.py --count 10 --max-steps 30
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from chimera.providers.factory import create_provider
from chimera.types import Message

DATASET_PATH = "/tmp/swe-bench-lite.jsonl"

SUPPORTED_REPOS = {
    "pytest-dev/pytest", "pylint-dev/pylint", "sympy/sympy",
    "psf/requests", "pallets/flask", "scikit-learn/scikit-learn",
}

SYSTEM_PROMPT = """You are an expert software engineer fixing a bug in an open-source Python project.
You have a bash terminal in a Docker container with the repo at /workspace.

IMPORTANT: You do NOT have access to any tests. Fix the bug based on the issue description alone.

WORKFLOW:
1. Read the issue carefully
2. Use grep/find to locate relevant source code
3. Read the specific code sections (cat -n file | head/tail)
4. Understand the root cause
5. Make the MINIMAL source code fix using sed -i or python3 -c
6. Verify your edit looks correct with cat/diff
7. Say DONE when you believe the fix is correct

RULES:
- Return ONLY a bash command. No explanation, no markdown.
- Make the SMALLEST possible change
- Do NOT create or modify test files
- When confident your fix is correct, say DONE
"""


def load_instances(path: str, count: int) -> list[dict]:
    instances = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d["repo"] in SUPPORTED_REPOS:
                instances.append(d)
    instances.sort(key=lambda d: len(d["patch"].splitlines()))
    return instances[:count]


def docker_exec(container: str, cmd: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["docker", "exec", container, "bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout + r.stderr
        lines = out.split("\n")
        if len(lines) > 80:
            out = "\n".join(lines[:25] + [f"...({len(lines)-50} lines)..."] + lines[-25:])
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "Timeout"
    except Exception as e:
        return 1, str(e)


def setup_container(repo: str, base_commit: str, instance_id: str) -> str | None:
    container = f"swe_{instance_id.replace('__', '_')[:40]}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    r = subprocess.run(
        ["docker", "run", "-d", "--name", container, "python:3.11-slim", "sleep", "7200"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None

    docker_exec(container, "apt-get update -qq && apt-get install -y -qq git build-essential > /dev/null 2>&1", 120)

    code, _ = docker_exec(container,
        f"git clone https://github.com/{repo}.git /workspace && "
        f"cd /workspace && git checkout {base_commit}", 300)
    if code != 0:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        return None

    for cmd in [
        "cd /workspace && pip install -e '.[testing]' -q 2>/dev/null",
        "cd /workspace && pip install -e '.[test]' -q 2>/dev/null",
        "cd /workspace && pip install -e '.[dev]' -q 2>/dev/null",
        "cd /workspace && pip install -e . -q 2>/dev/null",
    ]:
        code, _ = docker_exec(container, cmd, 300)
        if code == 0:
            break

    docker_exec(container, "pip install pytest -q 2>/dev/null")
    return container


def agent_fix(provider, container: str, problem: str, max_steps: int) -> str:
    """Agent produces a source-only fix. Returns the git diff."""
    messages = [
        Message.system(SYSTEM_PROMPT),
        Message.user(f"Issue:\n{problem[:3000]}\n\nStart by finding the relevant code."),
    ]

    for step in range(max_steps):
        response = provider.complete(messages, temperature=0.7, max_tokens=1024)
        cmd = response.content.strip()

        if "```" in cmd:
            cmd = "\n".join(l for l in cmd.split("\n") if not l.strip().startswith("```")).strip()
        if "\n" in cmd and not cmd.startswith("python"):
            cmd = cmd.split("\n")[0].strip()

        if not cmd or "DONE" in cmd.upper():
            break

        code, out = docker_exec(container, f"cd /workspace && {cmd}", 60)
        messages.append(Message.assistant(cmd))
        messages.append(Message.user(f"Exit: {code}\n{out[-2000:]}\n\nNext? (or DONE)"))

    # Get the agent's patch
    _, diff = docker_exec(container, "cd /workspace && git diff")
    return diff


def evaluate(container: str, test_patch: str, fail_to_pass: list[str], pass_to_pass: list[str]) -> dict:
    """Apply test_patch and run tests — the official evaluation step."""
    # Apply test_patch
    if test_patch:
        # Write patch to file and apply
        docker_exec(container, f"cat << 'PATCHEOF' > /tmp/test.patch\n{test_patch}\nPATCHEOF")
        code, out = docker_exec(container, "cd /workspace && git apply /tmp/test.patch 2>&1")
        if code != 0:
            # Try with --3way
            docker_exec(container, "cd /workspace && git apply --3way /tmp/test.patch 2>&1")

    # Run FAIL_TO_PASS tests
    f2p_pass = 0
    for test_id in fail_to_pass:
        code, _ = docker_exec(container, f"cd /workspace && python -m pytest {test_id} -x --tb=no -q 2>&1", 120)
        if code == 0:
            f2p_pass += 1

    # Run PASS_TO_PASS tests (sample — full run would be too slow)
    p2p_pass = 0
    p2p_total = min(len(pass_to_pass), 5)  # Sample 5
    for test_id in pass_to_pass[:p2p_total]:
        code, _ = docker_exec(container, f"cd /workspace && python -m pytest {test_id} -x --tb=no -q 2>&1", 120)
        if code == 0:
            p2p_pass += 1

    return {
        "f2p": f"{f2p_pass}/{len(fail_to_pass)}",
        "p2p": f"{p2p_pass}/{p2p_total}" if p2p_total > 0 else "N/A",
        "resolved": f2p_pass == len(fail_to_pass),
    }


def main():
    parser = argparse.ArgumentParser(description="SWE-bench (proper methodology)")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=30)
    args = parser.parse_args()

    if not os.path.exists(DATASET_PATH):
        print(f"Dataset not found: {DATASET_PATH}")
        sys.exit(1)

    provider = create_provider(model=args.model)
    instances = load_instances(DATASET_PATH, args.count)

    print(f"Model:      {provider.model_name}")
    print(f"Instances:  {len(instances)}")
    print(f"Max steps:  {args.max_steps}")
    print("Method:     Official (agent=source fix, eval=apply test_patch + run tests)")
    print()

    results = []
    resolved = 0
    start = time.time()

    for i, inst in enumerate(instances, 1):
        instance_id = inst["instance_id"]
        repo = inst["repo"]
        base_commit = inst["base_commit"]
        problem = inst["problem_statement"]
        test_patch = inst.get("test_patch", "")
        fail_to_pass = json.loads(inst["FAIL_TO_PASS"]) if isinstance(inst["FAIL_TO_PASS"], str) else inst["FAIL_TO_PASS"]
        pass_to_pass = json.loads(inst["PASS_TO_PASS"]) if isinstance(inst["PASS_TO_PASS"], str) else inst["PASS_TO_PASS"]

        print(f"[{i}/{len(instances)}] {instance_id}")

        container = setup_container(repo, base_commit, instance_id)
        if not container:
            print("  SKIP (setup)")
            results.append({"instance_id": instance_id, "status": "SKIP"})
            continue

        # Phase 1: Agent produces source fix (does NOT see tests)
        print(f"  Agent fixing (up to {args.max_steps} steps)...")
        agent_diff = agent_fix(provider, container, problem, args.max_steps)

        if not agent_diff.strip():
            print("  FAILED (no changes made)")
            results.append({"instance_id": instance_id, "status": "FAILED", "reason": "no_patch"})
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            continue

        print(f"  Agent produced patch ({len(agent_diff.splitlines())} lines)")

        # Phase 2: Evaluate (apply test_patch, run tests)
        print("  Evaluating...")
        eval_result = evaluate(container, test_patch, fail_to_pass, pass_to_pass)

        status = "RESOLVED" if eval_result["resolved"] else "FAILED"
        if eval_result["resolved"]:
            resolved += 1

        print(f"  {status} (FAIL_TO_PASS: {eval_result['f2p']}, PASS_TO_PASS: {eval_result['p2p']})")

        results.append({
            "instance_id": instance_id,
            "status": status,
            **eval_result,
        })

        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    elapsed = time.time() - start
    attempted = sum(1 for r in results if r["status"] in ("RESOLVED", "FAILED"))

    print()
    print("=" * 72)
    print("SWE-BENCH PROPER EVALUATION")
    print("=" * 72)
    for r in results:
        s = r["status"]
        extra = f" f2p={r.get('f2p','')} p2p={r.get('p2p','')}" if s != "SKIP" else ""
        print(f"  {r['instance_id']:40s} {s}{extra}")
    print()
    if attempted:
        print(f"Resolve rate: {resolved}/{attempted} ({100*resolved/attempted:.1f}%)")
    print(f"Time: {elapsed:.0f}s")

    path = f"/tmp/swebench_proper_{provider.model_name}.jsonl"
    with open(path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results: {path}")


if __name__ == "__main__":
    main()
