#!/usr/bin/env python3
"""Recreate Codex CLI in ~20 lines using `CodingAgent.from_preset('codex')`.

The `codex` preset: 24 tools + permissions + transcripts (no hooks, matching
the Codex CLI design). Same library, different preset.

Usage:
    export OPENAI_API_KEY='sk-...'
    export OPENAI_MODEL='gpt-4o'

    python examples/build_codex_clone.py "Fix the bug in auth.py"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from chimera.assembly.coding_agent import CodingAgent
from chimera.core.loop_events import LoopEventType


async def run(task: str, workdir: str, model: str) -> None:
    agent = CodingAgent.from_preset("codex", model=model, project_dir=workdir)

    print(f"Agent: {agent.provider.model_name} | {len(agent.tools)} tools | {workdir}")
    print(f"Task: {task}\n")

    async for event in agent.run(task):
        if event.type == LoopEventType.assistant:
            content = getattr(event.data, "content", "")
            if content.strip():
                print(content)
        elif event.type == LoopEventType.tool_result:
            tc, result = (
                event.data if isinstance(event.data, tuple) else (None, event.data)
            )
            name = getattr(tc, "name", "?") if tc else "?"
            output = getattr(result, "output", str(result))
            ok = "+" if getattr(result, "success", True) else "!"
            print(f"[{ok} {name}] {output[:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recreate Codex CLI using CodingAgent.from_preset('codex')"
    )
    parser.add_argument("task", help="Task for the agent")
    parser.add_argument("--workdir", default=".", help="Working directory")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o"),
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.task, os.path.abspath(args.workdir), args.model))
    except ValueError as e:
        print(f"Setup error: {e}", file=sys.stderr)
        print("\nSet OPENAI_API_KEY + OPENAI_MODEL, or any compatible provider env vars.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[interrupted]")


if __name__ == "__main__":
    main()
