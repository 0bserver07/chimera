#!/usr/bin/env python3
"""Wire protocol: monitor agent activity in real time.

Shows how Wire lets you observe what the agent is doing — which step
it's on, what tools it's calling, how much it's costing.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/wire_monitoring.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.wire.types import StepBegin, StepEnd, StatusUpdate


def main():
    try:
        provider = chimera.create_provider(model=os.environ.get("ANTHROPIC_MODEL"))
    except ValueError as _e:
        import sys
        print(f"Setup error: {_e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL before running.", file=sys.stderr)
        sys.exit(1)

    # Set up Wire to monitor agent activity
    wire = chimera.Wire()

    def on_message(msg):
        if isinstance(msg, StepBegin):
            print(f"  [wire] Step {msg.step} starting...")
        elif isinstance(msg, StepEnd):
            tool = msg.tool_name or "none"
            print(f"  [wire] Step {msg.step} done (tool: {tool})")
        elif isinstance(msg, StatusUpdate):
            tool = msg.metadata.get("tool", "?") if msg.metadata else "?"
            print(f"  [wire] Tool executed: {tool}")

    wire.on_message(on_message)

    # Connect Wire through LoopConfig
    config = chimera.LoopConfig(wire=wire)

    with tempfile.TemporaryDirectory(prefix="chimera-wire-") as tmpdir:
        env = chimera.LocalEnvironment(workdir=tmpdir)
        env.setup()

        agent = chimera.Agent(
            provider=provider,
            tools=list(chimera.AGENT_TOOLS),
            loop=chimera.ReAct(max_steps=10, config=config),
        )

        print("=== Wire Monitoring ===\n")
        result = agent.run(
            "Create a file called hello.py that prints 'Hello from Chimera!', "
            "then run it.",
            env=env,
        )

        print(f"\nAgent: {result.output[:200]}")
        print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f}]")
        env.cleanup()


if __name__ == "__main__":
    main()
