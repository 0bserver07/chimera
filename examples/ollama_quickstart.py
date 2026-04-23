#!/usr/bin/env python3
"""Run Chimera against any Ollama cloud model via the Anthropic-compatible endpoint.

Ollama exposes an Anthropic-compatible API on http://localhost:11434. Point Chimera
at it with ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN and you can drive any local
or cloud Ollama model (kimi-k2.6:cloud, glm-5.1:cloud, qwen3.5:cloud, etc.).

Usage:

  # 1. Start Ollama and pull a model
  #    ollama serve
  #    ollama pull kimi-k2.6:cloud

  # 2. Run the quickstart (defaults shown)
  python examples/ollama_quickstart.py

  # Override the model or endpoint
  python examples/ollama_quickstart.py --model glm-5.1:cloud
  python examples/ollama_quickstart.py --model qwen3.5 --base-url http://localhost:11434

  # Skip sections if a model does not support tools
  python examples/ollama_quickstart.py --skip-tool-use
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chimera

DEFAULT_MODEL = "kimi-k2.6:cloud"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_AUTH_TOKEN = "ollama"


def _mask_token(tok: str) -> str:
    """Mask a token for display. Shows first 4 and last 4 chars only."""
    if not tok:
        return "(empty)"
    if len(tok) <= 10:
        return "***"
    return f"{tok[:4]}...{tok[-4:]}"


def preflight(base_url: str) -> bool:
    """Ping Ollama. Return True if reachable, False otherwise."""
    parsed = urlparse(base_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with urllib.request.urlopen(host_root, timeout=3) as resp:
            body = resp.read(128).decode("utf-8", errors="replace")
            # Ollama root returns "Ollama is running"
            return "Ollama" in body or resp.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def print_preflight_error(base_url: str) -> None:
    print(f"Could not reach Ollama at {base_url}.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix:", file=sys.stderr)
    print("  1. Start the server:   ollama serve", file=sys.stderr)
    print("  2. Pull a cloud model: ollama pull kimi-k2.6:cloud", file=sys.stderr)
    print("  3. Re-run this script.", file=sys.stderr)
    print("", file=sys.stderr)
    print("If Ollama lives on a different host, pass --base-url.", file=sys.stderr)


def print_usage(usage) -> None:
    """Best-effort pretty-print of a usage object."""
    if usage is None:
        print("Tokens:   (none reported)")
        return
    # usage is typically a dict-like or Usage object
    try:
        if hasattr(usage, "__dict__") and not isinstance(usage, dict):
            items = vars(usage)
        elif isinstance(usage, dict):
            items = usage
        else:
            items = {"raw": usage}
        print(f"Tokens:   {items}")
    except Exception:
        print(f"Tokens:   {usage!r}")


def safe_cost(model: str, usage) -> float:
    """Compute cost; many Ollama models are unpriced in the registry — return 0.0."""
    if usage is None:
        return 0.0
    try:
        return chimera.calculate_cost(model, usage)
    except Exception:
        return 0.0


def demo_text_completion(provider, model: str) -> None:
    print("=== 1. Plain text completion ===")
    response = provider.complete(
        [chimera.Message.user("What is 7 * 8? Reply with just the number.")]
    )
    print(f"Response: {response.content}")
    print_usage(response.usage)
    print(f"Cost:     ${safe_cost(model, response.usage):.6f}")
    print()


def demo_tool_use(provider, model: str) -> None:
    print("=== 2. Tool use (calculator) ===")
    calc_tool = {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression and return the numeric result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A Python-style arithmetic expression, e.g. '123 * 456'",
                },
            },
            "required": ["expression"],
        },
    }

    prompt = "What is 123 * 456? Use the calculator tool, do not compute it yourself."
    first = provider.complete([chimera.Message.user(prompt)], tools=[calc_tool])

    if first.has_tool_calls:
        tc = first.tool_calls[0]
        expr = tc.arguments.get("expression", "0")
        print(f"Tool call: {tc.name}({expr!r})")
        # Evaluate the expression locally. Only arithmetic is allowed.
        try:
            result = str(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
        except Exception as exc:
            result = f"error: {exc}"

        messages = [
            chimera.Message.user(prompt),
            chimera.Message.assistant("", tool_calls=[tc]),
            chimera.Message.tool(call_id=tc.id, content=result),
        ]
        final = provider.complete(messages, tools=[calc_tool])
        print(f"Final:     {final.content}")
        combined_cost = safe_cost(model, first.usage) + safe_cost(model, final.usage)
        print_usage(final.usage)
        print(f"Cost:     ${combined_cost:.6f} (both turns)")
    else:
        # Some open-weights models do not emit tool calls — that is fine.
        print("Model answered directly (no tool call emitted).")
        print(f"Response:  {first.content}")
        print_usage(first.usage)
        print(f"Cost:     ${safe_cost(model, first.usage):.6f}")
    print()


def demo_multi_turn(provider, model: str) -> None:
    print("=== 3. Multi-turn conversation ===")
    messages = [
        chimera.Message.user("My favorite color is cerulean. Remember that."),
    ]
    r1 = provider.complete(messages)
    print(f"Turn 1: {r1.content[:80].strip()}")

    messages.append(chimera.Message.assistant(r1.content))
    messages.append(chimera.Message.user("What is my favorite color? One word."))
    r2 = provider.complete(messages)
    print(f"Turn 2: {r2.content.strip()}")

    total = safe_cost(model, r1.usage) + safe_cost(model, r2.usage)
    print(f"Cost:   ${total:.6f} (both turns)")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chimera + Ollama quickstart",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"(default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"(default: {DEFAULT_BASE_URL})")
    parser.add_argument("--auth-token", default=DEFAULT_AUTH_TOKEN, help="Auth token (default: 'ollama')")
    parser.add_argument("--skip-tool-use", action="store_true", help="Skip the tool-use demo")
    parser.add_argument("--skip-multi-turn", action="store_true", help="Skip the multi-turn demo")
    args = parser.parse_args()

    # Set the env vars that chimera.create_provider() reads. We set them
    # regardless of what is in the user's environment so that --auth-token
    # and --base-url override any existing values for this process.
    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    os.environ["ANTHROPIC_AUTH_TOKEN"] = args.auth_token
    # Make sure a stale real key does not hijack routing.
    os.environ.setdefault("ANTHROPIC_API_KEY", "")

    print(f"Model:    {args.model}")
    print(f"Base URL: {args.base_url}")
    print(f"Auth:     {_mask_token(args.auth_token)}")
    print()

    # --- Pre-flight ---
    if not preflight(args.base_url):
        print_preflight_error(args.base_url)
        return 2

    print("Ollama is up. Creating provider...")
    try:
        provider = chimera.create_provider(
            model=args.model,
            api_key=args.auth_token,
            base_url=args.base_url,
        )
    except Exception as exc:
        print(f"Could not create provider: {exc}", file=sys.stderr)
        print("Is the model pulled? Try:  ollama pull " + args.model, file=sys.stderr)
        return 3
    print()

    try:
        demo_text_completion(provider, args.model)
        if not args.skip_tool_use:
            demo_tool_use(provider, args.model)
        if not args.skip_multi_turn:
            demo_multi_turn(provider, args.model)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        print(
            "If the model does not support tool use, re-run with --skip-tool-use.",
            file=sys.stderr,
        )
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
