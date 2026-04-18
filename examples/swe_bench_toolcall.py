#!/usr/bin/env python3
"""SWE-bench with structured tool calling — matching OpenHands' approach.

The key insight: OpenHands uses LLM function calling (structured JSON tool use),
NOT raw text "give me a bash command". This is dramatically more reliable because
the model returns structured arguments instead of free-text that needs parsing.

This script uses Chimera's provider.complete() with tool schemas, matching
how OpenHands' CodeActAgent works with str_replace_editor + cmd_run.

Usage:
    source .env
    python examples/swe_bench_toolcall.py --count 10 --max-steps 50
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
from chimera.types import Message, ToolCall

DATASET_PATH = "/tmp/swe-bench-lite.jsonl"

SUPPORTED_REPOS = {
    "pytest-dev/pytest", "pylint-dev/pylint", "sympy/sympy",
    "psf/requests", "pallets/flask", "scikit-learn/scikit-learn",
}

# ─── Tool schemas (matching OpenHands' action space) ─────────────────

TOOLS = [
    {
        "name": "execute_bash",
        "description": (
            "Execute a bash command in the workspace. Use for: running tests, "
            "searching code (grep/find), viewing files (cat), installing deps. "
            "For file editing, prefer the str_replace_editor tool instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute. Working directory is /workspace.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "str_replace_editor",
        "description": (
            "Custom editing tool for viewing, creating and editing files. "
            "Commands: view (show file with line numbers), create (new file), "
            "str_replace (replace exact text), insert (add lines after a line number). "
            "The old_str must match EXACTLY — include enough context (3-5 lines) to be unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": ["view", "create", "str_replace", "insert"],
                    "description": "The editor command to run.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file, e.g. /workspace/src/module.py",
                },
                "old_str": {
                    "type": "string",
                    "description": "For str_replace: the exact text to find and replace. Must match uniquely.",
                },
                "new_str": {
                    "type": "string",
                    "description": "For str_replace: the replacement text. For insert: the text to insert.",
                },
                "insert_line": {
                    "type": "integer",
                    "description": "For insert: line number after which to insert new_str.",
                },
                "view_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "For view: [start_line, end_line] to show. Omit to show full file.",
                },
                "file_text": {
                    "type": "string",
                    "description": "For create: the full content of the new file.",
                },
            },
            "required": ["command", "path"],
        },
    },
    {
        "name": "think",
        "description": (
            "Use this to think through your approach before taking action. "
            "Describe what you've learned and what you plan to do next."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Your reasoning about the problem and next steps.",
                },
            },
            "required": ["thought"],
        },
    },
    {
        "name": "finish",
        "description": "Call this when you believe the bug is fixed.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

SYSTEM_PROMPT = """You are an autonomous software engineer. You will be given a bug report for an open-source Python project. Your task is to fix the bug by editing the source code.

ENVIRONMENT:
- You are in a Docker container with the full repository at /workspace
- You have bash access and a file editor

WORKFLOW:
1. First, EXPLORE the repository to understand its structure
2. Use grep/find to LOCATE the relevant source code
3. READ the specific code sections using the editor's view command
4. THINK about the root cause before making any changes
5. Make the MINIMAL edit using str_replace_editor
6. Verify your change looks correct by viewing the file again
7. Call finish when done

IMPORTANT:
- Do NOT modify test files
- Make the SMALLEST possible change
- The old_str in str_replace must match EXACTLY, including whitespace
- Include 3-5 lines of context in old_str to ensure uniqueness
- Always verify your edit by viewing the file after changing it
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
        if len(out) > 30000:
            lines = out.split("\n")
            out = "\n".join(lines[:40]) + f"\n\n[... {len(lines)-80} lines truncated ...]\n\n" + "\n".join(lines[-40:])
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "Command timed out"
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


def execute_tool(container: str, tool_call: ToolCall) -> str:
    """Execute a tool call and return the result."""
    name = tool_call.name
    args = tool_call.arguments

    if name == "execute_bash":
        code, out = docker_exec(container, f"cd /workspace && {args.get('command', '')}", 60)
        return f"Exit code: {code}\n{out}"

    elif name == "str_replace_editor":
        cmd = args.get("command", "")
        path = args.get("path", "")

        if cmd == "view":
            view_range = args.get("view_range")
            if view_range and len(view_range) == 2:
                code, out = docker_exec(container, f"cat -n {path} | sed -n '{view_range[0]},{view_range[1]}p'")
            else:
                code, out = docker_exec(container, f"cat -n {path}")
            return out if code == 0 else f"Error viewing {path}: {out}"

        elif cmd == "create":
            file_text = args.get("file_text", "")
            escaped = file_text.replace("'", "'\\''")
            code, out = docker_exec(container, f"mkdir -p $(dirname {path}) && echo '{escaped}' > {path}")
            return f"File created: {path}" if code == 0 else f"Error: {out}"

        elif cmd == "str_replace":
            old_str = args.get("old_str", "")
            new_str = args.get("new_str", "")
            if not old_str:
                return "Error: old_str is required for str_replace"

            # Use Python for reliable str_replace (handles multiline, special chars)
            if old_str.count("'''") > 0 or new_str.count("'''") > 0:
                # Fallback for strings containing triple quotes
                code, out = docker_exec(container,
                    f"cd /workspace && python3 -c \"\nimport pathlib, sys\n"
                    f"p = pathlib.Path(sys.argv[1])\n"
                    f"content = p.read_text()\n"
                    f"old = sys.argv[2]\n"
                    f"new = sys.argv[3]\n"
                    f"if content.count(old) != 1:\n"
                    f"    print(f'Error: found {{content.count(old)}} matches, need exactly 1')\n"
                    f"    sys.exit(1)\n"
                    f"p.write_text(content.replace(old, new, 1))\n"
                    f"print('Replacement applied')\n"
                    f"\" '{path}' '{old_str}' '{new_str}'"
                )
            else:
                code, out = docker_exec(container,
                    f"cd /workspace && python3 << 'PYEOF'\n"
                    f"import pathlib\n"
                    f"p = pathlib.Path('{path}')\n"
                    f"content = p.read_text()\n"
                    f"old = '''{old_str}'''\n"
                    f"new = '''{new_str}'''\n"
                    f"count = content.count(old)\n"
                    f"if count == 0:\n"
                    f"    print('Error: old_str not found in file')\n"
                    f"elif count > 1:\n"
                    f"    print(f'Error: old_str found {{count}} times, must be unique. Add more context.')\n"
                    f"else:\n"
                    f"    p.write_text(content.replace(old, new, 1))\n"
                    f"    print('Replacement applied successfully')\n"
                    f"PYEOF"
                )
            return out

        elif cmd == "insert":
            new_str = args.get("new_str", "")
            insert_line = args.get("insert_line", 0)
            code, out = docker_exec(container,
                f"cd /workspace && python3 << 'PYEOF'\n"
                f"import pathlib\n"
                f"p = pathlib.Path('{path}')\n"
                f"lines = p.read_text().splitlines(True)\n"
                f"insert_at = {insert_line}\n"
                f"new_lines = '''{new_str}'''.splitlines(True)\n"
                f"lines[insert_at:insert_at] = new_lines\n"
                f"p.write_text(''.join(lines))\n"
                f"print(f'Inserted {{len(new_lines)}} lines after line {{insert_at}}')\n"
                f"PYEOF"
            )
            return out

    elif name == "think":
        return "Thought recorded."

    elif name == "finish":
        return "FINISH"

    return f"Unknown tool: {name}"


def agent_fix(provider, container: str, problem: str, max_steps: int) -> tuple[str, int]:
    """Run agent with structured tool calling."""
    messages = [
        Message.system(SYSTEM_PROMPT),
        Message.user(f"Please fix the following bug:\n\n{problem[:4000]}"),
    ]

    recent_cmds: list[str] = []

    for step in range(max_steps):
        response = provider.complete(messages, tools=TOOLS, temperature=0.0, max_tokens=4096)

        if not response.has_tool_calls:
            # Model returned text — add it and continue
            messages.append(Message.assistant(response.content))
            messages.append(Message.user("Please use the available tools to fix the bug."))
            continue

        # Process tool calls
        messages.append(Message(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        ))

        for tc in response.tool_calls:
            result = execute_tool(container, tc)

            if result == "FINISH":
                _, diff = docker_exec(container, "cd /workspace && git diff")
                return diff, step + 1

            messages.append(Message(
                role="tool",
                content=result[-3000:],  # Truncate tool results
                call_id=tc.id,
            ))

            # Loop detection
            cmd_sig = f"{tc.name}:{str(tc.arguments)[:50]}"
            recent_cmds.append(cmd_sig)
            if len(recent_cmds) >= 4 and len(set(recent_cmds[-4:])) == 1:
                messages.append(Message.user(
                    "You are repeating the same action. Try a completely different approach."
                ))
                recent_cmds.clear()

        # Context condensation: keep system + first user + recent messages
        if len(messages) > 60:
            keep = max(int(len(messages) * 0.5), 20)
            messages = messages[:2] + messages[-keep:]

    _, diff = docker_exec(container, "cd /workspace && git diff")
    return diff, max_steps


def evaluate(container: str, test_patch: str, fail_to_pass: list[str], pass_to_pass: list[str]) -> dict:
    if test_patch:
        docker_exec(container,
            f"cd /workspace && python3 << 'PYEOF'\n"
            f"import pathlib\n"
            f"pathlib.Path('/tmp/test.patch').write_text('''{test_patch}''')\n"
            f"PYEOF", 30)
        docker_exec(container, "cd /workspace && git apply /tmp/test.patch 2>/dev/null || git apply --3way /tmp/test.patch 2>/dev/null", 30)

    f2p_pass = sum(1 for t in fail_to_pass if docker_exec(container, f"cd /workspace && python -m pytest {t} -x --tb=no -q 2>&1", 120)[0] == 0)
    p2p_total = min(len(pass_to_pass), 5)
    p2p_pass = sum(1 for t in pass_to_pass[:p2p_total] if docker_exec(container, f"cd /workspace && python -m pytest {t} -x --tb=no -q 2>&1", 120)[0] == 0)

    return {
        "f2p": f"{f2p_pass}/{len(fail_to_pass)}",
        "p2p": f"{p2p_pass}/{p2p_total}" if p2p_total > 0 else "N/A",
        "resolved": f2p_pass == len(fail_to_pass),
    }


def main():
    parser = argparse.ArgumentParser(description="SWE-bench with tool calling")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=50)
    args = parser.parse_args()

    if not os.path.exists(DATASET_PATH):
        sys.exit(f"Dataset not found: {DATASET_PATH}")

    provider = create_provider(model=args.model)
    instances = load_instances(DATASET_PATH, args.count)

    print(f"Model:      {provider.model_name}")
    print(f"Instances:  {len(instances)}")
    print(f"Max steps:  {args.max_steps}")
    print("Scaffold:   Tool calling (str_replace_editor + execute_bash + think + finish)")
    print("Temp:       0.0")
    print()

    results = []
    resolved = 0
    start = time.time()

    for i, inst in enumerate(instances, 1):
        instance_id = inst["instance_id"]
        problem = inst["problem_statement"]
        test_patch = inst.get("test_patch", "")
        fail_to_pass = json.loads(inst["FAIL_TO_PASS"]) if isinstance(inst["FAIL_TO_PASS"], str) else inst["FAIL_TO_PASS"]
        pass_to_pass = json.loads(inst["PASS_TO_PASS"]) if isinstance(inst["PASS_TO_PASS"], str) else inst["PASS_TO_PASS"]

        print(f"[{i}/{len(instances)}] {instance_id}")
        container = setup_container(inst["repo"], inst["base_commit"], instance_id)
        if not container:
            print("  SKIP")
            results.append({"instance_id": instance_id, "status": "SKIP"})
            continue

        print(f"  Fixing (tool calling, {args.max_steps} steps)...")
        diff, steps = agent_fix(provider, container, problem, args.max_steps)

        if not diff.strip():
            print(f"  FAILED (no patch, {steps} steps)")
            results.append({"instance_id": instance_id, "status": "FAILED", "reason": "no_patch", "steps": steps})
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            continue

        print(f"  Patch: {len(diff.splitlines())} diff lines, {steps} steps. Evaluating...")
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
    print("SWE-BENCH — TOOL CALLING SCAFFOLD")
    print("=" * 72)
    for r in results:
        s = r["status"]
        extra = f" f2p={r.get('f2p','')} p2p={r.get('p2p','')} steps={r.get('steps','')}" if s != "SKIP" else ""
        print(f"  {r['instance_id']:40s} {s}{extra}")
    print()
    if attempted:
        print(f"Resolve rate: {resolved}/{attempted} ({100*resolved/attempted:.1f}%)")
    print(f"Time: {elapsed:.0f}s")


if __name__ == "__main__":
    main()
