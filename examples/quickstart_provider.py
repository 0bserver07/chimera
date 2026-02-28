#!/usr/bin/env python3
"""Quickstart: Connect to any Anthropic-compatible provider and chat.

Usage:

  # GLM-5 via api.z.ai
  export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
  export ANTHROPIC_AUTH_TOKEN="your-token-here"
  python examples/quickstart_provider.py --model glm-5

  # Claude direct
  export ANTHROPIC_API_KEY="sk-ant-..."
  python examples/quickstart_provider.py --model claude-sonnet-4-20250514

  # Any OpenAI-compatible endpoint
  python examples/quickstart_provider.py --provider openai --model gpt-4o
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera


def main():
    parser = argparse.ArgumentParser(description="Test a provider connection")
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", "glm-5"))
    parser.add_argument("--provider", default=None, help="Provider type (auto-detected if omitted)")
    parser.add_argument("--base-url", default=None, help="Base URL override")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    base_url = args.base_url or os.environ.get("ANTHROPIC_BASE_URL")

    print(f"Provider: {args.provider or '(auto-detect)'}")
    print(f"Model:    {args.model}")
    print(f"Base URL: {base_url or '(default)'}")
    print(f"API key:  {'***' + api_key[-4:] if api_key else '(not set)'}")
    print()

    provider = chimera.create_provider(
        args.provider,
        model=args.model,
        api_key=api_key,
        base_url=base_url,
    )

    # --- Test 1: Text completion ---
    print("=== Text Completion ===")
    response = provider.complete([chimera.Message.user("What is 7 * 8? Reply with just the number.")])
    print(f"Response: {response.content}")
    print(f"Tokens:   {response.usage}")
    print()

    # --- Test 2: Tool use ---
    print("=== Tool Use ===")
    calc_tool = {
        "name": "calculator",
        "description": "Evaluate a math expression and return the result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "e.g. '2 + 2'"},
            },
            "required": ["expression"],
        },
    }
    response = provider.complete(
        [chimera.Message.user("What is 123 * 456? Use the calculator tool.")],
        tools=[calc_tool],
    )
    if response.has_tool_calls:
        tc = response.tool_calls[0]
        print(f"Tool call: {tc.name}({tc.arguments})")

        # Send result back
        messages = [
            chimera.Message.user("What is 123 * 456? Use the calculator tool."),
            chimera.Message.assistant("", tool_calls=[tc]),
            chimera.Message.tool(call_id=tc.id, content=str(eval(tc.arguments.get("expression", "0")))),
        ]
        final = provider.complete(messages, tools=[calc_tool])
        print(f"Final:     {final.content}")
    else:
        print(f"Direct answer: {response.content}")
    print()

    # --- Test 3: Multi-turn ---
    print("=== Multi-turn ===")
    messages = [
        chimera.Message.user("My favorite color is cerulean. Remember that."),
    ]
    r1 = provider.complete(messages)
    print(f"Turn 1: {r1.content[:80]}...")

    messages.append(chimera.Message.assistant(r1.content))
    messages.append(chimera.Message.user("What is my favorite color?"))
    r2 = provider.complete(messages)
    print(f"Turn 2: {r2.content}")
    print()

    # --- Cost ---
    total_cost = sum(
        chimera.calculate_cost(args.model, r.usage)
        for r in [response, r1, r2]
    )
    print(f"Estimated cost: ${total_cost:.6f}")
    print("Done.")


if __name__ == "__main__":
    main()
