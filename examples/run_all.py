#!/usr/bin/env python3
"""Run all Chimera examples interactively.

Usage:
    # Set up credentials first:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"

    # Then run:
    python examples/run_all.py          # interactive menu
    python examples/run_all.py 1        # run example #1 directly
    python examples/run_all.py all      # run all examples
"""
from __future__ import annotations

import os
import subprocess
import sys

EXAMPLES = [
    ("quickstart_provider.py",   "Provider basics — text, tool use, multi-turn"),
    ("agent_with_tools.py",      "Agent with 13 tools — creates and runs code"),
    ("composition_pipeline.py",  "Pipeline — chain coder → reviewer agents"),
    ("think_and_ask.py",         "ThinkTool + AskUserTool — reasoning + user interaction"),
    ("wire_monitoring.py",       "Wire protocol — real-time agent monitoring"),
    ("dmail_context_rewind.py",  "D-Mail — agent rewinds its own context"),
    ("flow_skills.py",           "Flow Skills — Mermaid flowchart decision tree"),
    ("quickstart_synthesize.py", "Synthesis — generate code from test specs"),
]


def check_env():
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not token:
        print("ERROR: No API credentials found.")
        print()
        print("Set up your environment:")
        print('  export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"')
        print('  export ANTHROPIC_AUTH_TOKEN="your-token"')
        print('  export ANTHROPIC_MODEL="glm-5"')
        return False
    model = os.environ.get("ANTHROPIC_MODEL", "?")
    base = os.environ.get("ANTHROPIC_BASE_URL", "(default)")
    print(f"Model: {model} | API: {base} | Key: ***{token[-4:]}")
    print()
    return True


def run_example(idx: int):
    name, desc = EXAMPLES[idx]
    path = os.path.join(os.path.dirname(__file__), name)
    print(f"{'='*60}")
    print(f"  Example {idx + 1}: {desc}")
    print(f"  File: {name}")
    print(f"{'='*60}")
    print()
    result = subprocess.run([sys.executable, path], cwd=os.path.dirname(__file__))
    print()
    return result.returncode


def main():
    if not check_env():
        sys.exit(1)

    args = sys.argv[1:]

    if args and args[0] == "all":
        for i in range(len(EXAMPLES)):
            run_example(i)
            if i < len(EXAMPLES) - 1:
                print("─" * 60)
                print()
        return

    if args and args[0].isdigit():
        idx = int(args[0]) - 1
        if 0 <= idx < len(EXAMPLES):
            run_example(idx)
        else:
            print(f"Invalid example number. Choose 1-{len(EXAMPLES)}.")
        return

    # Interactive menu
    print("Chimera Examples")
    print("─" * 40)
    for i, (name, desc) in enumerate(EXAMPLES, 1):
        print(f"  {i}. {desc}")
    print()
    print(f"  Enter 1-{len(EXAMPLES)} to run, or 'all' to run everything.")
    print()

    try:
        choice = input("Which example? ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice == "all":
        for i in range(len(EXAMPLES)):
            run_example(i)
            print()
        return

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(EXAMPLES):
            run_example(idx)
        else:
            print(f"Pick 1-{len(EXAMPLES)}.")
    else:
        print("Invalid choice.")


if __name__ == "__main__":
    main()
