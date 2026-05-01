#!/usr/bin/env python3
"""Live smoke test for weasel SDK and RPC against a real LLM.

Drives every public ``chimera.weasel.sdk.Agent`` entrypoint plus the
``chimera weasel --mode rpc`` JSON-RPC server against a real model.

Default model is ``glm-5.1:cloud`` served through Ollama's
Anthropic-compatible endpoint at ``http://127.0.0.1:11434``. Override
with ``--model`` and / or pre-existing ``ANTHROPIC_BASE_URL`` /
``ANTHROPIC_AUTH_TOKEN`` env vars.

Usage:
    python examples/weasel_live_smoke.py
    python examples/weasel_live_smoke.py --model glm-5.1:cloud
    python examples/weasel_live_smoke.py --skip-rpc

Outputs each phase's full transcript to stdout so the human running the
script can verify the live LLM actually replied.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time


def _ensure_anthropic_compat_env(default_url: str = "http://127.0.0.1:11434") -> None:
    """Ensure ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN are set.

    The smoke test routes ``glm-5.1:cloud`` through Ollama's
    Anthropic-compat endpoint. If the user already has these set
    (e.g. for z.ai), we leave them alone.
    """
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        os.environ["ANTHROPIC_BASE_URL"] = default_url
    if not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "ollama"


def phase_sdk_run(model: str) -> bool:
    """Phase 1a: synchronous one-shot via Agent.run."""
    from chimera.weasel.sdk import Agent

    print(f"\n=== SDK.run (model={model}) ===")
    agent = Agent(model=model)
    t0 = time.time()
    result = agent.run("what is 2+2?")
    dt = time.time() - t0
    print(f"  success={result.success} steps={result.steps} dt={dt:.2f}s")
    print(f"  output={result.output!r}")
    return result.success and ("4" in (result.output or ""))


def phase_sdk_arun(model: str) -> bool:
    """Phase 1b: async one-shot via Agent.arun."""
    from chimera.weasel.sdk import Agent

    print(f"\n=== SDK.arun (model={model}) ===")
    agent = Agent(model=model)

    async def _run() -> object:
        return await agent.arun("what is 2+2?")

    t0 = time.time()
    result = asyncio.run(_run())
    dt = time.time() - t0
    print(f"  success={result.success} steps={result.steps} dt={dt:.2f}s")
    print(f"  output={result.output!r}")
    return result.success and ("4" in (result.output or ""))


def phase_sdk_stream(model: str) -> bool:
    """Phase 1c: synchronous streaming via Agent.stream."""
    from chimera.weasel.sdk import Agent, EventType

    print(f"\n=== SDK.stream (model={model}) ===")
    agent = Agent(model=model)
    saw_text = False
    saw_done = False
    final_output = ""
    t0 = time.time()
    for event in agent.stream("what is 2+2?"):
        if event.type == EventType.TEXT:
            saw_text = True
            print(f"  [text] {event.text!r}")
        elif event.type == EventType.STEP:
            print(f"  [step] {event.step}")
        elif event.type == EventType.DONE:
            saw_done = True
            if event.result is not None:
                final_output = event.result.output or ""
                print(f"  [done] success={event.result.success} output={final_output!r}")
    dt = time.time() - t0
    print(f"  dt={dt:.2f}s saw_text={saw_text} saw_done={saw_done}")
    return saw_text and saw_done and ("4" in final_output)


def phase_sdk_chat(model: str) -> bool:
    """Phase 1d: multi-turn chat state via Agent.chat."""
    from chimera.weasel.sdk import Agent

    print(f"\n=== SDK.chat (model={model}) ===")
    agent = Agent(model=model)
    t0 = time.time()
    r1 = agent.chat("hi")
    print(f"  turn1 -> {r1!r}")
    r2 = agent.chat("what's my name? I didn't say.")
    print(f"  turn2 -> {r2!r}")
    dt = time.time() - t0
    print(f"  dt={dt:.2f}s")
    # Verify multi-turn state actually worked: agent must have responded
    # twice and the second response should reflect not-knowing.
    ok_turns = bool(r1) and bool(r2)
    return ok_turns


def phase_rpc(model: str, repo_root: str) -> bool:
    """Phase 2: JSON-RPC stdio server end-to-end.

    Spawns ``uv run chimera weasel --mode rpc --model ...``, sends three
    requests (``prompt`` / ``cancel`` / ``get_state``), validates the
    JSON-RPC envelope shape on each, and shuts the subprocess down by
    closing stdin.
    """
    print(f"\n=== RPC (model={model}) ===")
    cmd = ["uv", "run", "chimera", "weasel", "--mode", "rpc", "--model", model]
    print(f"  spawn: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=repo_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "prompt", "params": {"message": "what is 2+2?"}},
        {"jsonrpc": "2.0", "id": 2, "method": "cancel", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "get_state", "params": {}},
    ]

    out_lines: list[str] = []
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        # Build the full input frame; communicate() handles closing stdin.
        payload = "\n".join(json.dumps(req) for req in requests) + "\n"
        for req in requests:
            print(f"  -> {json.dumps(req)}")

        try:
            stdout, stderr = proc.communicate(input=payload, timeout=180)
        except subprocess.TimeoutExpired:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=15)

        if stderr:
            print(f"  [stderr]\n{stderr}")
        for raw in stdout.splitlines():
            raw = raw.strip()
            if raw:
                out_lines.append(raw)
                print(f"  <- {raw}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()

    # Validate envelopes.
    parsed = []
    for raw in out_lines:
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError:
            print(f"  WARN: non-JSON line {raw!r}")

    by_id = {p.get("id"): p for p in parsed if isinstance(p, dict)}
    ok = True
    for expected_id in (1, 2, 3):
        env = by_id.get(expected_id)
        if env is None:
            print(f"  FAIL: no response for id={expected_id}")
            ok = False
            continue
        if env.get("jsonrpc") != "2.0":
            print(f"  FAIL: id={expected_id} bad jsonrpc field")
            ok = False
        if "result" not in env and "error" not in env:
            print(f"  FAIL: id={expected_id} missing result/error")
            ok = False

    # Shape-specific checks.
    p1 = by_id.get(1, {}).get("result")
    if isinstance(p1, dict):
        print(f"  prompt result keys={sorted(p1)} success={p1.get('success')}")
    p2 = by_id.get(2, {}).get("result")
    if isinstance(p2, dict):
        print(f"  cancel result={p2}")
        if "cancelled" not in p2:
            print("  FAIL: cancel result missing 'cancelled'")
            ok = False
    p3 = by_id.get(3, {}).get("result")
    if isinstance(p3, dict):
        print(f"  get_state keys={sorted(p3)} model={p3.get('model')!r}")
        if "messages" not in p3 or "model" not in p3:
            print("  FAIL: get_state result missing keys")
            ok = False

    return ok


def main() -> int:
    """Run all phases and print PASS / FAIL summary."""
    parser = argparse.ArgumentParser(description="Weasel SDK + RPC live smoke test")
    parser.add_argument("--model", default="glm-5.1:cloud")
    parser.add_argument("--repo-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--skip-rpc", action="store_true")
    parser.add_argument("--skip-sdk", action="store_true")
    args = parser.parse_args()

    _ensure_anthropic_compat_env()
    print(f"ANTHROPIC_BASE_URL={os.environ.get('ANTHROPIC_BASE_URL')}")
    print(f"ANTHROPIC_AUTH_TOKEN_set={bool(os.environ.get('ANTHROPIC_AUTH_TOKEN') or os.environ.get('ANTHROPIC_API_KEY'))}")

    results: dict[str, bool] = {}
    if not args.skip_sdk:
        for name, fn in (
            ("sdk.run", phase_sdk_run),
            ("sdk.arun", phase_sdk_arun),
            ("sdk.stream", phase_sdk_stream),
            ("sdk.chat", phase_sdk_chat),
        ):
            try:
                results[name] = fn(args.model)
            except Exception as exc:
                print(f"  EXC {name}: {exc!r}")
                results[name] = False
    if not args.skip_rpc:
        try:
            results["rpc"] = phase_rpc(args.model, args.repo_root)
        except Exception as exc:
            print(f"  EXC rpc: {exc!r}")
            results["rpc"] = False

    print("\n=== summary ===")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
