#!/usr/bin/env python3
"""D-Mail: agent rewinds its own context to save tokens.

The agent creates a checkpoint, does exploratory work, then sends a
D-Mail to rewind back with only the useful findings. This is context
compaction disguised as time travel.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/dmail_context_rewind.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


def main():
    provider = chimera.create_provider(model=os.environ.get("ANTHROPIC_MODEL", "glm-5"))

    with tempfile.TemporaryDirectory(prefix="chimera-dmail-") as tmpdir:
        env = chimera.LocalEnvironment(workdir=tmpdir)
        env.setup()

        # Give the agent file tools + DMailTool
        dmail = chimera.DMailTool()
        tools = list(chimera.AGENT_TOOLS) + [dmail]

        agent = chimera.Agent(
            provider=provider,
            tools=tools,
            loop=chimera.ReAct(max_steps=10),
            prompt=chimera.Prompt.from_string(
                "You are a research agent. You have a 'dmail' tool that lets you "
                "manage context efficiently. Use action='checkpoint' to save your "
                "position, then action='send' with a summary to rewind when you've "
                "gathered enough information. This keeps your context clean."
            ),
        )

        # Write some files for the agent to explore
        env.write_file("config.json", '{"database": "postgres", "port": 5432, "debug": true}')
        env.write_file("app.py", 'from flask import Flask\napp = Flask(__name__)\n\n@app.route("/")\ndef index():\n    return "Hello"\n')
        env.write_file("requirements.txt", "flask==3.0.0\npsycopg2-binary==2.9.9\ngunicorn==21.2.0\n")

        print("=== D-Mail Context Rewind ===\n")
        result = agent.run(
            "Create a checkpoint, then explore the project files to understand "
            "what this project is. After reading the files, send a D-Mail back to "
            "your checkpoint summarizing what you found. Then write a brief "
            "PROJECT_SUMMARY.md file based on your D-Mail knowledge.",
            env=env,
        )

        print(f"\nAgent: {result.output[:300]}")
        print(f"\n[steps: {result.steps} | cost: ${result.cost:.4f}]")
        print(f"[checkpoints created: {dmail.checkpoint_count}]")

        # Show what was created
        summary_path = os.path.join(tmpdir, "PROJECT_SUMMARY.md")
        if os.path.exists(summary_path):
            print(f"\n--- PROJECT_SUMMARY.md ---")
            print(open(summary_path).read())

        env.cleanup()


if __name__ == "__main__":
    main()
