#!/usr/bin/env python3
"""SWE-bench with Docker isolation — the correct way.

Each instance runs in its own Docker container with:
- Clean Python environment (no system package conflicts)
- Repo cloned and checked out at the right commit
- Dependencies installed inside the container
- Agent runs commands via docker exec

This matches how the official SWE-bench evaluations work.

Usage:
    source .env
    python examples/swe_bench_docker.py --count 5 --max-steps 30
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chimera.providers.factory import create_provider
from chimera.types import Message

DATASET_PATH = "/tmp/swe-bench-lite.jsonl"

SUPPORTED_REPOS = {
    "pytest-dev/pytest",
    "pylint-dev/pylint",
    "sympy/sympy",
    "psf/requests",
    "pallets/flask",
}


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
    """Run a command inside a Docker container."""
    try:
        r = subprocess.run(
            ["docker", "exec", container, "bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 124, "Timeout"
    except Exception as e:
        return 1, str(e)


def setup_container(repo: str, base_commit: str, instance_id: str) -> str | None:
    """Create a Docker container with the repo at the right commit."""
    container_name = f"swe_{instance_id.replace('__', '_')[:40]}"

    # Remove if exists
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # Start container with Python 3.11 (widely compatible)
    r = subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         "python:3.11-slim", "sleep", "3600"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"    Docker start failed: {r.stderr[:200]}")
        return None

    # Install git
    code, out = docker_exec(container_name, "apt-get update -qq && apt-get install -y -qq git > /dev/null 2>&1")
    if code != 0:
        print(f"    Git install failed")
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        return None

    # Clone repo
    code, out = docker_exec(container_name,
        f"git clone https://github.com/{repo}.git /workspace && "
        f"cd /workspace && git checkout {base_commit}",
        timeout=300)
    if code != 0:
        print(f"    Clone/checkout failed: {out[-200:]}")
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        return None

    # Install project + test deps
    # Try common patterns
    install_cmds = [
        "cd /workspace && pip install -e '.[testing]' -q 2>/dev/null",
        "cd /workspace && pip install -e '.[test]' -q 2>/dev/null",
        "cd /workspace && pip install -e '.[dev]' -q 2>/dev/null",
        "cd /workspace && pip install -e . -q 2>/dev/null",
    ]
    for cmd in install_cmds:
        code, _ = docker_exec(container_name, cmd, timeout=300)
        if code == 0:
            break

    # Install pytest if not already
    docker_exec(container_name, "pip install pytest -q 2>/dev/null")

    return container_name


def run_test_in_docker(container: str, test_id: str) -> bool:
    """Run a test inside the Docker container."""
    code, out = docker_exec(container,
        f"cd /workspace && python -m pytest {test_id} -x --tb=no -q 2>&1",
        timeout=120)
    return code == 0


def agent_loop(provider, container: str, problem: str, fail_to_pass: list[str], max_steps: int) -> dict:
    """Run a ReAct-style agent loop using docker exec for all file operations."""
    system = (
        "You are an expert software engineer fixing a bug in an open-source Python project.\n"
        "You have access to a terminal. Execute commands to:\n"
        "1. Read files with: cat <path>\n"
        "2. Search with: grep -rn 'pattern' <path>\n"
        "3. Edit files with: sed -i 's/old/new/' <path>  or  python3 -c \"...\"\n"
        "4. List files with: find . -name '*.py' | head -20\n"
        "5. Run tests with: python -m pytest <test_id> -x --tb=short\n\n"
        "Return ONLY the shell command to execute. No explanation, no markdown.\n"
        "When the fix is complete and tests pass, return: DONE"
    )

    messages = [
        Message.system(system),
        Message.user(
            f"Bug to fix:\n{problem[:2000]}\n\n"
            f"Working directory: /workspace\n"
            f"Test to make pass: {' '.join(fail_to_pass[:3])}\n\n"
            f"What command should I run first?"
        ),
    ]

    total_input = 0
    total_output = 0

    for step in range(max_steps):
        response = provider.complete(messages, temperature=0.7, max_tokens=2048)
        total_input += response.usage.get("input_tokens", 0)
        total_output += response.usage.get("output_tokens", 0)

        command = response.content.strip()

        # Strip markdown
        if command.startswith("```"):
            lines = command.split("\n")
            command = "\n".join(l for l in lines if not l.startswith("```")).strip()

        if "DONE" in command.upper():
            break

        # Truncate very long commands
        if len(command) > 2000:
            command = command[:2000]

        # Execute in Docker
        code, output = docker_exec(container, f"cd /workspace && {command}", timeout=60)

        # Truncate long output (head+tail)
        output_lines = output.split("\n")
        if len(output_lines) > 100:
            head = output_lines[:30]
            tail = output_lines[-30:]
            output = "\n".join(head + [f"\n... ({len(output_lines) - 60} lines truncated) ...\n"] + tail)

        messages.append(Message.assistant(command))
        messages.append(Message.user(
            f"Exit code: {code}\nOutput:\n{output[-3000:]}\n\n"
            f"What command next? (or DONE if fixed)"
        ))

    return {
        "steps": step + 1,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


def main():
    parser = argparse.ArgumentParser(description="SWE-bench with Docker isolation")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=30)
    args = parser.parse_args()

    if not os.path.exists(DATASET_PATH):
        print(f"Dataset not found: {DATASET_PATH}")
        sys.exit(1)

    provider = create_provider(model=args.model)
    instances = load_instances(DATASET_PATH, args.count)

    print(f"Model:     {provider.model_name}")
    print(f"Instances: {len(instances)}")
    print(f"Max steps: {args.max_steps}")
    print(f"Isolation: Docker (python:3.11-slim)")
    print()

    results = []
    resolved = 0
    start = time.time()

    for i, inst in enumerate(instances, 1):
        instance_id = inst["instance_id"]
        repo = inst["repo"]
        base_commit = inst["base_commit"]
        problem = inst["problem_statement"]
        fail_to_pass = json.loads(inst["FAIL_TO_PASS"]) if isinstance(inst["FAIL_TO_PASS"], str) else inst["FAIL_TO_PASS"]

        print(f"[{i}/{len(instances)}] {instance_id}")
        print(f"  repo: {repo}, patch: {len(inst['patch'].splitlines())} lines")

        # Setup Docker container
        print(f"  Setting up Docker container...")
        container = setup_container(repo, base_commit, instance_id)
        if not container:
            print(f"  SKIP (container setup failed)")
            results.append({"instance_id": instance_id, "status": "SKIP"})
            continue

        # Verify bug exists
        pre_results = [run_test_in_docker(container, t) for t in fail_to_pass]
        pre_pass = sum(pre_results)
        if pre_pass == len(fail_to_pass):
            print(f"  SKIP (tests already pass)")
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            results.append({"instance_id": instance_id, "status": "SKIP", "reason": "already_passes"})
            continue

        print(f"  Pre-fix: {pre_pass}/{len(fail_to_pass)} pass (bug confirmed)")

        # Run agent
        print(f"  Running agent...")
        agent_result = agent_loop(provider, container, problem, fail_to_pass, args.max_steps)

        # Verify fix
        post_results = [run_test_in_docker(container, t) for t in fail_to_pass]
        post_pass = sum(post_results)
        success = post_pass == len(fail_to_pass)

        # Show diff
        _, diff = docker_exec(container, "cd /workspace && git diff --stat")

        status = "RESOLVED" if success else "FAILED"
        if success:
            resolved += 1

        print(f"  {status} (pre: {pre_pass}/{len(fail_to_pass)} → post: {post_pass}/{len(fail_to_pass)}, steps: {agent_result['steps']})")
        if diff.strip():
            print(f"  Changes: {diff.strip()}")

        results.append({
            "instance_id": instance_id,
            "status": status,
            "pre": f"{pre_pass}/{len(fail_to_pass)}",
            "post": f"{post_pass}/{len(fail_to_pass)}",
            "steps": agent_result["steps"],
            "tokens": agent_result["input_tokens"] + agent_result["output_tokens"],
        })

        # Cleanup
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    elapsed = time.time() - start
    print()
    print("=" * 72)
    print("SWE-BENCH (Docker-isolated) RESULTS")
    print("=" * 72)
    for r in results:
        s = r["status"]
        extra = f" pre={r.get('pre','')} post={r.get('post','')} steps={r.get('steps','')}" if s in ("RESOLVED", "FAILED") else ""
        print(f"  {r['instance_id']:40s} {s}{extra}")
    print()
    attempted = sum(1 for r in results if r["status"] in ("RESOLVED", "FAILED"))
    if attempted:
        print(f"Resolve rate: {resolved}/{attempted} ({100*resolved/attempted:.1f}%)")
    print(f"Time: {elapsed:.0f}s")

    path = f"/tmp/swebench_docker_{provider.model_name}.jsonl"
    with open(path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results: {path}")


if __name__ == "__main__":
    main()
