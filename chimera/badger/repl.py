"""Interactive REPL for the ``chimera badger`` subcommand.

Badger is the seventh Chimera coding-agent subcommand. Like its
siblings, it composes the same Chimera primitives (``Agent``,
``LoopConfig``, ``ReAct``, the slash command palette in
:mod:`chimera.cli.slash_commands`) so improvements to the shared REPL
flow downstream into every subcommand automatically.

This module:

* :func:`run_badger_repl` — top-level entry point for interactive mode.
  Builds an :class:`~chimera.core.agent.Agent`, persists per-run events
  under ``~/.chimera/eventlog/badger-<id>/``, and hands control to
  :func:`chimera.cli.code.run_code`.
* :func:`build_badger_agent` — construct a default badger Agent without
  entering the REPL loop. Used by tests to verify wiring without a TTY.
* :func:`shim_badger_args` — translate badger flags into the namespace
  the shared ``run_code`` expects.

Trademark hygiene: this module avoids naming the upstream by brand.
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
    "build_badger_agent",
    "badger_eventlog_root",
    "make_run_id",
    "open_badger_run_log",
    "run_badger_repl",
    "shim_badger_args",
]


def badger_eventlog_root() -> Path:
    """Root directory for all persisted badger runs."""
    return Path.home() / ".chimera" / "eventlog"


def make_run_id() -> str:
    """Generate a sortable, unique run id for a badger REPL session.

    The id is ``badger-<utc_compact>-<uuid8>``, e.g.
    ``badger-20260430T013012-a3f9b1c2``.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"badger-{stamp}-{suffix}"


def open_badger_run_log(run_id: str | None = None) -> tuple[Any, Path]:
    """Open (or create) an :class:`EventLog` for ``run_id``.

    Args:
        run_id: The persisted run identifier. When ``None``, a fresh id
            is minted via :func:`make_run_id`.

    Returns:
        A tuple of ``(EventLog, run_dir)``.
    """
    from chimera.sessions.eventlog.log import EventLog

    resolved = run_id or make_run_id()
    run_dir = badger_eventlog_root() / resolved
    run_dir.mkdir(parents=True, exist_ok=True)
    return EventLog(run_dir), run_dir


def _build_badger_provider(args: argparse.Namespace) -> Any:
    """Resolve the badger provider, with safe fallback to the generic factory.

    Args:
        args: Parsed argparse namespace from the badger subparser.

    Returns:
        A configured :class:`~chimera.providers.base.Provider`.

    Raises:
        ValueError: When neither the badger chain nor the generic factory
            can resolve a provider.
    """
    try:
        from chimera.badger import providers as _badger_providers
    except ImportError:
        _badger_providers = None  # type: ignore[assignment]

    if _badger_providers is not None and hasattr(
        _badger_providers, "build_provider"
    ):
        return _badger_providers.build_provider(args)

    from chimera.providers.factory import create_provider

    model = getattr(args, "model", None) or os.environ.get("BADGER_MODEL")
    return create_provider(model=model)


def shim_badger_args(args: argparse.Namespace) -> argparse.Namespace:
    """Translate badger flags into the namespace ``run_code`` expects.

    Args:
        args: Parsed badger namespace.

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
        max_steps=getattr(args, "max_steps", 25) or 25,
        mode="interactive",
        models=getattr(args, "models", "") or "",
        preset=getattr(args, "agent", None) or getattr(args, "preset", None),
        print_mode=None,
    )
    return shimmed


def build_badger_agent(
    args: argparse.Namespace, *, provider: Any | None = None,
) -> Any:
    """Construct a default badger Agent without entering the REPL loop.

    Args:
        args: Parsed badger namespace. Read for ``model`` and ``cwd``.
        provider: Optional pre-built provider. When ``None`` we resolve
            via :func:`_build_badger_provider`.

    Returns:
        A fully configured :class:`~chimera.core.agent.Agent`.
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS

    resolved_provider = (
        provider if provider is not None else _build_badger_provider(args)
    )
    max_steps = int(getattr(args, "max_steps", 25) or 25)
    config = LoopConfig()
    loop = ReAct(max_steps=max_steps, config=config)
    base_prompt = (
        "You are Badger, a Chimera coding agent in the harness-rewrite "
        "tradition. Plan briefly, act with a tight tool budget, and "
        "verify before declaring success."
    )
    prompt = Prompt.from_string(base_prompt)
    tools = list(AGENT_TOOLS)
    return Agent(
        provider=resolved_provider,
        tools=tools,
        loop=loop,
        prompt=prompt,
    )


def run_badger_repl(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera badger`` for interactive mode.

    Args:
        args: Parsed argparse namespace from the badger subparser.

    Returns:
        Process exit code (``0`` on success).
    """
    # 1. Stake an eventlog directory.
    try:
        _log, run_dir = open_badger_run_log(getattr(args, "run_id", None))
    except Exception as exc:  # noqa: BLE001
        print(f"[badger] eventlog setup failed: {exc}", file=sys.stderr)
        run_dir = None
    if run_dir is not None and not getattr(args, "_quiet_run_dir", False):
        print(f"[badger] run dir: {run_dir}")

    # 2. Resolve the provider eagerly so a missing API key surfaces here.
    try:
        provider = _build_badger_provider(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "\nSet up a provider:\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  export OPENAI_API_KEY='sk-...'\n"
            "  export OPENROUTER_API_KEY='sk-or-...'",
            file=sys.stderr,
        )
        return 1

    # 3. Hand off to the shared REPL.
    from chimera.cli.code import run_code

    shimmed = shim_badger_args(args)
    if getattr(provider, "model_name", None):
        shimmed.model = provider.model_name
    return int(run_code(shimmed))
