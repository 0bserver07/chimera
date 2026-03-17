#!/usr/bin/env python3
"""Supervisor delegation: coordinator dispatches to researcher + coder workers.

The Supervisor pattern gives the coordinator agent delegate tools so it can
dispatch sub-tasks to specialist workers and synthesize their results.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/supervisor_delegation.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.composition import Supervisor


def main():
    model = os.environ.get("ANTHROPIC_MODEL", "glm-5")
    provider = chimera.create_provider(model=model)

    # Worker 1: researcher -- analyzes requirements
    researcher = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=3),
        prompt=chimera.Prompt.from_string(
            "You are a software researcher. When given a topic, provide a "
            "concise analysis of requirements, trade-offs, and recommended "
            "approach. Be brief and actionable."
        ),
        name="researcher",
    )

    # Worker 2: coder -- writes implementation
    coder = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=3),
        prompt=chimera.Prompt.from_string(
            "You are a Python developer. When given a task with research "
            "context, write clean, production-ready code. Output ONLY the "
            "code with brief comments."
        ),
        name="coder",
    )

    # Coordinator -- delegates to workers and synthesizes
    coordinator = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=6),
        prompt=chimera.Prompt.from_string(
            "You are a project coordinator. You have two tools:\n"
            "- researcher: delegates research tasks\n"
            "- coder: delegates coding tasks\n\n"
            "First delegate research to understand the problem, then delegate "
            "coding with the research findings. Finally, summarize the result."
        ),
        name="coordinator",
    )

    # Build the supervisor -- workers must be a dict[str, Agent]
    sup = Supervisor(
        coordinator=coordinator,
        workers={"researcher": researcher, "coder": coder},
    )

    print("=== Supervisor Delegation ===\n")
    task = (
        "Research the best approach for implementing a thread-safe LRU cache "
        "in Python, then write the implementation."
    )
    print(f"Task: {task}\n")

    result = sup.run(task, env=None)

    print(f"Output:\n{result.output}")
    print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f} | success: {result.success}]")


if __name__ == "__main__":
    main()
