"""``chimera stoat`` — Stoat, a Chimera coding agent in the shell-mode-toggle tradition.

Stoat is the sixth Chimera coding-agent CLI, paralleling :mod:`chimera.mink`,
:mod:`chimera.otter`, :mod:`chimera.ferret`, :mod:`chimera.weasel`, and
:mod:`chimera.shrew`. Where weasel ships a four-mode minimal harness and shrew
focuses on small local models, stoat mirrors a coding agent that exposes a
**shell-mode toggle** (``Ctrl-X`` / ``/shell``) — the same prompt buffer can
either feed the LLM agent or run shell commands directly, switching back and
forth without leaving the REPL.

This module ships:

* ``add_arguments`` — registers the documented flag surface
  (``--version``, ``--model``, ``-p``, ``--mode``, ``--shell-mode``, ``--cwd``,
  ``--max-steps``, ``--allowed-tools``, ``--no-color``, ``--no-rich``).
* ``run`` — dispatches to subcommands (``serve`` / ``sessions`` / ``share`` /
  ``agents`` / ``bench``) or to the print / interactive REPL paths.

The ``-p`` print path runs a single agent turn through
:func:`chimera.stoat.providers.build_provider`. The interactive REPL is
delegated to :mod:`chimera.stoat.repl`, which layers a shell-mode state
machine (:mod:`chimera.stoat.shell_mode`) over the shared ``chimera code``
REPL.

Trademark hygiene: this module never names the upstream brand. Path mentions
like ``~/.kimi/config.json`` are filesystem facts (not brand claims) and are
referenced in docs / scrub allowlists, never in source identifiers.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from chimera.errors import friendly_errors

# WHY: stdlib + chimera at import time. Provider deps (anthropic, openai,
# httpx) and the REPL/sessions stack lazy-import inside their dispatch
# helpers so ``chimera stoat --help`` and ``chimera stoat --version`` stay
# cheap even when the SDKs aren't installed.

_VERSION = "0.5.0"
"""Stoat scaffold version. Independent of the chimera package version
because stoat is a per-CLI release line (mirrors weasel/shrew)."""

_DEFAULT_MODEL = "kimi-k2.6"
"""Default model when neither ``--model`` nor ``$STOAT_MODEL`` is set.

WHY: the upstream shell-mode-toggle harness is tuned for the Kimi K2.6
chat model. Naming the model id is a filesystem-style fact (the model
exists; we route via :mod:`chimera.stoat.providers`); naming the brand
that produces it is what the trademark scrub forbids."""

_VALID_MODES = ("interactive", "print", "rpc")
"""Operating modes for ``--mode``. Print is the ``-p`` shortcut, RPC
delegates to :mod:`chimera.cli.code` in JSON-RPC mode."""

_VALID_SUBCOMMANDS = (
    None,
    "serve",
    "sessions",
    "share",
    "agents",
    "bench",
)

_VALID_SUB_ACTIONS = (None, "list", "show", "cost", "humaneval", "tau-bench")


# A10-W11: hold the parser ref so ``--help-long`` in ``run()`` can call
# ``parser.format_help()`` after argparse has already finished its parse.
_PARSER: argparse.ArgumentParser | None = None

# A10-W11: long-form per-flag descriptions printed by ``--help-long``.
# Short ``help=`` strings on the parser stay <=60 chars so ``chimera stoat
# --help`` fits in <=50 lines; the verbose copy lives here.
_LONG_HELP: dict[str, str] = {
    "--model": (
        "Model identifier. Resolution order: --model > $STOAT_MODEL > "
        f"the {_DEFAULT_MODEL} default. Routed through "
        "chimera.stoat.providers.build_provider so the Kimi-first "
        "provider chain is honored."
    ),
    "-p / --print": (
        "One-shot print mode: run a single agent turn against PROMPT, "
        "emit the assistant text on stdout, then exit. Pairs with "
        "--json for a machine-readable result envelope."
    ),
    "--mode": (
        "Operating mode: 'interactive' (REPL, default), 'print' "
        "(one-shot text — equivalent to -p), 'rpc' (delegate to "
        "chimera.cli.code in JSON-RPC mode for IDE integrations)."
    ),
    "--shell-mode": (
        "Boot the REPL already in shell mode. Each input line runs as "
        "'bash -c <input>' until /shell or Ctrl-X toggles back to agent "
        "mode. The shell-mode toggle is stoat's headline feature."
    ),
    "--cwd": (
        "Working directory for the agent run. Default: process cwd. "
        "Resolved to an absolute path before the env is built."
    ),
    "--max-steps": (
        "Maximum agent steps per turn (default 50). Cap protects "
        "against runaway loops. Honored by both -p and the REPL."
    ),
    "--allowed-tools": (
        "Comma-separated tool name allowlist (case-insensitive). Empty "
        "means every tool in AGENT_TOOLS is exposed. Unknown names "
        "produce an error listing the valid set."
    ),
    "--no-color": (
        "Disable ANSI colors in REPL output. Honored implicitly when "
        "the $NO_COLOR environment variable is set; explicit flag "
        "wins over auto-detection."
    ),
    "--no-rich": (
        "Force the plain ConsoleStreamHandler even when stdout is a "
        "TTY. Default behavior auto-selects rich on TTY and plain "
        "when stdout is piped or redirected."
    ),
    "--json": (
        "When paired with -p, emit a single JSON object on stdout "
        "({output, success, model}) instead of plain text. Useful "
        "for piping into jq or downstream tools."
    ),
    "subcommand": (
        "Optional positional: 'serve' (ACP server stub), 'sessions' "
        "(list/show/cost), 'share' (export a session transcript), "
        "'agents' (registry browser), 'bench' (benchmark suites)."
    ),
    "--since": (
        "With 'sessions cost': drop sessions older than this cutoff. "
        "Accepts shorthand (7d / 24h / 30m) or an ISO-8601 date."
    ),
    "--cost-model": (
        "With 'sessions cost': case-insensitive substring filter on "
        "model name. Pass 'all' (or omit) to include every model."
    ),
    "--cost-format": (
        "With 'sessions cost': output format. Defaults to 'json' "
        "when --json is set, 'text' otherwise. CSV is also supported."
    ),
    "--cost-limit": (
        "With 'sessions cost': cap on rows considered (newest first). "
        "No cap by default; useful for fixture stability."
    ),
    "--share-sink": (
        "With 'share': destination for the rendered transcript. "
        "Defaults to 'file' (writes ~/.chimera/shares/stoat-<id>.<ext>). "
        "'stdout' streams the transcript directly."
    ),
    "--share-format": (
        "With 'share': render format. Defaults to 'json' "
        "(round-trips with 'sessions show --json'). 'md' yields "
        "a human-readable markdown transcript."
    ),
    "--all-clis": (
        "With 'sessions list': include sessions created by every "
        "Chimera CLI (otter / ferret / weasel / shrew / mink / "
        "badger), not just stoat. Adds an ORIGIN column."
    ),
}


def _resolve_version() -> str:
    """Return the stoat scaffold version for ``--version`` output.

    Returns:
        ``"0.5.0"`` (the per-CLI release line) — independent of the
        ``chimera-run`` package version. Mirrors weasel/shrew's per-CLI
        release cadence.
    """
    return _VERSION


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera stoat`` flags on ``parser``.

    Mirrors the otter/ferret/weasel ``add_arguments`` shape so embedders
    and tests can attach the same flag surface to a parser they already
    own.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the stoat
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    # A10-W11: stash for ``--help-long`` later in ``run()``.
    global _PARSER
    _PARSER = parser

    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera stoat {_resolve_version()}",
    )
    # A10-W11: top-level help-long flag. Argparse parses it like any
    # store_true; ``run()`` checks it before the routing tree.
    parser.add_argument(
        "--help-long",
        dest="help_long",
        action="store_true",
        default=False,
        help="Show full help (incl. long flag descriptions).",
    )

    core = parser.add_argument_group("Core")
    behavior = parser.add_argument_group("Behavior")
    output = parser.add_argument_group("Output")
    persistence = parser.add_argument_group("Persistence")

    # WHY: env precedence is --model > $STOAT_MODEL > _DEFAULT_MODEL.
    # Lets CI / shells pin a model once while keeping ad-hoc --model
    # overrides cheap. Mirrors weasel's $WEASEL_MODEL pattern.
    core.add_argument(
        "--model",
        default=os.environ.get("STOAT_MODEL") or _DEFAULT_MODEL,
        help=f"Model id (default: $STOAT_MODEL or {_DEFAULT_MODEL}).",
    )
    core.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        metavar="PROMPT",
        default=None,
        help="One-shot: run PROMPT, print, exit.",
    )
    core.add_argument(
        "--mode",
        choices=list(_VALID_MODES),
        default="interactive",
        help="Mode (default: interactive).",
    )
    # WHY: shell-mode toggle is the headline feature. ``--shell-mode``
    # boots the REPL already in shell mode (Ctrl-X / /shell flips back).
    behavior.add_argument(
        "--shell-mode",
        dest="shell_mode",
        action="store_true",
        default=False,
        help="Start REPL in shell mode (toggle via /shell or Ctrl-X).",
    )
    core.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: cwd).",
    )
    behavior.add_argument(
        "--max-steps",
        type=int,
        default=50,
        metavar="N",
        help="Max agent steps per turn (default: 50).",
    )
    behavior.add_argument(
        "--allowed-tools",
        default="",
        metavar="LIST",
        help="Comma tool allowlist (empty = all).",
    )
    output.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI colors (also honors $NO_COLOR).",
    )
    output.add_argument(
        "--no-rich",
        action="store_true",
        default=False,
        help="Force plain stream handler even on TTY.",
    )
    output.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="With -p: emit JSON envelope instead of text.",
    )
    # WHY: subcommand placeholders are positionals so the orchestrator
    # can route ``chimera stoat sessions list``, ``chimera stoat share
    # <id>``, etc. without re-parsing.
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help="serve | sessions | share | agents | bench.",
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        choices=list(_VALID_SUB_ACTIONS),
        metavar="ACTION",
        help="list | show | cost | <suite>.",
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Session id or agent name for show/share.",
    )
    # WHY: cost flags mirror mink/weasel/shrew so the rollup JSON/CSV/text
    # shape is byte-identical across CLIs.
    persistence.add_argument(
        "--since",
        dest="cost_since",
        default=None,
        metavar="WINDOW",
        help="sessions cost: cutoff (e.g. 7d / ISO).",
    )
    persistence.add_argument(
        "--cost-model",
        dest="cost_model",
        default=None,
        metavar="STR",
        help="sessions cost: model substring filter.",
    )
    persistence.add_argument(
        "--cost-format",
        dest="cost_format",
        choices=("text", "json", "csv"),
        default=None,
        metavar="FMT",
        help="sessions cost: text | json | csv.",
    )
    persistence.add_argument(
        "--cost-limit",
        dest="cost_limit",
        type=int,
        default=None,
        metavar="N",
        help="sessions cost: row cap (newest first).",
    )
    # Share knobs.
    persistence.add_argument(
        "--share-sink",
        dest="share_sink",
        choices=("file", "stdout"),
        default=None,
        metavar="SINK",
        help="share: file (default) | stdout.",
    )
    persistence.add_argument(
        "--share-format",
        dest="share_format",
        choices=("json", "md"),
        default=None,
        metavar="FMT",
        help="share: json (default) | md.",
    )
    # B9-W11: cross-CLI session listing.
    persistence.add_argument(
        "--all-clis",
        dest="sessions_all_clis",
        action="store_true",
        default=False,
        help="sessions list: include every Chimera CLI's sessions.",
    )


# ---------------------------------------------------------------------------
# Allowed-tools filtering — mirrors ferret/otter helper.
# ---------------------------------------------------------------------------


class _UnknownAllowedTool(ValueError):
    """Raised when ``--allowed-tools`` names a tool that doesn't exist."""


def _filter_allowed_tools(tools: list[Any], allowed: str) -> list[Any]:
    """Return *tools* filtered to the comma-separated names in *allowed*.

    Matching is case-insensitive. An unknown name raises
    :class:`_UnknownAllowedTool`.

    Args:
        tools: Source tool list (typically ``AGENT_TOOLS``).
        allowed: Raw comma-separated string from ``--allowed-tools``.

    Returns:
        New filtered list. Empty *allowed* returns *tools* unchanged.

    Raises:
        _UnknownAllowedTool: When *allowed* names a tool not in *tools*.
    """
    cleaned = (allowed or "").strip()
    if not cleaned:
        return list(tools)
    wanted = {n.strip().lower() for n in cleaned.split(",") if n.strip()}
    if not wanted:
        return list(tools)
    name_index = {t.name.lower(): t for t in tools}
    unknown = sorted(wanted - set(name_index.keys()))
    if unknown:
        valid = ", ".join(sorted(name_index.keys()))
        raise _UnknownAllowedTool(
            f"error: unknown tool '{unknown[0]}'. Valid tools: {valid}"
        )
    return [t for name, t in name_index.items() if name in wanted]


# ---------------------------------------------------------------------------
# One-shot --print path.
# ---------------------------------------------------------------------------


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single agent turn and emit results in the requested format.

    Builds a minimal :class:`Agent` directly (no MCP / LSP wiring; stoat
    keeps the print path lean), routing the provider through
    :func:`chimera.stoat.providers.build_provider` so the Kimi-first
    chain is honored.

    Args:
        args: Parsed CLI namespace.

    Returns:
        Process exit code (``0`` on success, ``1`` on agent failure,
        ``2`` on usage error, ``130`` on cancellation).
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
    from chimera.stoat.providers import build_provider

    prompt_text = getattr(args, "print_mode", None)
    if not prompt_text:
        print("stoat -p: missing PROMPT argument", file=sys.stderr)
        return 2

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())

    try:
        provider = build_provider(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"stoat: provider error: {exc}", file=sys.stderr)
        return 1

    base_env = LocalEnvironment(workdir=cwd)
    base_env.setup()

    cancel = CancellationToken()
    config = LoopConfig(cancellation=cancel)
    loop = ReAct(
        max_steps=int(getattr(args, "max_steps", 50) or 50),
        config=config,
    )
    sys_prompt = Prompt.from_string(
        "You are Stoat, a Chimera coding agent with a shell-mode toggle. "
        "Use tools to inspect and modify the user's repo. Be concise."
    )

    tools = list(AGENT_TOOLS)
    allowed = getattr(args, "allowed_tools", "") or ""
    if allowed:
        try:
            tools = _filter_allowed_tools(tools, allowed)
        except _UnknownAllowedTool as exc:
            print(str(exc), file=sys.stderr)
            base_env.cleanup()
            return 2

    agent = Agent(
        provider=provider,
        tools=tools,
        loop=loop,
        prompt=sys_prompt,
    )

    result: Any = None
    try:
        result = asyncio.run(agent.async_run(prompt_text, env=base_env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        base_env.cleanup()

    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", "") or ""

    if getattr(args, "json_output", False):
        payload = {
            "output": output,
            "success": success,
            "model": getattr(provider, "model_name", getattr(args, "model", "")),
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        if output:
            print(output)
    return 0 if success else 1


# ---------------------------------------------------------------------------
# Subcommand dispatch.
# ---------------------------------------------------------------------------


def _dispatch_serve(args: argparse.Namespace) -> int:
    """Stub for ``chimera stoat serve``.

    Stoat's serve mode mirrors a future ACP / IDE-extension transport;
    the heavy lifting is delegated to ``chimera ferret serve --http``
    (the multi-CLI HTTP/SSE server already lives there). Returning 2
    keeps shell pipelines from silently treating "not implemented" as
    success while the implementation matures.
    """
    print(
        "stoat serve: ACP / IDE-extension server is a follow-up. "
        "Use 'chimera ferret serve --http' for the cross-CLI HTTP server.",
        file=sys.stderr,
    )
    return 2


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Forward ``chimera stoat sessions [list|show|cost]`` to the sessions handler."""
    from chimera.stoat.sessions import dispatch_sessions

    return dispatch_sessions(args)


def _dispatch_share(args: argparse.Namespace) -> int:
    """Forward ``chimera stoat share <session-id>`` to the share handler."""
    from chimera.stoat.sessions import dispatch_share

    return dispatch_share(args)


def _dispatch_agents(args: argparse.Namespace) -> int:
    """Stub for ``chimera stoat agents [list|show <name>]``.

    Stoat re-uses the cross-CLI agent registry (project ``.chimera/agents/``
    plus ``~/.chimera/agents/``). The list/show wiring is filled in by
    a follow-up; today we surface a hint and exit 2 so scripts don't
    silently succeed on a missing implementation.
    """
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    print(
        f"stoat agents: action={action!r} target={target!r} "
        "(scaffold; see docs/stoat/parity-matrix.md).",
        file=sys.stderr,
    )
    return 2


def _dispatch_bench(args: argparse.Namespace) -> int:
    """Stub for ``chimera stoat bench <suite>``.

    Stoat mirrors the bench surface from ferret / shrew (humaneval +
    tau-bench). Implementation lands in a follow-up release; the
    placeholder keeps the CLI surface visible.
    """
    suite = getattr(args, "sub_action", None)
    print(
        f"stoat bench: suite={suite!r} (scaffold; see docs/stoat/parity-matrix.md).",
        file=sys.stderr,
    )
    return 2


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "serve": _dispatch_serve,
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
    "agents": _dispatch_agents,
    "bench": _dispatch_bench,
}


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


@friendly_errors
def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera stoat``.

    Routing precedence:

    1. ``subcommand in _SUBCOMMAND_DISPATCH`` — forward to the handler.
    2. ``-p PROMPT`` — one-shot print mode.
    3. ``--mode rpc`` — delegate to :mod:`chimera.cli.code` in rpc mode.
    4. default — interactive REPL via :mod:`chimera.stoat.repl`.

    Args:
        args: Parsed namespace from the stoat subparser.

    Returns:
        Process exit code.
    """
    # A10-W11: ``--help-long`` shows the standard ``--help`` output plus a
    # ``Detailed flag descriptions`` section sourced from ``_LONG_HELP``.
    if getattr(args, "help_long", False):
        from chimera.cli.help_long import print_help_long

        print_help_long(_PARSER, _LONG_HELP)
        return 0

    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    # ``-p`` always wins over --mode for CLI ergonomics parity with the
    # other Chimera coding-agent CLIs.
    if getattr(args, "print_mode", None) is not None:
        return _run_print_mode(args)

    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        # WHY: stoat doesn't ship its own RPC server — the shared
        # ``chimera code --mode rpc`` is the cross-CLI integration point
        # and stoat's loop/provider stack lights up the same wire.
        try:
            from chimera.cli.code import run_code as _run_code
        except Exception as exc:  # noqa: BLE001
            print(f"stoat: rpc transport unavailable ({exc})", file=sys.stderr)
            return 2
        # Synthesise a code-compatible namespace; we keep the model /
        # workdir / max_steps and force ``--mode rpc``.
        rpc_ns = argparse.Namespace(
            model=getattr(args, "model", None),
            workdir=getattr(args, "cwd", None) or os.getcwd(),
            max_steps=int(getattr(args, "max_steps", 50) or 50),
            mode="rpc",
            models="",
            preset=None,
            print_mode=None,
            # WHY (G3, wave 10): RPC mode pre-dates the CodingAgent default
            # flip and wires its own JSON-RPC handlers on top of the legacy
            # ReAct/Session stack. Pin legacy_react=True so the default
            # change can't regress stoat's RPC handshake.
            legacy_react=True,
        )
        return int(_run_code(rpc_ns))
    if mode == "print":
        print("stoat: --mode print requires -p PROMPT", file=sys.stderr)
        return 2

    # Interactive (default).
    from chimera.stoat.repl import run as _repl_run

    return _repl_run(args)


__all__ = [
    "add_arguments",
    "run",
]
