#!/usr/bin/env python3
"""Pipeline composition: chain two agents together.

Agent 1 generates code, Agent 2 reviews it.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/composition_pipeline.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


def main():
    model = os.environ.get("ANTHROPIC_MODEL", "glm-5")
    provider = chimera.create_provider(model=model)

    coder = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=3),
        prompt=chimera.Prompt.from_string(
            "You are a Python developer. Write clean, concise code. "
            "Output ONLY the code, no explanation."
        ),
        name="coder",
    )

    reviewer = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=3),
        prompt=chimera.Prompt.from_string(
            "You are a code reviewer. Review the code from the previous step. "
            "List any bugs, style issues, or improvements. Be brief."
        ),
        name="reviewer",
    )

    pipe = chimera.Pipeline([coder, reviewer])

    print("=== Pipeline: Code → Review ===\n")
    result = pipe.run(
        "Write a Python function that checks if a string is a palindrome, "
        "handling spaces and capitalization.",
        env=None,
    )
    print(f"Final output:\n{result.output}")
    print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f}]")


if __name__ == "__main__":
    main()
