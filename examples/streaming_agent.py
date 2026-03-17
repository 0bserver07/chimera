#!/usr/bin/env python3
"""Streaming agent: real-time step output with ConsoleStreamHandler.

Shows how to attach a stream handler to see each step as it happens,
including tool calls and their results.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/streaming_agent.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.streaming.handlers import ConsoleStreamHandler


def main():
    provider = chimera.create_provider()

    # Attach a ConsoleStreamHandler via LoopConfig
    config = chimera.LoopConfig(handler=ConsoleStreamHandler())

    agent = chimera.Agent(
        provider=provider,
        tools=[chimera.ThinkTool()],
        loop=chimera.ReAct(max_steps=10, config=config),
        prompt=chimera.Prompt.from_string(
            "You are a helpful assistant. Use the think tool to reason "
            "step-by-step before answering."
        ),
    )

    print("=== Streaming Agent ===\n")
    print("(Console stream handler will show each step as it happens)\n")

    result = agent.run(
        "What are three interesting facts about the Fibonacci sequence? "
        "Think through each one before responding.",
        env=None,
    )

    print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f} | success: {result.success}]")


if __name__ == "__main__":
    main()
