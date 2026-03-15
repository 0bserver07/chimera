#!/usr/bin/env python3
"""ThinkTool + AskUserTool: agent reasons then asks for clarification.

Shows how an agent can use internal reasoning (think) and pause to ask
the user a question (ask_user with a callback).

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/think_and_ask.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


def main():
    provider = chimera.create_provider(model=os.environ.get("ANTHROPIC_MODEL", "glm-5"))

    # Simulate user responses
    responses = iter(["Python", "beginner", "build a web app", "yes", "sure"])

    def fake_user(question, choices=None):
        answer = next(responses)
        print(f"  [Agent asks] {question}")
        print(f"  [User says]  {answer}")
        return answer

    agent = chimera.Agent(
        provider=provider,
        tools=[chimera.ThinkTool(), chimera.AskUserTool(callback=fake_user)],
        loop=chimera.ReAct(max_steps=8),
        prompt=chimera.Prompt.from_string(
            "You are a helpful tutor. Use the think tool to reason about "
            "what you need to know before helping. Use ask_user to ask the "
            "user questions. Be concise."
        ),
    )

    print("=== Agent with Think + AskUser ===\n")
    result = agent.run(
        "Help me learn to code. Figure out what I need first.",
        env=None,
    )
    print(f"\nAgent output:\n{result.output}")
    print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f}]")


if __name__ == "__main__":
    main()
