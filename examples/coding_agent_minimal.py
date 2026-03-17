#!/usr/bin/env python3
"""Minimal coding agent with REPL -- under 80 lines of logic.

A stripped-down coding agent that demonstrates the core Chimera loop:
provider + tools + REPL. No argparse, no wire, no sessions -- just the
essentials.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"

    python examples/coding_agent_minimal.py              # current directory
    python examples/coding_agent_minimal.py /tmp/project  # specific directory
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


def main():
    provider = chimera.create_provider()
    workdir = sys.argv[1] if len(sys.argv) > 1 else "."
    workdir = os.path.abspath(workdir)
    os.makedirs(workdir, exist_ok=True)

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    agent = chimera.Agent(
        provider=provider,
        tools=list(chimera.AGENT_TOOLS),
        loop=chimera.ReAct(max_steps=20),
    )

    print(f"Chimera | {provider.model_name} | {workdir} | /help")
    total = 0.0

    while True:
        try:
            inp = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not inp:
            continue
        if inp == "/exit":
            break
        if inp == "/help":
            print("  Type a task. Commands: /tools /cost /exit")
            continue
        if inp == "/tools":
            for t in agent.tools:
                print(f"  {t.name}")
            continue
        if inp == "/cost":
            print(f"  ${total:.4f}")
            continue
        try:
            result = agent.run(inp, env=env)
            total += result.cost
            print(f"\n[{result.steps} steps, ${result.cost:.4f}]")
        except KeyboardInterrupt:
            print("\n  (interrupted)")
        except Exception as e:
            print(f"\n  Error: {e}")

    print(f"\nTotal: ${total:.4f}")
    env.cleanup()


if __name__ == "__main__":
    main()
