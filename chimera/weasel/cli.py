"""``chimera weasel`` — minimal coding-agent harness CLI (W1 scaffold).

Weasel is the fourth Chimera coding-agent CLI. Where mink/otter/ferret each
ship rich opinionated ergonomics, weasel ships **powerful defaults + four
operating modes** and skips features like sub-agents and plan mode entirely.
Minimalism is the feature.

This module ships the W1 scaffold:

* ``add_arguments`` registers a deliberately tiny flag surface
  (``--version``, ``--mode``, ``--model``, ``-p``, ``--json``,
  ``--list-models``, plus a ``sessions`` subcommand placeholder for W1's
  list/show).
* ``run`` dispatches to the four documented modes — interactive (REPL),
  print, RPC, SDK passthrough — or the ``sessions`` subcommand.
* The three placeholder modes return ``2`` (usage) when their owners
  (W2 RPC, W4 SDK) haven't landed yet, so the scaffold never silently
  no-ops a request.

Trademark hygiene: never names the upstream brand. ``.weasel/extensions/``
is a filesystem fact (not a brand claim). The ``.weasel/`` mention here is
likewise a path, not a product name.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# WHY: stdlib only at import time. The interactive path delegates to
# ``chimera.cli.code.run_code`` which itself lazy-imports providers — so
# ``chimera weasel --help`` / ``--version`` stays cheap even when the
# Anthropic / OpenAI SDKs aren't installed.

_VERSION = "0.5.0"
"""Weasel scaffold version. Independent of the chimera package version
because weasel is a per-CLI release line."""

_DEFAULT_MODEL = "claude-sonnet-4-6"
"""Default model when neither ``--model`` nor ``$WEASEL_MODEL`` is set."""

_VALID_MODES = ("interactive", "print", "rpc", "sdk")
_VALID_SUBCOMMANDS = (None, "sessions", "share")
_VALID_SUB_ACTIONS = (None, "list", "show", "cost")


def _resolve_version() -> str:
    """Return the weasel scaffold version string for ``--version``.

    Returns:
        ``"0.5.0"`` (the per-CLI release line) — independent of the
        ``chimera-run`` package version. Mirrors the four-mode harness's
        own per-CLI release cadence.
    """
    return _VERSION


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera weasel`` flags on ``parser``.

    Mirrors mink/otter's ``add_arguments`` shape so embedders / tests can
    attach the same flag surface to a parser they already own.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the weasel
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera weasel {_resolve_version()}",
    )
    # WHY: env precedence is --model > $WEASEL_MODEL > _DEFAULT_MODEL,
    # mirroring otter's $OTTER_MODEL pattern. CI / shells pin a model
    # once while keeping ad-hoc --model overrides cheap.
    parser.add_argument(
        "--model",
        default=os.environ.get("WEASEL_MODEL") or _DEFAULT_MODEL,
        help=(
            "Model identifier (default: $WEASEL_MODEL or "
            f"{_DEFAULT_MODEL}). Resolved through "
            "``chimera.providers.factory.create_provider``."
        ),
    )
    # WHY: the four-mode philosophy. Default is interactive. Print is the
    # ``-p`` shortcut (parity with the upstream minimal harness). RPC is
    # stdio JSON-RPC for process integration (W2). SDK is the passthrough
    # banner pointing the user at ``from chimera.weasel.sdk import Agent``.
    parser.add_argument(
        "--mode",
        choices=list(_VALID_MODES),
        default="interactive",
        help=(
            "Operating mode: interactive (REPL, default), print "
            "(one-shot text/JSON), rpc (stdio JSON-RPC), or sdk "
            "(prints embedding pointer and exits)."
        ),
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        help="One-shot: run a single turn with PROMPT, print, exit.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help=(
            "When paired with -p, emit a single JSON object on stdout "
            "(``{output, success, model}``) instead of plain text."
        ),
    )
    parser.add_argument(
        "--list-models",
        dest="list_models",
        action="store_true",
        default=False,
        help=(
            "List models recognised by ``chimera.providers.cost.PRICING`` "
            "and exit."
        ),
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: current directory).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum agent steps per turn (default: 50).",
    )
    # WHY (sessions placeholder): weasel's only subcommand is ``sessions``
    # (list/show). Everything else stays out of the surface deliberately.
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help="Optional: 'sessions' (list/show/cost) or 'share <id>'.",
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        metavar="ACTION",
        help=(
            "With 'sessions': 'list', 'show <id>', or 'cost'. With "
            "'share': the SESSION_ID to share (positional)."
        ),
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Session id consumed by 'sessions show'.",
    )
    # WHY: cost subcommand flags. Mirror ``mink runs cost`` so the rollup
    # JSON/CSV/text shape is byte-identical across CLIs. All optional;
    # defaults come from ``cmd_sessions_cost``.
    parser.add_argument(
        "--since",
        dest="cost_since",
        default=None,
        help=(
            "With 'sessions cost': drop sessions older than this cutoff. "
            "Accepts shorthand (``7d`` / ``24h`` / ``30m``) or ISO-8601."
        ),
    )
    parser.add_argument(
        "--cost-model",
        dest="cost_model",
        default=None,
        help=(
            "With 'sessions cost': case-insensitive substring filter "
            "on model name. Pass ``all`` (or omit) for every model."
        ),
    )
    parser.add_argument(
        "--cost-format",
        dest="cost_format",
        choices=("text", "json", "csv"),
        default=None,
        help=(
            "With 'sessions cost': output format. Defaults to ``json`` "
            "when ``--json`` is set, ``text`` otherwise."
        ),
    )
    parser.add_argument(
        "--cost-limit",
        dest="cost_limit",
        type=int,
        default=None,
        help=(
            "With 'sessions cost': cap on rows considered (newest first; "
            "no cap by default)."
        ),
    )
    # WHY: share subcommand flags. Mirror otter's share_cmd; HTTP / HTML
    # are intentionally omitted — weasel keeps the share surface small.
    parser.add_argument(
        "--share-sink",
        dest="share_sink",
        choices=("file", "stdout"),
        default=None,
        help=(
            "With 'share': destination for the rendered transcript. "
            "Defaults to ``file`` (writes ``~/.chimera/shares/weasel-<id>.<ext>``)."
        ),
    )
    parser.add_argument(
        "--share-format",
        dest="share_format",
        choices=("json", "md"),
        default=None,
        help=(
            "With 'share': render format. Defaults to ``json`` "
            "(round-trips with ``sessions show --json``)."
        ),
    )


# ---------------------------------------------------------------------------
# --list-models
# ---------------------------------------------------------------------------


def _run_list_models() -> int:
    """Print known model identifiers (from ``chimera.providers.cost.PRICING``).

    Returns:
        Process exit code (always ``0``).
    """
    try:
        from chimera.providers.cost import PRICING
    except Exception as exc:  # noqa: BLE001 — never crash on import drift
        print(f"weasel: could not load model registry: {exc}", file=sys.stderr)
        return 1
    for model in sorted(PRICING):
        print(model)
    return 0


# ---------------------------------------------------------------------------
# print mode (one-shot)
# ---------------------------------------------------------------------------


def _activate_extensions(cwd: str) -> tuple[list[Any], list[Any]]:
    """Discover and activate weasel extensions for a single agent run.

    Walks ``<cwd>/.weasel/extensions/`` and ``~/.weasel/extensions/``
    via :func:`chimera.weasel.extensions.load_weasel_extensions`, then
    activates each plugin onto a fresh
    :class:`chimera.plugins.base.ComponentRegistry` so we can collect
    the tool + hook contributions. Plugin activation failures are
    swallowed: a single bad extension must not break the print path.

    Args:
        cwd: Project root used as the project-scope discovery anchor.

    Returns:
        ``(tools, hooks)`` — a list of :class:`BaseTool` instances and
        a list of :class:`Hook` records, both possibly empty.
    """
    from pathlib import Path

    from chimera.plugins.base import ComponentRegistry
    from chimera.weasel.extensions import load_weasel_extensions

    try:
        plugins = load_weasel_extensions(Path(cwd))
    except Exception as exc:  # noqa: BLE001 — never crash the print path
        print(
            f"weasel: extension discovery failed; continuing without "
            f"extensions: {exc}",
            file=sys.stderr,
        )
        return [], []

    registry = ComponentRegistry()
    for plugin in plugins:
        try:
            plugin.activate(registry)
        except Exception as exc:  # noqa: BLE001 — quarantine extension errors
            print(
                f"weasel: extension '{getattr(plugin, 'name', '?')}' failed "
                f"to activate: {exc}",
                file=sys.stderr,
            )
            continue

    tools = list(registry.tools)
    # ``ComponentRegistry.hooks`` is keyed by event type; flatten back to
    # a list of Hook records so downstream consumers see one record per
    # contribution.
    hooks: list[Any] = []
    for entries in registry.hooks.values():
        hooks.extend(entries)
    return tools, hooks


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single turn and emit results in the requested format.

    Builds a minimal :class:`Agent` directly (no MCP / LSP wiring, no
    rules ingestion, no checkpointing — that's the weasel point), then
    layers in any extensions discovered under
    ``<cwd>/.weasel/extensions/`` and ``~/.weasel/extensions/`` (W3).
    When ``--json`` is set, emits a single JSON object on stdout;
    otherwise prints the plain-text output.

    Args:
        args: Parsed CLI namespace from :func:`add_arguments`.

    Returns:
        Process exit code (``0`` on agent success, ``1`` otherwise).
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
    from chimera.weasel.providers import build_provider as _build_provider

    cwd = os.path.abspath(args.cwd or os.getcwd())

    try:
        # WHY: weasel.providers.build_provider knows about the full chain
        # including Ollama tag detection (`name:tag` ids like glm-5.1:cloud)
        # and the OpenRouter / llama.cpp / Ollama fallbacks. The bare
        # ``create_provider`` factory only handles prefix-based inference
        # which routes ``glm-*`` to Anthropic and fails on Ollama-tagged ids.
        provider = _build_provider(args)
    except Exception as exc:  # noqa: BLE001 — surface provider auth errors cleanly
        print(f"weasel: provider error: {exc}", file=sys.stderr)
        return 1

    env = LocalEnvironment(workdir=cwd)
    env.setup()

    cancel = CancellationToken()
    # WHY: load extensions before constructing the loop so any
    # extension-contributed hooks land in the LoopConfig.hooks bag and
    # extension tools are merged into the agent's tool group below.
    # Extension load failures are swallowed (load_errors lives on the
    # extension instance) so a bad extension cannot break the print path.
    ext_tools, ext_hooks = _activate_extensions(cwd)

    config = LoopConfig(cancellation=cancel)
    loop = ReAct(max_steps=int(args.max_steps), config=config)
    prompt = Prompt.from_string(
        "You are Weasel, a minimal Chimera coding agent. "
        "Use tools to inspect and modify the user's repo. Be concise."
    )

    tools = list(AGENT_TOOLS) + ext_tools
    agent = Agent(
        provider=provider,
        tools=tools,
        loop=loop,
        prompt=prompt,
    )
    # WHY: stash hooks where downstream consumers can find them. The
    # core ReAct loop doesn't natively dispatch shell-command hooks
    # (that's a follow-up); attaching them to the agent makes them
    # introspectable via tests and future hook-runner middleware.
    if ext_hooks:
        try:
            setattr(agent, "_weasel_extension_hooks", ext_hooks)
        except Exception:  # noqa: BLE001 — defensive
            pass

    result: Any = None
    try:
        result = asyncio.run(agent.async_run(args.print_mode, env=env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        env.cleanup()

    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", "") or ""

    if getattr(args, "json_output", False):
        payload = {
            "output": output,
            "success": success,
            "model": getattr(provider, "model_name", args.model),
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        if output:
            print(output)
    return 0 if success else 1


# ---------------------------------------------------------------------------
# RPC + SDK placeholders
# ---------------------------------------------------------------------------


def _run_rpc_mode(args: argparse.Namespace) -> int:
    """Run ``chimera weasel --mode rpc`` — stdio JSON-RPC 2.0 server.

    Late-binds to :func:`chimera.weasel.rpc.run_rpc_server` (W2). When the
    module is somehow unavailable the placeholder behaviour from the
    scaffold is preserved so shell pipelines surface a clear error.

    Args:
        args: Parsed weasel namespace; ``model`` / ``workdir`` /
            ``max_steps`` are forwarded to the server when present.

    Returns:
        Exit code from the RPC run loop.
    """
    try:
        from chimera.weasel.rpc import run_rpc_server
    except ImportError:
        print(
            "weasel rpc: stdio JSON-RPC mode not yet wired in this scaffold "
            "(see research/weasel/SPEC.md, agent W2).",
            file=sys.stderr,
        )
        return 2
    return int(run_rpc_server(args))


def _run_sdk_mode(_args: argparse.Namespace) -> int:
    """Pointer for ``chimera weasel --mode sdk``.

    The SDK is an import surface, not a CLI mode — invoking it from the
    shell prints the embedding hint and exits. W4 lands the actual
    :class:`chimera.weasel.sdk.Agent`; until then the import path is
    documented but not constructible.
    """
    print(
        "weasel sdk: embed via 'from chimera.weasel.sdk import Agent' "
        "(see research/weasel/SPEC.md, agent W4).",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Forward ``chimera weasel sessions [list|show <id>|cost]`` to W1's handler.

    Mirrors otter's dispatch shape so the same on-disk eventlog layout
    (under ``~/.chimera/eventlog/weasel-*``) is consumable with the same
    UX as ``chimera otter sessions``. The ``cost`` action re-uses
    :mod:`chimera.mink.cost` so the rollup schema stays identical across
    all four CLIs.
    """
    from chimera.weasel.sessions import dispatch_sessions

    return dispatch_sessions(args)


def _dispatch_share(args: argparse.Namespace) -> int:
    """Forward ``chimera weasel share <session-id>`` to W1's share handler.

    The W1 parser stores the session id in ``args.sub_action`` (the
    second positional slot). We forward the namespace so
    :func:`chimera.weasel.sessions.dispatch_share` reads
    ``share_sink`` / ``share_format`` flags off the same args object.
    """
    from chimera.weasel.sessions import dispatch_share

    return dispatch_share(args)


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera weasel``.

    Resolves the requested mode + subcommand:

    * ``--list-models`` — print and exit.
    * ``sessions list|show`` — forward to :mod:`chimera.weasel.sessions`.
    * ``-p PROMPT`` — one-shot print mode (text or JSON).
    * ``--mode rpc`` — stdio JSON-RPC server (W2 placeholder).
    * ``--mode sdk`` — embedding pointer.
    * default — interactive REPL via :func:`chimera.weasel.repl.run`.

    Args:
        args: Parsed namespace from the weasel subparser.

    Returns:
        Process exit code.
    """
    if getattr(args, "list_models", False):
        return _run_list_models()

    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    # ``-p`` always wins over --mode for CLI ergonomics parity with the
    # upstream minimal harness's print mode.
    if getattr(args, "print_mode", None) is not None:
        return _run_print_mode(args)

    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        return _run_rpc_mode(args)
    if mode == "sdk":
        return _run_sdk_mode(args)
    if mode == "print":
        # --mode print without -p is a usage error; we don't have a prompt
        # to feed the agent. Surface that explicitly rather than dropping
        # into the interactive REPL by accident.
        print(
            "weasel: --mode print requires -p PROMPT",
            file=sys.stderr,
        )
        return 2

    # Interactive (default).
    from chimera.weasel.repl import run as _repl_run

    return _repl_run(args)


__all__ = [
    "add_arguments",
    "run",
]
