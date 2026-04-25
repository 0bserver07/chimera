"""Interactive REPL for the ``chimera otter`` subcommand.

Otter is the second Chimera coding-agent subcommand; mink mirrors one
upstream agent and otter mirrors the other. Both compose the same
Chimera primitives (``Agent``, ``LoopConfig``, ``ReAct``, the slash
command palette in :mod:`chimera.cli.code`) so improvements to the
shared REPL flow downstream into both subcommands automatically.

Wave-1 scope (this module):

* ``run_otter_repl(args)`` — the top-level entry point. It builds an
  :class:`~chimera.core.agent.Agent`, persists per-run events under
  ``~/.chimera/eventlog/otter-<id>/`` (collaborating with O3), and then
  hands control to :func:`chimera.cli.code.run_code` so future REPL
  features (steering, ``/yolo``, branching) reach otter users without a
  fork.

The companion otter modules (``providers``, ``slash``, ``sessions``)
are owned by sibling agents in the wave-1 build. We import them
lazily — every callsite degrades gracefully when the sibling module
isn't present yet, so this file stays loadable in isolation. That
lets tests in ``tests/otter/test_repl.py`` exercise import + bootstrap
behaviour even before the rest of the otter tree lands.

Trademark hygiene: this module deliberately uses the neutral phrasing
"the upstream coding agent" / "open-source coding agent" in any
user-visible string, per ``research/otter/SPEC.md``.
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
    "build_otter_agent",
    "make_run_id",
    "open_otter_run_log",
    "otter_eventlog_root",
    "run_otter_repl",
    "shim_otter_args",
]


# ---------------------------------------------------------------------------
# Eventlog / persistence helpers (collaborate with O3)
# ---------------------------------------------------------------------------

def otter_eventlog_root() -> Path:
    """Root directory for all persisted otter runs.

    Returns:
        ``~/.chimera/eventlog/`` honoring the current ``Path.home()``.
        O3 owns the subcommand-listing surface; we just mint
        ``otter-*`` run directories underneath this root.
    """
    return Path.home() / ".chimera" / "eventlog"


def make_run_id() -> str:
    """Generate a sortable, unique run id for an otter REPL session.

    The id is ``otter-<utc_compact>-<uuid8>`` (e.g.
    ``otter-20260425T013012-a3f9b1c2``). The compact UTC timestamp keeps
    lexical ordering aligned with chronological ordering, while the uuid
    suffix avoids collisions when two runs land in the same second.

    Returns:
        A new run id string.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"otter-{stamp}-{suffix}"


def open_otter_run_log(run_id: str | None = None) -> tuple[Any, Path]:
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
    run_dir = otter_eventlog_root() / resolved
    run_dir.mkdir(parents=True, exist_ok=True)
    return EventLog(run_dir), run_dir


# ---------------------------------------------------------------------------
# Provider wiring (collaborates with O12)
# ---------------------------------------------------------------------------

def _build_otter_provider(args: argparse.Namespace) -> Any:
    """Resolve the otter provider via the O12 module, with safe fallback.

    O12 is responsible for the OpenAI/OpenRouter/Anthropic provider
    chain plus the ``OTTER_MODEL`` env var. Until that module lands we
    fall back to :func:`chimera.providers.factory.create_provider` so
    the REPL is usable end-to-end during the parallel build.

    Args:
        args: Parsed argparse namespace from the otter subparser. We
            only read ``model`` and ``base_url`` off of it.

    Returns:
        A configured :class:`~chimera.providers.base.Provider`.

    Raises:
        ValueError: When neither the otter provider chain nor the
            generic factory can resolve a provider (e.g. no API key).
    """
    # Late-bind: O12 may or may not be present in the working tree yet.
    try:
        from chimera.otter import providers as _otter_providers  # type: ignore[attr-defined]
    except ImportError:
        _otter_providers = None  # type: ignore[assignment]

    if _otter_providers is not None and hasattr(_otter_providers, "build_provider"):
        return _otter_providers.build_provider(args)

    # Fallback: same path the generic ``chimera code`` REPL uses.
    from chimera.providers.factory import create_provider

    model = getattr(args, "model", None) or os.environ.get("OTTER_MODEL")
    return create_provider(model=model)


# ---------------------------------------------------------------------------
# Slash command palette (collaborates with O8)
# ---------------------------------------------------------------------------

def _resolve_slash_registry() -> dict[str, Any] | None:
    """Return otter-specific slash overrides, or ``None`` to use defaults.

    O8 owns the otter slash palette. When ``chimera.otter.slash`` is
    available and exposes ``COMMANDS``, those entries override or extend
    the shared registry in :mod:`chimera.cli.slash_commands`. When the
    module isn't present yet, we fall through to mink's set (which is
    just the shared registry — both subcommands share the same default
    slash commands today).

    Returns:
        A mapping of ``{name: handler}`` to merge over the shared
        registry, or ``None`` when no overrides exist.
    """
    try:
        from chimera.otter import slash as _otter_slash  # type: ignore[attr-defined]
    except ImportError:
        return None
    commands = getattr(_otter_slash, "COMMANDS", None)
    if isinstance(commands, dict) and commands:
        return dict(commands)
    return None


# ---------------------------------------------------------------------------
# Argument shimming for chimera.cli.code.run_code
# ---------------------------------------------------------------------------

def shim_otter_args(args: argparse.Namespace) -> argparse.Namespace:
    """Translate otter-subcommand flags into the namespace ``run_code`` expects.

    ``chimera.cli.code.run_code`` reads attributes off ``args`` directly
    (``model``, ``workdir``, ``max_steps``, ``mode``, ``models``,
    ``preset``, ``print_mode``). We construct a fresh namespace with
    those names populated from the otter flag set so the shared REPL
    works unchanged. Mirrors ``chimera.mink.cli._shim_code_args``.

    Args:
        args: Parsed otter namespace.

    Returns:
        A new namespace tailored for ``run_code``.
    """
    cwd = os.path.abspath(getattr(args, "cwd", None) or getattr(args, "workdir", None) or os.getcwd())
    return argparse.Namespace(
        model=getattr(args, "model", None),
        workdir=cwd,
        max_steps=getattr(args, "max_steps", 50) or 50,
        mode="interactive",
        models=getattr(args, "models", "") or "",
        preset=getattr(args, "agent", None) or getattr(args, "preset", None),
        print_mode=None,
    )


# ---------------------------------------------------------------------------
# Agent bootstrap (test-friendly seam)
# ---------------------------------------------------------------------------

def build_otter_agent(args: argparse.Namespace, *, provider: Any | None = None) -> Any:
    """Construct a default otter Agent without entering the REPL loop.

    Tests use this seam to verify wiring (provider, default tools,
    prompt) without needing a TTY. The full REPL (steering thread,
    readline, slash dispatch) is exercised separately by
    :func:`run_otter_repl`.

    Args:
        args: Parsed otter namespace. Read for ``model`` and ``cwd``.
        provider: Optional pre-built provider. When ``None`` we resolve
            via :func:`_build_otter_provider` (which falls back to the
            generic factory if the O12 module isn't present yet).

    Returns:
        A fully configured :class:`~chimera.core.agent.Agent`.
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS

    resolved_provider = provider if provider is not None else _build_otter_provider(args)
    max_steps = int(getattr(args, "max_steps", 50) or 50)
    config = LoopConfig()
    loop = ReAct(max_steps=max_steps, config=config)
    prompt = Prompt.from_string(
        "You are an interactive coding assistant in the otter REPL. Use "
        "the available tools to read, edit, search, and run code. Be "
        "concise and direct.",
    )
    return Agent(
        provider=resolved_provider,
        tools=list(AGENT_TOOLS),
        loop=loop,
        prompt=prompt,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_otter_repl(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera otter`` for interactive mode.

    Bootstraps an otter run directory under
    ``~/.chimera/eventlog/otter-*/`` (so ``chimera otter sessions
    list/show`` can find this run later) and then delegates to
    :func:`chimera.cli.code.run_code` with a shimmed namespace. The
    same delegation pattern powers ``chimera mink`` so the two
    subcommands stay in lockstep on REPL features.

    Args:
        args: Parsed argparse namespace from the otter subparser.
            Must expose at minimum ``model``, ``cwd``/``workdir``,
            ``max_steps``. ``agent`` (preset) and ``models`` (cycling
            list) are optional.

    Returns:
        Process exit code (``0`` on success).
    """
    # 1. Stake an eventlog directory for this run. The actual stream of
    #    events is appended by the REPL via the shared session machinery
    #    (O3 wires the listing/show commands on top of this layout).
    try:
        _log, run_dir = open_otter_run_log(getattr(args, "run_id", None))
    except Exception as exc:  # noqa: BLE001
        # Persistence is best-effort. Print to stderr but do not crash
        # the REPL — interactive use is still valuable without a log.
        print(f"[otter] eventlog setup failed: {exc}", file=sys.stderr)
        run_dir = None
    if run_dir is not None and not getattr(args, "_quiet_run_dir", False):
        print(f"[otter] run dir: {run_dir}")

    # 2. Allow the slash module to register otter-specific overrides.
    #    The shared registry in chimera.cli.slash_commands is the
    #    canonical source of truth; otter-only commands extend it.
    overrides = _resolve_slash_registry()
    if overrides:
        try:
            from chimera.cli import slash_commands as _shared_slash

            for name, handler in overrides.items():
                # WHY: register() exists on the shared module today;
                # if it ever gets renamed, fall back to mutating the
                # underlying dict so we don't crash the REPL.
                if hasattr(_shared_slash, "register"):
                    _shared_slash.register(name, handler)
                else:  # pragma: no cover - defensive fallback
                    registry = getattr(_shared_slash, "_REGISTRY", None)
                    if isinstance(registry, dict):
                        registry[name] = handler
        except Exception as exc:  # noqa: BLE001
            print(f"[otter] slash overrides failed: {exc}", file=sys.stderr)

    # 3. Resolve the provider eagerly (matches mink's contract). This
    #    surfaces a missing API key with a clean error before we drop
    #    into the REPL — ``run_code`` will otherwise re-resolve from
    #    the model name, which is fine.
    try:
        provider = _build_otter_provider(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "\nSet up a provider:\n"
            "  export OPENAI_API_KEY='sk-...'\n"
            "  export OPENROUTER_API_KEY='sk-or-...'\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'",
            file=sys.stderr,
        )
        return 1

    # 4. Hand off to the shared REPL with an args namespace shaped the
    #    way ``run_code`` expects. We don't fork that body: future REPL
    #    improvements (tree, /yolo, steering) automatically reach otter.
    from chimera.cli.code import run_code

    shimmed = shim_otter_args(args)
    if getattr(provider, "model_name", None):
        shimmed.model = provider.model_name
    return int(run_code(shimmed))
