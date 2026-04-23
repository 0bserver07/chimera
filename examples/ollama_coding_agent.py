#!/usr/bin/env python3
"""Drive a full Chimera coding agent end-to-end using an Ollama cloud model.

This spins up the fully-assembled `CodingAgent` (from chimera.assembly), points
it at an Ollama cloud model via the Anthropic-compatible endpoint, and runs a
concrete task in the current directory while streaming every loop event.

Usage:

  # 1. Start Ollama and pull a model with a big context window
  #    ollama serve
  #    ollama pull kimi-k2.6:cloud

  # 2. Run the agent on the default task
  python examples/ollama_coding_agent.py

  # Override the model or task
  python examples/ollama_coding_agent.py --model glm-5.1:cloud
  python examples/ollama_coding_agent.py --task "Summarize what this project does" --max-steps 20
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_MODEL = "kimi-k2.6:cloud"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_AUTH_TOKEN = "ollama"
DEFAULT_TASK = (
    "Scan the current working directory. Find every Python (.py) file, count the "
    "number of lines in each one, and print a short summary: total files, total "
    "lines, and the top 5 files by line count. Do not modify any files."
)

# Rough lower bound for a useful coding-agent context window. Anything smaller
# and the agent will thrash on even small repos.
MIN_CONTEXT_TOKENS = 64_000


def preflight_ollama(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with urllib.request.urlopen(host_root, timeout=3) as resp:
            body = resp.read(128).decode("utf-8", errors="replace")
            return "Ollama" in body or resp.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def check_model_context(base_url: str, model: str) -> tuple[bool, int | None, str]:
    """Ask Ollama for the model's context length via its native /api/show endpoint.

    Returns (ok, context_tokens, message). If we cannot determine the context
    length, we fall through with ok=True so the user is not blocked.
    """
    parsed = urlparse(base_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    try:
        import json as _json

        req = urllib.request.Request(
            f"{host_root}/api/show",
            data=_json.dumps({"name": model}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False, None, f"Model {model!r} is not pulled. Run: ollama pull {model}"
        return True, None, f"(could not introspect model: HTTP {exc.code})"
    except Exception as exc:
        return True, None, f"(could not introspect model: {exc})"

    # Ollama exposes context length under "model_info" as "<arch>.context_length"
    ctx = None
    model_info = info.get("model_info") or {}
    for key, val in model_info.items():
        if key.endswith(".context_length") and isinstance(val, int):
            ctx = val
            break
    if ctx is None:
        return True, None, "(context length not reported by Ollama)"
    if ctx < MIN_CONTEXT_TOKENS:
        msg = (
            f"Model {model!r} only reports a {ctx:,}-token context window. "
            f"A coding agent really wants >= {MIN_CONTEXT_TOKENS:,} tokens. "
            f"Try a cloud model like kimi-k2.6:cloud or glm-5.1:cloud."
        )
        return False, ctx, msg
    return True, ctx, f"Context window: {ctx:,} tokens."


def print_preflight_error(base_url: str, model: str) -> None:
    print(f"Could not reach Ollama at {base_url}.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Fix:", file=sys.stderr)
    print("  1. ollama serve", file=sys.stderr)
    print(f"  2. ollama pull {model}", file=sys.stderr)
    print("  3. Re-run this script.", file=sys.stderr)


def format_event_snippet(event) -> str:
    """Extract a short, printable snippet from a LoopEvent."""
    data = getattr(event, "data", None)
    if data is None:
        return ""
    # tool_use events carry a tool call
    if hasattr(data, "name") and hasattr(data, "arguments"):
        args_repr = str(data.arguments)
        if len(args_repr) > 120:
            args_repr = args_repr[:117] + "..."
        return f"{data.name}({args_repr})"
    # tool_result events are (tool_call, result) tuples
    if isinstance(data, tuple) and len(data) == 2:
        _tc, result = data
        text = getattr(result, "content", None) or getattr(result, "output", None) or str(result)
        text = str(text).replace("\n", " ")
        return text[:160] + ("..." if len(text) > 160 else "")
    # assistant / system / error: strings or objects with .content
    text = getattr(data, "content", None) or str(data)
    text = str(text).replace("\n", " ")
    return text[:160] + ("..." if len(text) > 160 else "")


async def run_agent(model: str, task: str, max_steps: int) -> int:
    from chimera.assembly.coding_agent import CodingAgent

    print(f"Building agent (model={model})...")
    try:
        agent = CodingAgent(model=model, project_dir=os.getcwd())
    except Exception as exc:
        print(f"Could not build CodingAgent: {exc}", file=sys.stderr)
        return 3
    # Honour --max-steps regardless of the preset default.
    try:
        agent._config.max_turns = max_steps
    except Exception:
        pass

    print(f"Task: {task}")
    print("--- streaming events ---")

    start = time.monotonic()
    step_count = 0
    final_result = None
    try:
        async for event in agent.run(task):
            etype = getattr(event.type, "value", str(event.type))
            if etype == "result":
                final_result = event.data
                continue
            snippet = format_event_snippet(event)
            if etype in ("tool_use", "tool_result"):
                step_count += 1
            if snippet:
                print(f"[{etype}] {snippet}")
            else:
                print(f"[{etype}]")
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        agent.abort()
        return 130

    elapsed = time.monotonic() - start
    print("--- done ---")

    # Pull totals out of the final LoopResult if we have one.
    total_cost = 0.0
    reason = "(unknown)"
    turn_count = step_count
    if final_result is not None:
        total_cost = getattr(final_result, "cost_usd", 0.0) or 0.0
        reason = getattr(final_result, "reason", reason)
        turn_count = getattr(final_result, "turn_count", turn_count)

    print()
    print(f"Reason:       {reason}")
    print(f"Total steps:  {turn_count}")
    print(f"Total cost:   ${total_cost:.6f}")
    print(f"Elapsed:      {elapsed:.1f}s")
    return 0


async def main_async(args) -> int:
    # Configure the Anthropic-compatible endpoint before we import the provider.
    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    os.environ["ANTHROPIC_AUTH_TOKEN"] = args.auth_token
    os.environ.setdefault("ANTHROPIC_API_KEY", "")

    print(f"Model:    {args.model}")
    print(f"Base URL: {args.base_url}")
    print()

    if not preflight_ollama(args.base_url):
        print_preflight_error(args.base_url, args.model)
        return 2

    ok, _ctx, msg = check_model_context(args.base_url, args.model)
    print(msg)
    if not ok:
        print("Aborting. Pull a larger model or pass --model.", file=sys.stderr)
        return 4
    print()

    return await run_agent(args.model, args.task, args.max_steps)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chimera CodingAgent driven by an Ollama model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"(default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"(default: {DEFAULT_BASE_URL})")
    parser.add_argument("--auth-token", default=DEFAULT_AUTH_TOKEN, help="Auth token (default: 'ollama')")
    parser.add_argument("--task", default=DEFAULT_TASK, help="Task for the agent")
    parser.add_argument("--max-steps", type=int, default=30, help="Max loop turns (default: 30)")
    args = parser.parse_args()

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
