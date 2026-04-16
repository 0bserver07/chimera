#!/usr/bin/env python3
"""Session persistence: save and resume a conversation across runs.

Demonstrates using FileStorage to persist a multi-turn session to disk,
then resuming it later with full conversation history intact.

Usage:
    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"
    python examples/session_persistence.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera
from chimera.sessions.storage.file import FileStorage


def main():
    try:
        provider = chimera.create_provider()
    except ValueError as _e:
        import sys
        print(f"Setup error: {_e}", file=sys.stderr)
        print("Set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL before running.", file=sys.stderr)
        sys.exit(1)

    agent = chimera.Agent(
        provider=provider,
        loop=chimera.ReAct(max_steps=5),
        prompt=chimera.Prompt.from_string(
            "You are a helpful assistant. Remember details the user tells you."
        ),
    )

    with tempfile.TemporaryDirectory(prefix="chimera-sessions-") as session_dir:
        storage = FileStorage(session_dir)

        # --- Turn 1: create a session and tell it something ---
        print("=== Session Persistence ===\n")
        session = chimera.Session(agent=agent, env=None, storage=storage)
        session_id = session.session_id
        print(f"Session ID: {session_id}")

        print("\n--- Turn 1: introducing ourselves ---")
        result1 = chimera.drain_steps(session.iter_chat(
            "My name is Bob and my favorite language is Rust."
        ))
        print(f"Agent: {result1.output[:200]}")
        print(f"[cost: ${result1.cost:.4f}]")

        # Save the session
        session.save()
        print(f"\nSession saved to {session_dir}")

        # Verify it was saved
        saved_sessions = storage.list_sessions()
        print(f"Stored sessions: {saved_sessions}")

        # --- Turn 2: resume the session and ask about earlier context ---
        print("\n--- Turn 2: resuming and asking a question ---")
        session2 = chimera.Session.resume(
            session_id, agent=agent, storage=storage,
        )

        # The resumed session should have the conversation history
        print(f"Resumed session has {len(session2.messages)} messages")

        result2 = chimera.drain_steps(session2.iter_chat(
            "What is my name and favorite language?"
        ))
        print(f"Agent: {result2.output[:200]}")
        print(f"[cost: ${result2.cost:.4f}]")

        # Save again with the new turn
        session2.save()

        print(f"\nTotal messages after 2 turns: {len(session2.messages)}")
        print("Session persistence demo complete.")


if __name__ == "__main__":
    main()
