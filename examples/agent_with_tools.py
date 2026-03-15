#!/usr/bin/env python3
"""Agent with tools: read files, write code, run bash, think, and plan.

This shows Chimera's core value: give an LLM tools and let it work.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/agent_with_tools.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


def main():
    provider = chimera.create_provider(model=os.environ.get("ANTHROPIC_MODEL", "glm-5"))

    # Create a temp workspace so the agent can create files safely
    with tempfile.TemporaryDirectory(prefix="chimera-demo-") as tmpdir:
        env = chimera.LocalEnvironment(workdir=tmpdir)
        env.setup()

        # Give the agent the full AGENT_TOOLS set (13 tools)
        agent = chimera.Agent(
            provider=provider,
            tools=list(chimera.AGENT_TOOLS),
            loop=chimera.ReAct(max_steps=10),
        )

        print("=== Task: Create a Python script and run it ===\n")
        result = agent.run(
            "Create a Python file called fibonacci.py that prints the first 10 "
            "Fibonacci numbers. Then run it with bash and tell me the output.",
            env=env,
        )
        print(f"\nAgent output:\n{result.output}")
        print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f} | success: {result.success}]")

        # Show what the agent created
        print(f"\n=== Files in workspace ===")
        for f in os.listdir(tmpdir):
            filepath = os.path.join(tmpdir, f)
            if os.path.isfile(filepath):
                print(f"\n--- {f} ---")
                print(open(filepath).read())

        env.cleanup()


if __name__ == "__main__":
    main()
