#!/usr/bin/env python3
"""Mink walking skeleton: drive the Chimera ReAct loop on Kimi K2.6 via Ollama.

Honors CHIMERA_MINK_MODEL, OLLAMA_HOST, CHIMERA_MINK_FALLBACK env vars
(plus the deprecated CHIMERA_CC_* aliases). On connection or auth
failure to the primary model, falls back to a smaller local model with
a reduced context window. See research/mink/25-implementation-plan.md
sections 4 and 5.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from chimera.core.agent import Agent
from chimera.core.cancellation import CancellationToken
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.message_queue import MessageQueues
from chimera.core.prompt import Prompt
from chimera.env.local import LocalEnvironment
from chimera.permissions.presets import AutoApprove
from chimera.providers.ollama import OllamaProvider
from chimera.streaming.handlers import ConsoleStreamHandler
from chimera.tools.bash import BashTool
from chimera.tools.edit import EditFileTool
from chimera.tools.list_files import ListFilesTool
from chimera.tools.read import ReadFileTool
from chimera.tools.search import SearchTool
from chimera.tools.todo import TodoTool
from chimera.tools.write import WriteFileTool

_DEFAULT_HOST = "http://localhost:11434"


def _env_with_legacy(new: str, legacy: str, default: str) -> str:
    """Read ``new`` env var; fall back to deprecated ``legacy`` with a warning."""
    val = os.environ.get(new)
    if val:
        return val
    legacy_val = os.environ.get(legacy)
    if legacy_val:
        print(
            f"[deprecated] {legacy} is deprecated; use {new} instead.",
            file=sys.stderr,
        )
        return legacy_val
    return default


MODEL = _env_with_legacy("CHIMERA_MINK_MODEL", "CHIMERA_CC_MODEL", "kimi-k2.6:cloud")
FALLBACK = _env_with_legacy("CHIMERA_MINK_FALLBACK", "CHIMERA_CC_FALLBACK", "qwen3:32b")
# Empty string from `OLLAMA_HOST=` would otherwise crash with
# `httpcore.UnsupportedProtocol` on the first request; treat blank as unset.
HOST = os.environ.get("OLLAMA_HOST") or _DEFAULT_HOST


def make_provider(model: str, *, fallback: bool = False) -> OllamaProvider:
    # Cloud Kimi advertises 262k; local fallbacks max out near 131k.
    ctx = 131_072 if fallback else (262_144 if model.startswith("kimi") else 131_072)
    return OllamaProvider(model=model, base_url=HOST, context_length=ctx)


def _daemon_reachable(host: str) -> bool:
    """Cheap probe so we can skip the live call when no daemon is running."""
    try:
        import httpx
    except ImportError:
        return False
    try:
        httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2).raise_for_status()
        return True
    except Exception:
        return False


async def main(prompt: str) -> int:
    import httpx

    if not _daemon_reachable(HOST):
        print(
            f"[skip] no Ollama daemon answering at {HOST}. "
            "Start `ollama serve` (or set OLLAMA_HOST) and rerun.",
            file=sys.stderr,
        )
        return 2

    try:
        provider = make_provider(MODEL)
        # Cheap liveness probe so we can fall back before burning a turn.
        httpx.get(f"{HOST.rstrip('/')}/api/tags", timeout=3).raise_for_status()
    except (httpx.HTTPError, OSError, ConnectionError) as exc:
        print(f"[warn] {MODEL} unavailable ({exc}); falling back to {FALLBACK}", file=sys.stderr)
        provider = make_provider(FALLBACK, fallback=True)

    cancel = CancellationToken()
    # WHY: LoopConfig.permissions wants the simple sync PermissionPolicy
    # contract; AutoApprove matches --permission-mode bypassPermissions /
    # acceptEdits semantics and lets the demo dispatch tools without prompts.
    config = LoopConfig(
        permissions=AutoApprove(),
        handler=ConsoleStreamHandler(),
        cancellation=cancel,
        message_queues=MessageQueues(),
    )

    tools = [
        BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool(),
        SearchTool(), ListFilesTool(),
        # WHY: persist=True so the walking-skeleton demo survives /resume;
        # tests use bare TodoTool() (ephemeral).
        TodoTool(persist=True),
    ]
    agent = Agent(
        provider=provider,
        tools=tools,
        loop=ReAct(max_steps=20, config=config),
        prompt=Prompt(
            "You are Mink, a Chimera coding agent. Use tools to inspect and "
            "modify the user's repo. Plan briefly, then act."
        ),
    )

    env = LocalEnvironment(workdir=os.getcwd())
    env.setup()
    try:
        result = await agent.async_run(prompt, env=env)
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        env.cleanup()

    cost = getattr(result, "cost", 0.0) or 0.0
    print(f"\n--- DONE --- steps={result.steps} ok={result.success} cost=${cost:.4f}")
    return 0 if result.success else 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mink_walking_skeleton",
        description=(
            "Mink walking skeleton: Chimera ReAct loop on Kimi K2.6 via Ollama. "
            "Honors CHIMERA_MINK_MODEL, CHIMERA_MINK_FALLBACK, OLLAMA_HOST "
            "(plus the deprecated CHIMERA_CC_* aliases)."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="prompt text (defaults to a small repo-inspection task)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    text = " ".join(args.prompt) or "list files then read README.md"
    raise SystemExit(asyncio.run(main(text)))
