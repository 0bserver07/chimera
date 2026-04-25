#!/usr/bin/env python3
"""Build a full-featured coding agent in ~20 lines via `CodingAgent.from_preset()`.

This is the canonical "how do I build my own coding agent" example. It uses
the `claude_code` preset key — 24 tools, permissions, hooks, transcripts,
compaction, streaming — wired together as one `CodingAgent`.

Usage:
    export ANTHROPIC_API_KEY='sk-ant-...'
    # or any compatible endpoint:
    #   export ANTHROPIC_BASE_URL='https://api.z.ai/api/anthropic'
    #   export ANTHROPIC_AUTH_TOKEN='your-token'
    #   export ANTHROPIC_MODEL='glm-5'

    python examples/agent/build_full_preset_agent.py "Fix the bug in auth.py"
    python examples/agent/build_full_preset_agent.py --workdir /tmp/myproject "Add tests"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from chimera.assembly.coding_agent import CodingAgent
from chimera.core.loop_events import LoopEventType


async def run(task: str, workdir: str, model: str) -> None:
    # One line: a fully-assembled coding agent from the full-featured preset.
    agent = CodingAgent.from_preset("claude_code", model=model, project_dir=workdir)

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
    parser = argparse.ArgumentParser(description="Build a full-featured coding agent via CodingAgent.from_preset('claude_code')")
    parser.add_argument("task", help="Task for the agent")
    parser.add_argument("--workdir", default=".", help="Working directory")
    parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.task, os.path.abspath(args.workdir), args.model))
    except ValueError as e:
        print(f"Setup error: {e}", file=sys.stderr)
        print("\nSet one of: ANTHROPIC_API_KEY, or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[interrupted]")


if __name__ == "__main__":
    main()
