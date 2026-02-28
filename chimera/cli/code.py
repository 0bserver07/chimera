"""Interactive coding agent REPL."""
from __future__ import annotations

import os
import sys

from chimera import __version__
from chimera.core.agent import Agent
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.core.prompt import Prompt
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider
from chimera.sessions.session import Session
from chimera.streaming.handlers import ConsoleStreamHandler

_DEFAULT_SYSTEM = """\
You are a coding assistant with access to tools for reading, writing, \
editing files, running commands, searching code, and running tests. \
Help the user with their coding tasks. Be concise and direct."""


def run_code(args) -> int:
    """Run the interactive coding REPL."""
    workdir = os.path.abspath(args.workdir)
    provider = create_provider(model=args.model)
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # Auto-discover project context
    system = _DEFAULT_SYSTEM
    try:
        from chimera.config.loader import ProjectConfig
        project = ProjectConfig.from_directory(workdir)
        if project and project.rules_text:
            system += "\n\n# Project Context\n" + project.rules_text
    except Exception:
        pass  # Config discovery is best-effort

    handler = ConsoleStreamHandler()
    loop = ReAct(
        max_steps=args.max_steps,
        config=LoopConfig(handler=handler),
    )

    prompt = Prompt.from_string(system)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS), loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env)

    total_cost = 0.0
    print(f"chimera code v{__version__} | model: {args.model} | workdir: {workdir}")
    print("Type /exit to quit.\n")

    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        cmd = user_input.strip()
        if cmd in ("/exit", "/quit"):
            break
        if not cmd:
            continue

        try:
            result = drain_steps(session.iter_chat(user_input))
            total_cost += result.cost
            print(f"\n  [cost: ${result.cost:.4f} | steps: {result.steps}]")
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)

    print(f"\nSession total: ${total_cost:.4f}")
    env.cleanup()
    return 0
