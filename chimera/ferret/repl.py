"""Interactive REPL for the ``chimera ferret`` subcommand.

Ferret is the third Chimera coding-agent subcommand; mink mirrors a
TUI-first ergonomic, otter mirrors a server-first / multi-client posture,
and ferret mirrors a sandbox-first / IDE-first / OpenAI-flagship coding
agent. All three compose the same Chimera primitives (``Agent``,
``LoopConfig``, ``ReAct``, the slash command palette in
:mod:`chimera.cli.code`) so improvements to the shared REPL flow
downstream into every subcommand automatically.

Wave-1 scope (this module):

* ``run_ferret_repl(args)`` — the top-level entry point. It builds an
  :class:`~chimera.core.agent.Agent`, persists per-run events under
  ``~/.chimera/eventlog/ferret-<id>/`` (collaborating with
  :mod:`chimera.ferret.sessions`), and then hands control to
  :func:`chimera.cli.code.run_code` so future REPL features (steering,
  ``/yolo``, branching) reach ferret users without a fork.
* ``run_ferret_print(args)`` — minimal one-shot path that runs a single
  turn and prints the result. Sibling agents in the wave (FF2 sandbox,
  FF3 approval, FF6 providers) layer on top of this.

The companion ferret modules (``sandbox``, ``approval``, ``ide``,
``cloud_bridge``, ``providers``) are owned by sibling agents in the
wave. We import them lazily — every callsite degrades gracefully when
the sibling module isn't present yet, so this file stays loadable in
isolation. That lets tests in ``tests/ferret/test_repl.py`` exercise
import + bootstrap behaviour even before the rest of the ferret tree
lands.

Trademark hygiene: this module deliberately uses the neutral phrasing
"the upstream IDE-first OpenAI-flagship coding agent" in any
user-visible string, per ``research/ferret/SPEC.md``.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import uuid
from pathlib import Path
from typing import Any

__all__ = [
    "build_ferret_agent",
    "ferret_eventlog_root",
    "make_run_id",
    "open_ferret_run_log",
    "run_ferret_print",
    "run_ferret_repl",
    "shim_ferret_args",
]


# ---------------------------------------------------------------------------
# Eventlog / persistence helpers (collaborate with sessions.py)
# ---------------------------------------------------------------------------


def ferret_eventlog_root() -> Path:
    """Root directory for all persisted ferret runs.

    Returns:
        ``~/.chimera/eventlog/`` honoring the current ``Path.home()``.
        :mod:`chimera.ferret.sessions` owns the listing surface; we
        just mint ``ferret-*`` run directories underneath this root.
    """
    return Path.home() / ".chimera" / "eventlog"


def make_run_id() -> str:
    """Generate a sortable, unique run id for a ferret REPL session.

    The id is ``ferret-<utc_compact>-<uuid8>`` (e.g.
    ``ferret-20260430T013012-a3f9b1c2``). The compact UTC timestamp
    keeps lexical ordering aligned with chronological ordering, while
    the uuid suffix avoids collisions when two runs land in the same
    second.

    Returns:
        A new run id string.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"ferret-{stamp}-{suffix}"


def open_ferret_run_log(run_id: str | None = None) -> tuple[Any, Path]:
    """Open (or create) an :class:`EventLog` for ``run_id``.

    Args:
        run_id: The persisted run identifier. When ``None``, a fresh id
            is minted via :func:`make_run_id` so callers don't need a
            None-narrow first.

    Returns:
        A tuple of ``(EventLog, run_dir)``.
    """
    # WHY: keep the import local — ``chimera.sessions.eventlog.log`` pulls
    # in fcntl which isn't available on every CI shim. Keeping it lazy
    # also lets tests stub the directory without the real EventLog.
    from chimera.sessions.eventlog.log import EventLog

    resolved = run_id or make_run_id()
    run_dir = ferret_eventlog_root() / resolved
    run_dir.mkdir(parents=True, exist_ok=True)
    return EventLog(run_dir), run_dir


# ---------------------------------------------------------------------------
# Provider wiring (collaborates with FF6)
# ---------------------------------------------------------------------------


def _build_ferret_provider(args: argparse.Namespace) -> Any:
    """Resolve the ferret provider via the FF6 module, with safe fallback.

    FF6 owns the OpenAI-flagship provider chain (gpt-5 → gpt-4o →
    claude-sonnet-4-6 → openai/gpt-5 via OpenRouter). Until that module
    lands we fall back to :func:`chimera.providers.factory.create_provider`
    so the REPL is usable end-to-end during the parallel build.

    Args:
        args: Parsed argparse namespace from the ferret subparser. We
            only read ``model`` off of it.

    Returns:
        A configured :class:`~chimera.providers.base.Provider`.

    Raises:
        ValueError: When neither the ferret provider chain nor the
            generic factory can resolve a provider (e.g. no API key).
    """
    # Late-bind: FF6 may or may not be present in the working tree yet.
    try:
        from chimera.ferret import providers as _ferret_providers  # type: ignore[attr-defined]
    except ImportError:
        _ferret_providers = None  # type: ignore[assignment]

    if _ferret_providers is not None and hasattr(
        _ferret_providers, "build_provider"
    ):
        return _ferret_providers.build_provider(args)

    from chimera.providers.factory import create_provider

    model = getattr(args, "model", None) or os.environ.get("FERRET_MODEL")
    return create_provider(model=model)


# ---------------------------------------------------------------------------
# Argument shimming for chimera.cli.code.run_code
# ---------------------------------------------------------------------------


def shim_ferret_args(args: argparse.Namespace) -> argparse.Namespace:
    """Translate ferret-subcommand flags into the namespace ``run_code`` expects.

    ``chimera.cli.code.run_code`` reads attributes off ``args`` directly
    (``model``, ``workdir``, ``max_steps``, ``mode``, ``models``,
    ``preset``, ``print_mode``). We construct a fresh namespace with
    those names populated from the ferret flag set so the shared REPL
    works unchanged. Mirrors :func:`chimera.otter.repl.shim_otter_args`.

    Args:
        args: Parsed ferret namespace.

    Returns:
        A new namespace tailored for ``run_code``.
    """
    cwd = os.path.abspath(
        getattr(args, "cwd", None)
        or getattr(args, "workdir", None)
        or os.getcwd()
    )
    shimmed = argparse.Namespace(
        model=getattr(args, "model", None),
        workdir=cwd,
        max_steps=getattr(args, "max_steps", 50) or 50,
        mode="interactive",
        models=getattr(args, "models", "") or "",
        preset=getattr(args, "agent", None) or getattr(args, "preset", None),
        print_mode=None,
    )
    return shimmed


# ---------------------------------------------------------------------------
# Agent bootstrap (test-friendly seam)
# ---------------------------------------------------------------------------


def build_ferret_agent(
    args: argparse.Namespace, *, provider: Any | None = None,
) -> Any:
    """Construct a default ferret Agent without entering the REPL loop.

    Tests use this seam to verify wiring (provider, default tools,
    prompt) without needing a TTY. The full REPL (steering thread,
    readline, slash dispatch) is exercised separately by
    :func:`run_ferret_repl`.

    Args:
        args: Parsed ferret namespace. Read for ``model`` and ``cwd``.
        provider: Optional pre-built provider. When ``None`` we resolve
            via :func:`_build_ferret_provider` (which falls back to the
            generic factory if the FF6 module isn't present yet).

    Returns:
        A fully configured :class:`~chimera.core.agent.Agent`.
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS

    resolved_provider = (
        provider if provider is not None else _build_ferret_provider(args)
    )
    max_steps = int(getattr(args, "max_steps", 50) or 50)
    config = LoopConfig()
    loop = ReAct(max_steps=max_steps, config=config)
    base_prompt = (
        "You are Ferret, a Chimera coding agent in the sandbox-first / "
        "IDE-first tradition. Use the available tools to read, edit, "
        "search, and run code. Default to the safest sandbox tier; "
        "request elevation only when a write or network op is required."
    )
    prompt = Prompt.from_string(base_prompt)
    tools = list(AGENT_TOOLS)
    return Agent(
        provider=resolved_provider,
        tools=tools,
        loop=loop,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# One-shot --print path (delegates through run_code in JSON modes)
# ---------------------------------------------------------------------------


def run_ferret_print(args: argparse.Namespace) -> int:
    """Run a single turn for ``chimera ferret -p PROMPT``.

    Stays minimal: build provider + agent, run one async turn, print the
    output. Sibling agents (FF2 sandbox, FF3 approval, FF6 providers)
    will replace this with a sandbox-first runner. For now the scaffold
    delegates to a plain :class:`Agent.async_run` against a
    :class:`LocalEnvironment` so the ``-p`` surface is testable.

    Args:
        args: Parsed ferret namespace; reads ``print_mode``, ``model``,
            ``cwd``, ``max_steps``, ``output_format``.

    Returns:
        Process exit code: 0 on success, 1 on agent failure, 2 on usage
        error, 130 on cancellation.
    """
    import asyncio
    import json

    from chimera.core.agent import Agent
    from chimera.core.cancellation import CancellationToken
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment

    prompt_text = getattr(args, "print_mode", None)
    if not prompt_text:
        print("ferret -p: missing PROMPT argument", file=sys.stderr)
        return 2

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    output_format = getattr(args, "output_format", "text") or "text"

    provider = _build_ferret_provider(args)
    env = LocalEnvironment(workdir=cwd)
    env.setup()

    cancel = CancellationToken()
    config = LoopConfig(cancellation=cancel)
    loop = ReAct(max_steps=int(getattr(args, "max_steps", 50) or 50), config=config)
    base_prompt = (
        "You are Ferret, a Chimera coding agent. Plan briefly, then act."
    )
    chimera_prompt = Prompt.from_string(base_prompt)
    tools = list(AGENT_TOOLS)
    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=chimera_prompt)

    try:
        result = asyncio.run(agent.async_run(prompt_text, env=env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        env.cleanup()

    if output_format == "json":
        payload = {
            "output": getattr(result, "output", ""),
            "steps": getattr(result, "steps", 0),
            "cost": getattr(result, "cost", 0.0),
            "success": getattr(result, "success", False),
            "model": getattr(provider, "model_name", getattr(args, "model", "")),
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        out = getattr(result, "output", None)
        if out:
            print(out)
    return 0 if getattr(result, "success", False) else 1


# ---------------------------------------------------------------------------
# Public REPL entry point
# ---------------------------------------------------------------------------


def run_ferret_repl(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera ferret`` for interactive mode.

    Bootstraps a ferret run directory under
    ``~/.chimera/eventlog/ferret-*/`` (so ``chimera ferret sessions
    list/show`` can find this run later) and then delegates to
    :func:`chimera.cli.code.run_code` with a shimmed namespace. The
    same delegation pattern powers ``chimera otter`` so the
    subcommands stay in lockstep on REPL features.

    Args:
        args: Parsed argparse namespace from the ferret subparser.
            Must expose at minimum ``model``, ``cwd``/``workdir``,
            ``max_steps``. ``agent`` (preset) and ``models`` (cycling
            list) are optional.

    Returns:
        Process exit code (``0`` on success).
    """
    # 1. Stake an eventlog directory for this run. The actual stream of
    #    events is appended by the REPL via the shared session machinery
    #    (sessions.py wires the listing/show commands on top of this
    #    layout).
    try:
        _log, run_dir = open_ferret_run_log(getattr(args, "run_id", None))
    except Exception as exc:  # noqa: BLE001
        # Persistence is best-effort. Print to stderr but do not crash
        # the REPL — interactive use is still valuable without a log.
        print(f"[ferret] eventlog setup failed: {exc}", file=sys.stderr)
        run_dir = None
    if run_dir is not None and not getattr(args, "_quiet_run_dir", False):
        print(f"[ferret] run dir: {run_dir}")

    # 2. Resolve the provider eagerly. This surfaces a missing API key
    #    with a clean error before we drop into the REPL — ``run_code``
    #    will otherwise re-resolve from the model name, which is fine.
    try:
        provider = _build_ferret_provider(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "\nSet up a provider:\n"
            "  export OPENAI_API_KEY='sk-...'\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  export OPENROUTER_API_KEY='sk-or-...'",
            file=sys.stderr,
        )
        return 1

    # 3. Hand off to the shared REPL with an args namespace shaped the
    #    way ``run_code`` expects. We don't fork that body: future REPL
    #    improvements (tree, /yolo, steering) automatically reach ferret.
    from chimera.cli.code import run_code

    shimmed = shim_ferret_args(args)
    if getattr(provider, "model_name", None):
        shimmed.model = provider.model_name
    return int(run_code(shimmed))
