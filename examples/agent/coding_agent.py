#!/usr/bin/env python3
"""A real coding agent — read code, write code, run tests, fix bugs.

Give it a task and a directory. It reads code, writes code, runs tests,
fixes bugs. Works with any Anthropic-compatible API (Claude, GLM-5, etc).

Usage:
    # One-shot task:
    source .env
    python examples/coding_agent.py "Create a REST API with Flask" --workdir /tmp/myproject

    # Interactive REPL:
    python examples/coding_agent.py --workdir . --interactive

    # Review current directory:
    python examples/coding_agent.py "Review the code and list any issues" --workdir .

    # Fix a bug:
    python examples/coding_agent.py "Fix the failing test in test_parser.py" --workdir .

    # Generate tests:
    python examples/coding_agent.py "Write unit tests for utils.py" --workdir .
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import chimera
from chimera.wire.types import StatusUpdate, StepBegin

SYSTEM_PROMPT = """\
You are an expert coding agent. You can read files, write files, edit files, \
run shell commands, search code, run tests, and use git.

Guidelines:
- Read existing code before modifying it
- Run tests after making changes
- Be concise in explanations, thorough in code
- Use the think tool to plan complex tasks before acting
- Use the todo tool to track multi-step work
"""


def build_agent(workdir: str, verbose: bool = False):
    """Build a fully-equipped coding agent."""
    try:
        provider = chimera.create_provider(
            model=os.environ.get("ANTHROPIC_MODEL"),
        )
    except ValueError as e:
        print(f"Setup error: {e}\n", file=sys.stderr)
        print("Set one of these before running:", file=sys.stderr)
        print("  export ANTHROPIC_API_KEY='sk-ant-...'", file=sys.stderr)
        print("  # or for a compatible endpoint (e.g. GLM-5 via z.ai):", file=sys.stderr)
        print("  export ANTHROPIC_BASE_URL='https://api.z.ai/api/anthropic'", file=sys.stderr)
        print("  export ANTHROPIC_AUTH_TOKEN='your-token'", file=sys.stderr)
        print("  export ANTHROPIC_MODEL='glm-5'", file=sys.stderr)
        sys.exit(1)

    # Wire for real-time monitoring
    wire = chimera.Wire()
    if verbose:
        def on_msg(msg):
            if isinstance(msg, StepBegin):
                print(f"  ⟳ step {msg.step}...", flush=True)
            elif isinstance(msg, StatusUpdate) and msg.metadata:
                tool = msg.metadata.get("tool", "")
                if tool:
                    print(f"  → {tool}", flush=True)
        wire.on_message(on_msg)

    config = chimera.LoopConfig(wire=wire)

    # DMailTool for context management on long tasks
    dmail = chimera.DMailTool()
    tools = list(chimera.AGENT_TOOLS) + [dmail]

    # Load project context if available
    system = SYSTEM_PROMPT
    try:
        from chimera.config.loader import ProjectConfig
        project = ProjectConfig.from_directory(workdir)
        if project and project.rules_text:
            system += "\n\n# Project Rules\n" + project.rules_text
    except Exception:
        pass

    env = chimera.LocalEnvironment(workdir=workdir)
    env.setup()

    agent = chimera.Agent(
        provider=provider,
        tools=tools,
        loop=chimera.ReAct(max_steps=25, config=config),
        prompt=chimera.Prompt.from_string(system),
    )

    return agent, env, provider


def run_task(task: str, workdir: str, verbose: bool = False):
    """Run a single coding task."""
    agent, env, provider = build_agent(workdir, verbose)

    print(f"Model:   {provider.model_name}")
    print(f"Workdir: {workdir}")
    print(f"Task:    {task}")
    print()

    result = agent.run(task, env=env)

    print()
    print(result.output)
    print()
    print(f"[steps: {result.steps} | cost: ${result.cost:.4f} | {'ok' if result.success else 'FAILED'}]")

    env.cleanup()
    return result


def run_interactive(workdir: str, verbose: bool = False):
    """Interactive REPL — like chimera code."""
    agent, env, provider = build_agent(workdir, verbose)
    session = chimera.Session(agent=agent, env=env)

    print(f"Chimera coding agent | model: {provider.model_name}")
    print(f"Workdir: {workdir}")
    print("Commands: /help /tools /cost /exit")
    print()

    total_cost = 0.0

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("Bye!")
            break
        elif user_input == "/help":
            print("  Just type a task. The agent has 14 tools (read, write, edit, bash, etc).")
            print("  /tools — list tools | /cost — show cost | /exit — quit")
            continue
        elif user_input == "/tools":
            for t in agent.tools:
                print(f"  {t.name}: {t.description[:60]}")
            continue
        elif user_input == "/cost":
            print(f"  Total: ${total_cost:.4f}")
            continue

        try:
            result = chimera.drain_steps(session.iter_chat(user_input))
            total_cost += result.cost
            print(f"\n[cost: ${result.cost:.4f} | steps: {result.steps}]\n")
        except KeyboardInterrupt:
            print("\n  (interrupted)")
        except Exception as exc:
            print(f"\n  Error: {exc}\n")

    print(f"\nTotal cost: ${total_cost:.4f}")
    env.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="Chimera coding agent — give it a task, it writes code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s "Create a Flask REST API" --workdir /tmp/project
  %(prog)s "Fix the bug in parser.py" --workdir .
  %(prog)s "Write tests for utils.py" --workdir .
  %(prog)s --interactive --workdir .
""",
    )
    parser.add_argument("task", nargs="?", help="Task for the agent (omit for interactive mode)")
    parser.add_argument("--workdir", default=".", help="Working directory (default: current)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive REPL mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show step-by-step progress")
    args = parser.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)

    if args.interactive or args.task is None:
        run_interactive(workdir, args.verbose)
    else:
        run_task(args.task, workdir, args.verbose)


if __name__ == "__main__":
    main()
