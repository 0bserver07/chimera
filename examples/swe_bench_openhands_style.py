#!/usr/bin/env python3
"""SWE-bench scaffold replicating OpenHands' approach using Chimera primitives.

Key differences from our basic scaffold (matching what OpenHands does):
1. Long-horizon prompt with task planning workflow
2. In-context learning example (complete debugging sequence)
3. Efficiency-first prompting (combine commands)
4. Loop detection (detect repeated commands, force different approach)
5. Context window management (keep first msg + recent 50%)
6. temperature=0.0 (deterministic, not 0.7)
7. 30k char output truncation
8. Explicit exploration-first mandate
9. Never ask for help (full autonomy)
10. Higher iteration limit (50 steps)

Usage:
    source .env
    python examples/swe_bench_openhands_style.py --count 10 --max-steps 50
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
    "pytest-dev/pytest", "pylint-dev/pylint", "sympy/sympy",
    "psf/requests", "pallets/flask", "scikit-learn/scikit-learn",
}

# ─── OpenHands-style System Prompt ───────────────────────────────────

SYSTEM_PROMPT = """You are an autonomous software engineer tasked with fixing a bug in an open-source Python repository. You are working in a Docker container with full bash access at /workspace.

IMPORTANT RULES:
- You should ONLY interact with the environment. NEVER ask for human help.
- Each action is somewhat expensive. Wherever possible, combine multiple operations into a single command. For example, use sed to make multiple replacements at once, or chain grep commands with &&.
- Quality over speed. Think carefully before acting.

WORKFLOW (follow this order):
1. EXPLORATION: First, explore the repository structure to understand the codebase. Use find, ls, and grep to locate relevant files. Do NOT assume file paths.
2. ANALYSIS: Read the relevant source code carefully. Understand the root cause of the bug before writing any fix.
3. IMPLEMENTATION: Make the MINIMAL code change to fix the issue. Prefer small, surgical edits.
4. VERIFICATION: After making changes, verify your edit by reading the modified file. Check that your change looks correct.

FILE EDITING:
- For simple single-line changes: use sed -i 's/old_pattern/new_pattern/' file
- For multi-line or complex changes: use python3 -c "import pathlib; ..." to read, modify, and write
- NEVER create backup files or alternative versions. Edit files in place.
- After EVERY edit, cat the modified section to verify it looks correct.

RETURN FORMAT:
- Return ONLY a bash command to execute. No explanation, no markdown, no code fences.
- When you believe the bug is fixed, respond with exactly: DONE

EXAMPLE SESSION:
User: Fix the bug where foo() returns None instead of the expected value.
Assistant: find /workspace -type f -name "*.py" | xargs grep -l "def foo" | head -5
User: Exit: 0
/workspace/src/module.py
Assistant: cat -n /workspace/src/module.py | head -30
User: Exit: 0
     1  def foo(x):
     2      result = x * 2
     3      # missing return statement
     4
Assistant: sed -i '3s/.*# missing return statement/    return result/' /workspace/src/module.py
User: Exit: 0
Assistant: cat -n /workspace/src/module.py | head -5
User: Exit: 0
     1  def foo(x):
     2      result = x * 2
     3      return result
     4
Assistant: DONE
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

        # OpenHands-style truncation: max 30k chars, head+tail
        if len(out) > 30000:
            lines = out.split("\n")
            head = lines[:50]
            tail = lines[-50:]
            out = "\n".join(head) + f"\n\n[... {len(lines) - 100} lines truncated ...]\n\n" + "\n".join(tail)

        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "[Command timed out after {timeout}s]"
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


def agent_fix(provider, container: str, problem: str, max_steps: int) -> tuple[str, int]:
    """OpenHands-style agent loop. Returns (diff, steps_used)."""
    messages = [
        Message.system(SYSTEM_PROMPT),
        Message.user(
            f"Please fix the following issue in the repository at /workspace.\n\n"
            f"ISSUE:\n{problem[:4000]}\n\n"
            f"Begin by exploring the repository structure to understand the codebase."
        ),
    ]

    # Loop detection: track recent commands
    recent_commands: list[str] = []

    for step in range(max_steps):
        response = provider.complete(
            messages,
            temperature=0.0,  # OpenHands uses 0.0 for determinism
            max_tokens=2048,
        )
        cmd = response.content.strip()

        # Clean markdown fences
        if "```" in cmd:
            lines = cmd.split("\n")
            cmd = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

        # Take first line if there's explanation mixed in
        if "\n" in cmd:
            first = cmd.split("\n")[0].strip()
            # Keep multi-line if it's a python -c command
            if not cmd.startswith("python") and first and not first.startswith("#"):
                cmd = first

        if not cmd or cmd.upper().strip() == "DONE":
            break

        # Loop detection: if same command 3 times, force different approach
        recent_commands.append(cmd[:100])
        if len(recent_commands) >= 3 and len(set(recent_commands[-3:])) == 1:
            messages.append(Message.assistant(cmd))
            messages.append(Message.user(
                "You are repeating the same command. This approach is not working. "
                "Try a COMPLETELY DIFFERENT strategy to fix the bug."
            ))
            recent_commands.clear()
            continue

        # Execute
        code, output = docker_exec(container, f"cd /workspace && {cmd}", 60)

        messages.append(Message.assistant(cmd))
        messages.append(Message.user(f"Exit code: {code}\n{output[-3000:]}\n"))

        # Context window management: if too many messages, condense
        # Keep: system (0), first user (1), last 60% of conversation
        if len(messages) > 40:
            keep_count = max(int(len(messages) * 0.6), 10)
            condensed = messages[:2] + messages[-keep_count:]
            messages = condensed

    _, diff = docker_exec(container, "cd /workspace && git diff")
    return diff, step + 1 if 'step' in dir() else max_steps


def evaluate(container: str, test_patch: str, fail_to_pass: list[str], pass_to_pass: list[str]) -> dict:
    """Apply test_patch and run tests."""
    if test_patch:
        # Write and apply test patch
        # Escape for heredoc
        escaped = test_patch.replace("'", "'\\''")
        docker_exec(container, f"cd /workspace && echo '{escaped}' | git apply --allow-empty -", 30)
        # If that fails, try writing to file
        if test_patch:
            docker_exec(container,
                f"cd /workspace && python3 -c \""
                f"import pathlib; "
                f"pathlib.Path('/tmp/test.patch').write_text(pathlib.Path('/dev/stdin').read_text())\" "
                f"<< 'ENDPATCH'\n{test_patch}\nENDPATCH", 30)
            docker_exec(container, "cd /workspace && git apply /tmp/test.patch 2>/dev/null || git apply --3way /tmp/test.patch 2>/dev/null", 30)

    f2p_pass = 0
    for t in fail_to_pass:
        code, _ = docker_exec(container, f"cd /workspace && python -m pytest {t} -x --tb=no -q 2>&1", 120)
        if code == 0:
            f2p_pass += 1

    p2p_total = min(len(pass_to_pass), 5)
    p2p_pass = 0
    for t in pass_to_pass[:p2p_total]:
        code, _ = docker_exec(container, f"cd /workspace && python -m pytest {t} -x --tb=no -q 2>&1", 120)
        if code == 0:
            p2p_pass += 1

    return {
        "f2p": f"{f2p_pass}/{len(fail_to_pass)}",
        "p2p": f"{p2p_pass}/{p2p_total}" if p2p_total > 0 else "N/A",
        "resolved": f2p_pass == len(fail_to_pass),
    }


def main():
    parser = argparse.ArgumentParser(description="SWE-bench — OpenHands-style scaffold")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=50)
    args = parser.parse_args()

    if not os.path.exists(DATASET_PATH):
        print(f"Dataset not found: {DATASET_PATH}")
        sys.exit(1)

    provider = create_provider(model=args.model)
    instances = load_instances(DATASET_PATH, args.count)

    print(f"Model:      {provider.model_name}")
    print(f"Instances:  {len(instances)}")
    print(f"Max steps:  {args.max_steps}")
    print(f"Scaffold:   OpenHands-style (long-horizon prompt, loop detection, context condensation)")
    print(f"Temp:       0.0")
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
            print(f"  SKIP (setup)")
            results.append({"instance_id": instance_id, "status": "SKIP"})
            continue

        # Agent produces source fix (no test info given)
        print(f"  Fixing...")
        diff, steps = agent_fix(provider, container, problem, args.max_steps)

        if not diff.strip():
            print(f"  FAILED (no patch, {steps} steps)")
            results.append({"instance_id": instance_id, "status": "FAILED", "reason": "no_patch", "steps": steps})
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            continue

        print(f"  Patch: {len(diff.splitlines())} lines, {steps} steps")

        # Evaluate with test_patch
        print(f"  Evaluating...")
        eval_result = evaluate(container, test_patch, fail_to_pass, pass_to_pass)
        status = "RESOLVED" if eval_result["resolved"] else "FAILED"
        if eval_result["resolved"]:
            resolved += 1

        print(f"  {status} (f2p={eval_result['f2p']} p2p={eval_result['p2p']})")
        results.append({"instance_id": instance_id, "status": status, "steps": steps, **eval_result})
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    elapsed = time.time() - start
    attempted = sum(1 for r in results if r["status"] in ("RESOLVED", "FAILED"))

    print()
    print("=" * 72)
    print("SWE-BENCH — OPENHANDS-STYLE SCAFFOLD")
    print("=" * 72)
    for r in results:
        s = r["status"]
        extra = f" f2p={r.get('f2p','')} p2p={r.get('p2p','')} steps={r.get('steps','')}" if s != "SKIP" else ""
        print(f"  {r['instance_id']:40s} {s}{extra}")
    print()
    if attempted:
        print(f"Resolve rate: {resolved}/{attempted} ({100*resolved/attempted:.1f}%)")
    print(f"Time: {elapsed:.0f}s")

    path = f"/tmp/swebench_openhands_{provider.model_name}.jsonl"
    with open(path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results: {path}")


if __name__ == "__main__":
    main()
