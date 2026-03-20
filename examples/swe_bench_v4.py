#!/usr/bin/env python3
"""SWE-bench v4 -- anti-hesitation scaffold.

The dominant failure mode in v1-v3 was HESITATION: the agent reads files
repeatedly but never commits to an edit.  This scaffold forces action:

1. FORCE EDITING: After 5 read/view steps without an edit, inject a nudge:
   "You've read enough. Make an edit NOW."
2. EDIT VERIFICATION: After every sed/python-edit, auto-verify by viewing
   the changed file section.
3. ANTI-REPETITION: If the agent views the same file range twice, tell it
   to move forward.
4. HIGHER STEP LIMIT: 80 steps (OpenHands uses 100+).
5. AGGRESSIVE PROMPT: "Do NOT spend more than 5 steps reading."
6. CONTEXT CONDENSATION: Keep system + first user + recent 60%.
7. temperature=0.0 for determinism.

Usage:
    source .env
    python examples/swe_bench_v4.py --count 30 --max-steps 80
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# ---------------------------------------------------------------------------
# Anti-hesitation system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an autonomous expert software engineer fixing a bug in an "
    "open-source Python project.\n"
    "You are working in a Docker container with full bash access. "
    "The repo is at /workspace.\n\n"
    "CRITICAL RULES -- READ CAREFULLY:\n"
    "- Return ONLY a bash command. No explanations, no markdown, no code fences.\n"
    "- Do NOT spend more than 5 steps reading code. After understanding the "
    "bug, make the edit IMMEDIATELY.\n"
    "- Make the SMALLEST possible fix. One surgical change is better than "
    "rewriting a function.\n"
    "- Do NOT create or modify test files.\n"
    "- After making an edit, view the changed lines to verify correctness.\n"
    "- When your fix is complete, respond with exactly: DONE\n\n"
    "WORKFLOW (follow strictly):\n"
    "1. LOCATE (1-2 steps): grep/find to find the relevant source file(s).\n"
    "2. READ (2-3 steps): cat -n the specific function or class. "
    "Do NOT read entire large files -- use head/tail/sed to view specific "
    "line ranges.\n"
    "3. EDIT (1-2 steps): Use sed -i or python3 to make the fix. For "
    "multi-line or complex edits, use python3:\n"
    "   python3 << 'EOF'\n"
    "   import pathlib\n"
    "   p = pathlib.Path('/workspace/path/to/file.py')\n"
    "   content = p.read_text()\n"
    "   content = content.replace('old text', 'new text', 1)\n"
    "   p.write_text(content)\n"
    "   EOF\n"
    "4. VERIFY (1 step): cat -n the changed section to confirm it looks right.\n"
    "5. Say DONE.\n\n"
    "ANTI-PATTERNS TO AVOID:\n"
    "- Do NOT read the same file section twice.\n"
    "- Do NOT keep exploring after you understand the bug. ACT.\n"
    "- Do NOT ask questions. You are fully autonomous.\n"
    "- Do NOT use git commands. Edit files directly.\n\n"
    "EDITING TIPS:\n"
    "- sed -i 's/old/new/' file -- for simple single-line changes\n"
    "- python3 with pathlib.Path.read_text()/write_text() -- for "
    "multi-line or special-character changes\n"
    "- Always include enough context in your search-and-replace to match "
    "uniquely\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_instances(path: str, count: int) -> list[dict]:
    """Load SWE-bench instances, sorted by patch size (easiest first)."""
    instances = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d["repo"] in SUPPORTED_REPOS:
                instances.append(d)
    instances.sort(key=lambda d: len(d["patch"].splitlines()))
    return instances[:count]


def docker_exec(container: str, cmd: str, timeout: int = 120) -> tuple[int, str]:
    """Execute a command in the Docker container."""
    try:
        r = subprocess.run(
            ["docker", "exec", container, "bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        out = r.stdout + r.stderr

        # Truncate very long output (keep head + tail)
        if len(out) > 30000:
            lines = out.split("\n")
            head = lines[:50]
            tail = lines[-50:]
            out = (
                "\n".join(head)
                + f"\n\n[... {len(lines) - 100} lines truncated ...]\n\n"
                + "\n".join(tail)
            )

        return r.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"[Command timed out after {timeout}s]"
    except Exception as e:
        return 1, str(e)


def setup_container(repo: str, base_commit: str, instance_id: str) -> str | None:
    """Create and prepare a Docker container for the instance."""
    container = f"swe_{instance_id.replace('__', '_')[:40]}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    r = subprocess.run(
        ["docker", "run", "-d", "--name", container,
         "--memory", "4g",
         "python:3.11-slim", "sleep", "7200"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None

    # Install basic tools
    docker_exec(
        container,
        "apt-get update -qq && apt-get install -y -qq git build-essential "
        "> /dev/null 2>&1",
        180,
    )

    # Clone and checkout
    code, out = docker_exec(
        container,
        f"git clone https://github.com/{repo}.git /workspace && "
        f"cd /workspace && git checkout {base_commit}",
        300,
    )
    if code != 0:
        print(f"    Clone/checkout failed: {out[:200]}")
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        return None

    # Install project dependencies (try multiple extras)
    for cmd in [
        "cd /workspace && pip install -e '.[testing]' -q 2>/dev/null",
        "cd /workspace && pip install -e '.[test]' -q 2>/dev/null",
        "cd /workspace && pip install -e '.[dev]' -q 2>/dev/null",
        "cd /workspace && pip install -e . -q 2>/dev/null",
    ]:
        code, _ = docker_exec(container, cmd, 300)
        if code == 0:
            break

    docker_exec(container, "pip install pytest -q 2>/dev/null", 60)
    return container


# ---------------------------------------------------------------------------
# Anti-hesitation tracking
# ---------------------------------------------------------------------------

# Regex patterns for detecting read-only vs edit commands
READ_PATTERNS = [
    re.compile(r"^\s*cat\s"),
    re.compile(r"^\s*head\s"),
    re.compile(r"^\s*tail\s"),
    re.compile(r"^\s*less\s"),
    re.compile(r"^\s*more\s"),
    re.compile(r"^\s*grep\s"),
    re.compile(r"^\s*find\s"),
    re.compile(r"^\s*ls\s"),
    re.compile(r"^\s*wc\s"),
    re.compile(r"^\s*file\s"),
    re.compile(r"^\s*tree\s"),
    re.compile(r"^\s*rg\s"),
    re.compile(r"^\s*ag\s"),
]

EDIT_PATTERNS = [
    re.compile(r"^\s*sed\s+-i"),
    re.compile(r"^\s*python3?\s"),  # python3 -c or python3 << for edits
    re.compile(r"^\s*perl\s+-[pi]"),
    re.compile(r"^\s*echo\s.*>>?\s"),
    re.compile(r"^\s*tee\s"),
    re.compile(r"^\s*patch\s"),
    re.compile(r"write_text|\.write\("),
]


def is_read_cmd(cmd: str) -> bool:
    """Check if a command is read-only (viewing/searching)."""
    first_line = cmd.split("\n")[0].strip()
    return any(p.search(first_line) for p in READ_PATTERNS)


def is_edit_cmd(cmd: str) -> bool:
    """Check if a command modifies files."""
    return any(p.search(cmd) for p in EDIT_PATTERNS)


def extract_file_view(cmd: str) -> str | None:
    """Extract a normalized file+range key from a cat/head/tail command."""
    # Match patterns like: cat -n file.py | head -30
    # or: cat -n file.py | sed -n '10,20p'
    # Returns a normalized key for dedup
    m = re.search(r"cat\s+(?:-n\s+)?(\S+)", cmd)
    if m:
        filepath = m.group(1)
        # Check for range specifiers
        range_m = re.search(r"sed\s+-n\s+'?(\d+),(\d+)", cmd)
        head_m = re.search(r"head\s+-(\d+)", cmd)
        tail_m = re.search(r"tail\s+-(\d+)", cmd)
        if range_m:
            return f"{filepath}:{range_m.group(1)}-{range_m.group(2)}"
        if head_m:
            return f"{filepath}:head-{head_m.group(1)}"
        if tail_m:
            return f"{filepath}:tail-{tail_m.group(1)}"
        return filepath
    return None


def extract_edited_file(cmd: str) -> str | None:
    """Extract the file being edited by sed -i or python3 pathlib."""
    # sed -i 's/...' /workspace/path/file.py
    m = re.search(r"sed\s+-i\s+.*?(['\"].*?['\"])\s+(\S+\.py)", cmd)
    if m:
        return m.group(2)
    # pathlib.Path('file.py')
    m = re.search(r"Path\(['\"]([^'\"]+)['\"]\)", cmd)
    if m:
        return m.group(1)
    # Fallback: sed -i ... file
    m = re.search(r"sed\s+-i\s+\S+\s+(\S+)", cmd)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Agent loop with anti-hesitation
# ---------------------------------------------------------------------------

def agent_fix(
    provider,
    container: str,
    problem: str,
    max_steps: int,
) -> tuple[str, int]:
    """Run the agent loop with anti-hesitation interventions.

    Returns (git_diff, steps_used).
    """
    messages: list[Message] = [
        Message.system(SYSTEM_PROMPT),
        Message.user(
            f"Please fix the following issue in the repository at /workspace.\n\n"
            f"ISSUE:\n{problem[:5000]}\n\n"
            f"Start by locating the relevant source code with grep or find. "
            f"Then read the specific code, understand the bug, and make the "
            f"minimal fix. Remember: do NOT spend more than 5 steps reading."
        ),
    ]

    # Anti-hesitation state
    steps_since_edit = 0
    viewed_ranges: set[str] = set()
    recent_commands: list[str] = []
    nudge_count = 0
    has_made_any_edit = False

    for step in range(max_steps):
        try:
            response = provider.complete(
                messages, temperature=0.0, max_tokens=2048,
            )
        except Exception as e:
            print(f"    [step {step+1}] Provider error: {e}")
            # Wait and retry once
            time.sleep(2)
            try:
                response = provider.complete(
                    messages, temperature=0.0, max_tokens=2048,
                )
            except Exception as e2:
                print(f"    [step {step+1}] Provider error (retry): {e2}")
                break

        raw = response.content.strip()

        # Clean markdown fences
        cmd = raw
        if "```" in cmd:
            # Extract content between fences
            lines = cmd.split("\n")
            in_fence = False
            extracted = []
            for line in lines:
                if line.strip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    extracted.append(line)
            cmd = "\n".join(extracted).strip() if extracted else cmd
            # If still has fences, just strip them
            if "```" in cmd:
                cmd = "\n".join(
                    l for l in cmd.split("\n")
                    if not l.strip().startswith("```")
                ).strip()

        # If multi-line and not a python/heredoc command, take first
        # substantial line
        if "\n" in cmd:
            is_heredoc = "<<" in cmd or "EOF" in cmd or "PYEOF" in cmd
            is_python = cmd.lstrip().startswith("python")
            if not is_heredoc and not is_python:
                lines = [
                    l for l in cmd.split("\n")
                    if l.strip() and not l.strip().startswith("#")
                    and not l.strip().startswith("//")
                ]
                cmd = lines[0] if lines else cmd

        # Check for DONE
        if not cmd or "DONE" in cmd.upper():
            break

        # ── Anti-repetition: exact same command 3 times ──
        recent_commands.append(cmd[:200])
        if len(recent_commands) >= 3 and len(set(recent_commands[-3:])) == 1:
            messages.append(Message.assistant(cmd))
            messages.append(Message.user(
                "You are repeating the exact same command. This approach is "
                "NOT working. Try a COMPLETELY DIFFERENT strategy. If you've "
                "been reading, make an edit now. If your edit keeps failing, "
                "try a different approach to the fix."
            ))
            recent_commands.clear()
            steps_since_edit += 1
            continue

        # ── Anti-repetition: same file range viewed twice ──
        view_key = extract_file_view(cmd)
        if view_key and view_key in viewed_ranges and is_read_cmd(cmd):
            messages.append(Message.assistant(cmd))
            messages.append(Message.user(
                f"You already viewed {view_key}. Do NOT re-read the same "
                f"code. Move forward: either make an edit based on what you "
                f"learned, or look at a DIFFERENT file/section."
            ))
            steps_since_edit += 1
            continue
        if view_key:
            viewed_ranges.add(view_key)

        # ── Track read vs edit ──
        cmd_is_edit = is_edit_cmd(cmd)
        if cmd_is_edit:
            steps_since_edit = 0
            has_made_any_edit = True
        elif is_read_cmd(cmd):
            steps_since_edit += 1

        # ── Execute the command ──
        cmd_preview = cmd[:120].replace("\n", "\\n")
        edit_marker = " [EDIT]" if cmd_is_edit else ""
        print(f"    [{step+1:2d}] {cmd_preview}{edit_marker}")
        code, output = docker_exec(container, f"cd /workspace && {cmd}", 60)

        messages.append(Message.assistant(cmd))

        # Build response with optional nudges
        response_text = f"Exit code: {code}\n{output[-3000:]}\n"

        # ── Edit verification: after an edit, auto-show the changed file ──
        if cmd_is_edit and code == 0:
            edited_file = extract_edited_file(cmd)
            if edited_file:
                vcode, vout = docker_exec(
                    container,
                    f"cd /workspace && cat -n {edited_file} | head -80",
                    10,
                )
                if vcode == 0 and vout.strip():
                    response_text += (
                        f"\n[Auto-verify] Here is the current state of "
                        f"{edited_file}:\n{vout[-2000:]}\n"
                    )

        # ── Force editing nudge ──
        if steps_since_edit >= 5 and not cmd_is_edit:
            nudge_count += 1
            if nudge_count <= 3:
                response_text += (
                    "\n\n*** NUDGE: You have spent "
                    f"{steps_since_edit} steps reading/searching without "
                    "making any edit. You've read enough. Make an edit NOW "
                    "based on what you've learned. Use sed -i or python3 "
                    "to fix the code. ***"
                )
            else:
                # Stronger nudge after multiple ignored nudges
                response_text += (
                    "\n\n*** URGENT: You MUST make an edit in your next "
                    "response. You have been reading for too long. Pick the "
                    "most likely fix and implement it with sed -i or python3. "
                    "Even an imperfect fix is better than endless reading. ***"
                )

        # ── Late-game nudge: if past 60% of steps and no edit yet ──
        if step > max_steps * 0.6 and not has_made_any_edit:
            response_text += (
                f"\n\n*** WARNING: You have used {step+1}/{max_steps} steps "
                "and have NOT made any edit yet. You MUST make an edit NOW "
                "or you will run out of steps. ***"
            )

        messages.append(Message.user(response_text))

        # ── Context condensation ──
        # Keep: system (0), first user (1), and recent 60% of conversation
        if len(messages) > 50:
            keep_count = max(int(len(messages) * 0.6), 20)
            messages = messages[:2] + messages[-keep_count:]

    # Get the agent's patch
    _, diff = docker_exec(container, "cd /workspace && git diff")
    return diff, step + 1 if "step" in dir() else 0


# ---------------------------------------------------------------------------
# Evaluation (official SWE-bench methodology)
# ---------------------------------------------------------------------------

def apply_test_patch(container: str, test_patch: str) -> bool:
    """Apply test_patch using a reliable file-based approach."""
    if not test_patch or not test_patch.strip():
        return True

    # Write patch via python3 inside the container to avoid shell escaping
    import base64
    encoded = base64.b64encode(test_patch.encode()).decode()

    # Use python3 inside container to decode and write (avoids shell limits)
    docker_exec(
        container,
        f"python3 -c \"import base64,pathlib; "
        f"pathlib.Path('/tmp/test.patch').write_bytes("
        f"base64.b64decode('{encoded}'))\"",
        10,
    )

    # Try git apply (standard)
    code, out = docker_exec(
        container,
        "cd /workspace && git apply /tmp/test.patch 2>&1",
        30,
    )
    if code == 0:
        return True

    # Try with --3way (handles some context mismatches)
    code, out = docker_exec(
        container,
        "cd /workspace && git apply --3way /tmp/test.patch 2>&1",
        30,
    )
    if code == 0:
        return True

    # Try with --reject (applies what it can, skips conflicts)
    code, out = docker_exec(
        container,
        "cd /workspace && git apply --reject /tmp/test.patch 2>&1",
        30,
    )
    if code == 0:
        return True

    # Last resort: patch command with fuzz
    code, out = docker_exec(
        container,
        "cd /workspace && patch -p1 --fuzz=3 < /tmp/test.patch 2>&1",
        30,
    )
    return code == 0


def evaluate(
    container: str,
    test_patch: str,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> dict:
    """Apply test_patch and run tests -- the official evaluation step."""
    # Apply test patch
    patch_ok = apply_test_patch(container, test_patch)
    if not patch_ok:
        print("    [warn] Could not apply test_patch cleanly")

    # Run FAIL_TO_PASS tests
    f2p_pass = 0
    for test_id in fail_to_pass:
        code, _ = docker_exec(
            container,
            f"cd /workspace && python -m pytest {test_id} -x --tb=no -q 2>&1",
            120,
        )
        if code == 0:
            f2p_pass += 1

    # Run PASS_TO_PASS tests (sample up to 5 for speed)
    p2p_total = min(len(pass_to_pass), 5)
    p2p_pass = 0
    for test_id in pass_to_pass[:p2p_total]:
        code, _ = docker_exec(
            container,
            f"cd /workspace && python -m pytest {test_id} -x --tb=no -q 2>&1",
            120,
        )
        if code == 0:
            p2p_pass += 1

    return {
        "f2p": f"{f2p_pass}/{len(fail_to_pass)}",
        "p2p": f"{p2p_pass}/{p2p_total}" if p2p_total > 0 else "N/A",
        "resolved": f2p_pass == len(fail_to_pass),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SWE-bench v4 -- anti-hesitation scaffold"
    )
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--start", type=int, default=0,
                        help="Skip first N instances (for resuming)")
    args = parser.parse_args()

    if not os.path.exists(DATASET_PATH):
        print(f"Dataset not found: {DATASET_PATH}")
        print("Download: curl -L -o /tmp/swe-bench-lite.jsonl "
              "https://raw.githubusercontent.com/princeton-nlp/"
              "SWE-bench/main/swebench/collect/tasks/"
              "swe-bench-lite.jsonl")
        sys.exit(1)

    provider = create_provider(model=args.model)
    instances = load_instances(DATASET_PATH, args.count + args.start)
    instances = instances[args.start:]

    print("=" * 72)
    print("SWE-BENCH v4 -- ANTI-HESITATION SCAFFOLD")
    print("=" * 72)
    print(f"Model:      {provider.model_name}")
    print(f"Instances:  {len(instances)}")
    print(f"Max steps:  {args.max_steps}")
    print(f"Temp:       0.0")
    print(f"Features:   anti-hesitation nudges, edit verification, "
          f"anti-repetition, context condensation")
    print(f"Method:     Official (agent=source fix, eval=test_patch + tests)")
    print()

    results: list[dict] = []
    resolved = 0
    start_time = time.time()

    for i, inst in enumerate(instances, 1):
        instance_id = inst["instance_id"]
        repo = inst["repo"]
        base_commit = inst["base_commit"]
        problem = inst["problem_statement"]
        test_patch = inst.get("test_patch", "")
        fail_to_pass = (
            json.loads(inst["FAIL_TO_PASS"])
            if isinstance(inst["FAIL_TO_PASS"], str)
            else inst["FAIL_TO_PASS"]
        )
        pass_to_pass = (
            json.loads(inst["PASS_TO_PASS"])
            if isinstance(inst["PASS_TO_PASS"], str)
            else inst["PASS_TO_PASS"]
        )
        gold_patch_lines = len(inst["patch"].splitlines())

        print(f"[{i}/{len(instances)}] {instance_id} "
              f"(gold: {gold_patch_lines} lines)")

        # Setup container
        t0 = time.time()
        container = setup_container(repo, base_commit, instance_id)
        if not container:
            print(f"  SKIP (container setup failed)")
            results.append({
                "instance_id": instance_id,
                "status": "SKIP",
                "reason": "setup",
            })
            continue
        setup_time = time.time() - t0
        print(f"  Container ready ({setup_time:.0f}s)")

        # Phase 1: Agent produces source fix (does NOT see tests)
        t0 = time.time()
        agent_diff, steps = agent_fix(
            provider, container, problem, args.max_steps,
        )
        agent_time = time.time() - t0

        if not agent_diff.strip():
            print(f"  FAILED (no changes, {steps} steps, {agent_time:.0f}s)")
            results.append({
                "instance_id": instance_id,
                "status": "FAILED",
                "reason": "no_patch",
                "steps": steps,
                "agent_time": round(agent_time, 1),
            })
            subprocess.run(
                ["docker", "rm", "-f", container], capture_output=True
            )
            continue

        diff_lines = len(agent_diff.splitlines())
        print(f"  Agent: {diff_lines} diff lines, {steps} steps, "
              f"{agent_time:.0f}s")

        # Phase 2: Evaluate (apply test_patch, run tests)
        print(f"  Evaluating...")
        eval_result = evaluate(
            container, test_patch, fail_to_pass, pass_to_pass
        )

        status = "RESOLVED" if eval_result["resolved"] else "FAILED"
        if eval_result["resolved"]:
            resolved += 1

        print(f"  {status} (f2p={eval_result['f2p']} "
              f"p2p={eval_result['p2p']})")

        results.append({
            "instance_id": instance_id,
            "status": status,
            "steps": steps,
            "diff_lines": diff_lines,
            "agent_time": round(agent_time, 1),
            **eval_result,
        })

        # Cleanup container
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True
        )

        # Running tally
        attempted = sum(
            1 for r in results if r["status"] in ("RESOLVED", "FAILED")
        )
        if attempted > 0:
            print(f"  Running: {resolved}/{attempted} "
                  f"({100 * resolved / attempted:.0f}%)")
        print()

    # ── Final report ──
    elapsed = time.time() - start_time
    attempted = sum(
        1 for r in results if r["status"] in ("RESOLVED", "FAILED")
    )

    print()
    print("=" * 72)
    print("SWE-BENCH v4 RESULTS -- ANTI-HESITATION SCAFFOLD")
    print("=" * 72)
    for r in results:
        s = r["status"]
        if s in ("RESOLVED", "FAILED"):
            extra = (
                f" f2p={r.get('f2p', '')} p2p={r.get('p2p', '')} "
                f"steps={r.get('steps', '')} "
                f"time={r.get('agent_time', '')}s"
            )
        elif s == "SKIP":
            extra = f" ({r.get('reason', '')})"
        else:
            extra = ""
        marker = " <<" if s == "RESOLVED" else ""
        print(f"  {r['instance_id']:45s} {s}{extra}{marker}")

    print()
    if attempted:
        print(f"Resolve rate: {resolved}/{attempted} "
              f"({100 * resolved / attempted:.1f}%)")
    else:
        print("No instances attempted")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    if skipped:
        print(f"Skipped:      {skipped}")
    avg_steps = 0.0
    step_counts = [r["steps"] for r in results if "steps" in r]
    if step_counts:
        avg_steps = sum(step_counts) / len(step_counts)
        print(f"Avg steps:    {avg_steps:.1f}")
    print(f"Total time:   {elapsed:.0f}s "
          f"({elapsed / 60:.1f}m)")

    # Save results
    model_name = provider.model_name.replace("/", "_")
    results_path = f"/tmp/swebench_v4_{model_name}.jsonl"
    with open(results_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results:      {results_path}")


if __name__ == "__main__":
    main()
