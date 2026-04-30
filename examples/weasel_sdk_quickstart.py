#!/usr/bin/env python3
"""Quickstart for the embeddable ``chimera weasel`` SDK.

Demonstrates the four canonical entrypoints exposed by
:class:`chimera.weasel.sdk.Agent`:

1. ``run``    — sync one-shot.
2. ``arun``   — async one-shot.
3. ``stream`` — sync iterator of :class:`Event`.
4. ``astream``— async iterator of :class:`Event`.
5. ``chat``   — multi-turn convenience that reuses one :class:`Session`.

Set up a provider before running, e.g.::

    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token"
    export ANTHROPIC_MODEL="glm-5"

    python examples/weasel_sdk_quickstart.py

The SDK is intentionally provider-agnostic — pass an explicit
``provider=`` to :class:`Agent` to swap GLM-5 for OpenAI / Ollama / a
local mock.
"""
from __future__ import annotations

import asyncio
import sys

from chimera.weasel.sdk import Agent, Event, EventType


PROMPT = "List three single-word file names a typical Python repo has."


def demo_run() -> None:
    """One-shot sync — the simplest possible call."""
    agent = Agent()
    result = agent.run(PROMPT)
    print(f"[run] success={result.success} steps={result.steps}")
    print(result.output)


async def demo_arun() -> None:
    """One-shot async — same as :meth:`Agent.run` but awaitable."""
    agent = Agent()
    result = await agent.arun(PROMPT)
    print(f"[arun] success={result.success} steps={result.steps}")
    print(result.output)


def demo_stream() -> None:
    """Sync stream — print each event as it arrives."""
    agent = Agent()
    for event in agent.stream(PROMPT):
        _print_event(event)


async def demo_astream() -> None:
    """Async stream — same shape as :meth:`Agent.stream` but awaitable."""
    agent = Agent()
    async for event in agent.astream(PROMPT):
        _print_event(event)


def demo_chat() -> None:
    """Multi-turn — successive turns share one internal Session."""
    agent = Agent()
    print("> What's in this repo?")
    print(agent.chat("What's in this repo?"))
    print("\n> Now name the most important file.")
    print(agent.chat("Now name the most important file."))


def _print_event(event: Event) -> None:
    """Pretty-print an :class:`Event` for the demo."""
    if event.type == EventType.TEXT:
        sys.stdout.write(event.text)
        sys.stdout.flush()
    elif event.type == EventType.TOOL_CALL and event.tool_call is not None:
        print(f"\n[tool] {event.tool_call.name}({event.tool_call.arguments})")
    elif event.type == EventType.TOOL_RESULT and event.tool_result is not None:
        snippet = (event.tool_result.output or "")[:120].replace("\n", " ")
        print(f"[result] {snippet}")
    elif event.type == EventType.STEP:
        print(f"\n--- step {event.step} ---")
    elif event.type == EventType.DONE and event.result is not None:
        print(f"\n[done] cost=${event.result.cost:.4f} steps={event.result.steps}")


def main() -> int:
    """Run the five demos in sequence.

    Returns:
        Process exit code (always ``0`` — the demos swallow provider
        errors so missing creds don't crash the example).
    """
    demos: list[tuple[str, object]] = [
        ("run", demo_run),
        ("arun", demo_arun),
        ("stream", demo_stream),
        ("astream", demo_astream),
        ("chat", demo_chat),
    ]
    for name, fn in demos:
        print(f"\n=== weasel SDK demo: {name} ===")
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())  # type: ignore[arg-type]
            else:
                fn()  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 — demo, never crash
            print(f"[{name}] skipped: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
