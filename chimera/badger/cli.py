"""``chimera badger`` — Badger, a Chimera coding agent in the harness-rewrite tradition.

Badger is the seventh Chimera coding-agent CLI. It mirrors a harness-rewrite
posture: focused tool surface, tight max-step budgeting, and rerun-on-failure
discipline as first-class concerns. The companion ``parity`` subcommand
diffs the current agent's behaviour against a declared schema so operators
can detect drift before it ships.

Conventions follow ``chimera/ferret/cli.py`` closely so users moving between
``chimera ferret`` and ``chimera badger`` pay no surprise tax.

Trademark hygiene: this module never names the upstream by brand. Comparative
language uses "badger", "the upstream", or "the harness-rewrite tradition".
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from chimera.errors import friendly_errors

# WHY: only stdlib + chimera at import time. Heavy provider deps load
# lazily inside _build_provider so ``--help`` / ``--version`` stay cheap.

_DEFAULT_MODEL = "claude-sonnet-4-6"
"""Default model when neither ``--model`` nor ``$BADGER_MODEL`` is set.

WHY: badger inherits an Anthropic-first general-purpose chain. The
provider resolver in :mod:`chimera.badger.providers` honours the chain
(Anthropic -> OpenAI -> OpenRouter -> Ollama).
"""

# Tighter ceiling than ferret/otter (50). Mirrors the harness-rewrite
# posture: prefer rerun-on-failure over runaway loops.
_DEFAULT_MAX_STEPS = 25

_VALID_OUTPUT_FORMATS = ("text", "json", "stream-json")
_VALID_SUBCOMMANDS = (
    None,
    "serve",
    "sessions",
    "share",
    "agents",
    "bench",
    "parity",
)
_VALID_SUB_ACTIONS = (None, "list", "show", "humaneval", "tau-bench")

# A10-W11: parser ref for ``--help-long`` rendering and the per-flag
# long-form descriptions printed below the standard help.
_PARSER: argparse.ArgumentParser | None = None
_LONG_HELP: dict[str, str] = {
    "--model": (
        "Model identifier. Resolution order: --model > $BADGER_MODEL > "
        f"the {_DEFAULT_MODEL} default. Routed through "
        "chimera.badger.providers.build_provider with fallback to "
        "chimera.providers.factory.create_provider."
    ),
    "-p / --print": (
        "One-shot print mode: run a single agent turn against PROMPT, "
        "emit the assistant text on stdout, then exit. Pairs with "
        "--output-format json for a structured envelope."
    ),
    "--output-format": (
        "One-shot output format. 'text' (default) prints the assistant "
        "reply; 'json' emits a single result object on exit; "
        "'stream-json' prints one JSON line per LoopEvent."
    ),
    "--max-steps": (
        f"Maximum agent steps per turn (default: {_DEFAULT_MAX_STEPS}). "
        "Tighter than the other Chimera CLIs by design — pair with "
        "--rerun-on-failure to recover from short-trajectory failures."
    ),
    "--cwd": (
        "Working directory for the agent run. Default: process cwd. "
        "Resolved to an absolute path before the env is built."
    ),
    "--allowed-tools": (
        "Comma-separated tool names to allow (case-insensitive). Empty "
        "= every tool. The harness-rewrite default is the full set; "
        "restrict here for sandbox-like discipline."
    ),
    "--rerun-on-failure": (
        "When the agent's first attempt shows tell-tale failure markers "
        "(test failures, syntax errors), reset and retry with a refined "
        "prompt up to --max-reruns extra attempts."
    ),
    "--max-reruns": (
        "Maximum extra attempts when --rerun-on-failure is set "
        "(default: 2). Total attempts = 1 + max-reruns."
    ),
    "--no-rich": (
        "Force the plain ConsoleStreamHandler even when stdout is a TTY. "
        "Default behavior auto-selects rich on TTY and plain when "
        "stdout is piped."
    ),
    "--no-color": (
        "Synonym for --no-rich. Also honored implicitly when the "
        "$NO_COLOR environment variable is set."
    ),
    "--no-save": (
        "Do not persist the one-shot run to ~/.chimera/eventlog/. "
        "Default behavior saves the full message + tool history so "
        "the run can be resumed later."
    ),
    "--run-id": (
        "Override the auto-generated run id for the persisted eventlog "
        "directory. Useful for reproducible test fixtures."
    ),
    "--against": (
        "With 'parity': path to the parity schema file (JSON or YAML). "
        "Defaults to PARITY.md / PARITY.json under the current "
        "directory if neither --against nor a positional schema is "
        "supplied."
    ),
    "--http": (
        "With 'serve': run the HTTP server (default for badger). "
        "Pass --no-http to switch to ACP."
    ),
    "--host": (
        "With 'serve --http': bind host (default: 127.0.0.1). "
        "Use 0.0.0.0 only with --auth-token."
    ),
    "--port": (
        "With 'serve --http': bind port (default: 5176)."
    ),
    "--auth-token": (
        "With 'serve --http': shared-secret bearer token required on "
        "every request except /healthz."
    ),
    "subcommand": (
        "Optional positional: 'serve' (HTTP/ACP), 'sessions' (list/"
        "show), 'share' (export a session), 'agents' (list/show), "
        "'bench' (benchmark suites), 'parity' (parity-schema check)."
    ),
    "--all-clis": (
        "With 'sessions list': include sessions created by every "
        "Chimera CLI (otter / ferret / weasel / shrew / stoat / mink), "
        "not just badger. Adds an ORIGIN column."
    ),
}


def _resolve_version() -> str:
    """Resolve the chimera package version for ``--version`` output.

    Mirrors the sibling ``_resolve_version`` helpers so all coding-agent
    CLIs print the same semver under the same install.

    Returns:
        A version string, or ``"unknown"`` when neither source is reachable.
    """
    try:
        from chimera import __version__ as _v

        return str(_v)
    except Exception:  # noqa: BLE001
        try:
            from importlib.metadata import version as _meta_version

            return str(_meta_version("chimera-run"))
        except Exception:  # noqa: BLE001
            return "unknown"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera badger`` flags on ``parser``.

    Mirrors ferret's ``add_arguments`` shape so embedders / tests can
    attach the same flag surface to a parser they already own. Adds the
    badger-specific knobs: ``--rerun-on-failure`` and ``--max-reruns``.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the badger
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    # A10-W11: stash for ``--help-long`` rendering in ``run()``.
    global _PARSER
    _PARSER = parser

    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera badger {_resolve_version()}",
    )
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
    serve_grp = parser.add_argument_group("Serve / Parity")

    # WHY: env precedence is --model > $BADGER_MODEL > _DEFAULT_MODEL.
    core.add_argument(
        "--model",
        default=os.environ.get("BADGER_MODEL") or _DEFAULT_MODEL,
        metavar="MODEL",
        help=f"Model id (default: $BADGER_MODEL or {_DEFAULT_MODEL}).",
    )
    core.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        metavar="PROMPT",
        help="One-shot: run PROMPT, print, exit.",
    )
    output.add_argument(
        "--output-format",
        choices=list(_VALID_OUTPUT_FORMATS),
        default="text",
        metavar="FMT",
        help="text | json | stream-json (default: text).",
    )
    # WHY: tighter default than the other CLIs. Harness-rewrite posture
    # prefers rerun discipline over a long single trajectory.
    behavior.add_argument(
        "--max-steps",
        type=int,
        default=_DEFAULT_MAX_STEPS,
        metavar="N",
        help=f"Max agent steps per turn (default: {_DEFAULT_MAX_STEPS}).",
    )
    core.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: cwd).",
    )
    behavior.add_argument(
        "--allowed-tools",
        default="",
        metavar="LIST",
        help="Comma tool allowlist (empty = all).",
    )
    # WHY: rerun-on-failure is the load-bearing distinction. When set,
    # the agent's first attempt is checked for tell-tale failure markers
    # (test failures, syntax errors). On hit, we reset and retry with a
    # refined prompt up to --max-reruns extra attempts.
    behavior.add_argument(
        "--rerun-on-failure",
        action="store_true",
        default=False,
        help="Retry on test/syntax failure (uses --max-reruns).",
    )
    behavior.add_argument(
        "--max-reruns",
        type=int,
        default=2,
        metavar="N",
        help="Extra attempts when --rerun-on-failure (default: 2).",
    )
    output.add_argument(
        "--no-rich",
        action="store_true",
        default=False,
        help="Force plain stream handler even on TTY.",
    )
    output.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Synonym for --no-rich (also honors $NO_COLOR).",
    )
    persistence.add_argument(
        "--no-save",
        action="store_true",
        default=False,
        help="Don't persist the one-shot run to eventlog.",
    )
    persistence.add_argument(
        "--run-id",
        default=None,
        metavar="ID",
        help="Override auto-generated run id for the eventlog dir.",
    )
    # WHY (parity): the parity subcommand diffs current agent behaviour
    # against a declared schema. The schema lives at ``--against``.
    serve_grp.add_argument(
        "--against",
        dest="parity_against",
        default=None,
        metavar="PATH",
        help="parity: path to the parity schema (JSON/YAML).",
    )
    serve_grp.add_argument(
        "--http",
        action="store_true",
        default=False,
        help="serve: run HTTP server (default for badger).",
    )
    serve_grp.add_argument(
        "--host",
        default=None,
        metavar="HOST",
        help="serve --http: bind host (default: 127.0.0.1).",
    )
    serve_grp.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="serve --http: bind port (default: 5176).",
    )
    serve_grp.add_argument(
        "--auth-token",
        default=None,
        metavar="TOKEN",
        help="serve --http: bearer token required on requests.",
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help="serve | sessions | share | agents | bench | parity.",
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        choices=list(_VALID_SUB_ACTIONS),
        metavar="ACTION",
        help="list | show | <suite>.",
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Run/session id for show/share.",
    )
    # B9-W11: cross-CLI session listing. ``--all-clis`` drops the
    # ``badger-`` prefix filter from ``sessions list`` and ``sessions
    # show`` so sessions persisted by ``chimera otter``, ``chimera
    # ferret``, ``chimera weasel``, ``chimera shrew``, ``chimera stoat``,
    # and ``chimera mink`` are also visible.
    persistence.add_argument(
        "--all-clis",
        dest="sessions_all_clis",
        action="store_true",
        default=False,
        help="sessions list: include every Chimera CLI's sessions.",
    )


# ---------------------------------------------------------------------------
# Allowed-tools filtering — shared shape with ferret.
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
        A new filtered list. Empty *allowed* returns *tools* unchanged.

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
# Provider construction
# ---------------------------------------------------------------------------


def _build_provider(args: argparse.Namespace) -> Any:
    """Construct a Provider via the badger resolver, with safe fallback.

    Args:
        args: Parsed namespace; reads ``args.model``.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.
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

    return create_provider(model=getattr(args, "model", None) or _DEFAULT_MODEL)


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------


def _dispatch_serve(args: argparse.Namespace) -> int:
    """Stub for ``chimera badger serve``.

    The shared ``chimera otter`` HTTP server is the canonical transport;
    badger reuses that infrastructure. For the wave-9 scaffold the
    dispatcher emits a friendly message pointing operators at the otter
    server until a badger-flavoured factory lands.
    """
    print(
        "badger serve: not yet wired in this scaffold. Use "
        "'chimera otter serve --http' for the canonical server.",
        file=sys.stderr,
    )
    return 2


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Wire ``chimera badger sessions [list|show <id>]``."""
    try:
        from chimera.badger.sessions import dispatch_sessions
    except Exception as exc:  # noqa: BLE001
        print(
            f"badger sessions: handler unavailable ({exc})", file=sys.stderr,
        )
        return 2

    args.sessions_command = "sessions"
    args.sessions_action = getattr(args, "sub_action", None) or "list"
    args.sessions_target = getattr(args, "sub_target", None)
    args.sessions_since = getattr(args, "sessions_since", None)
    args.sessions_model = getattr(args, "sessions_model", None)
    args.sessions_limit = getattr(args, "sessions_limit", 50)
    args.sessions_json = getattr(args, "sessions_json", False)
    # B9-W11: ``--all-clis`` drops the ``badger-`` prefix filter so
    # sessions list / show can see every CLI's eventlog.
    args.sessions_all_clis = getattr(args, "sessions_all_clis", False)
    if not hasattr(args, "full"):
        args.full = True
    rc = dispatch_sessions(args)
    return rc if rc is not None else 0


def _dispatch_share(args: argparse.Namespace) -> int:
    """Dispatch ``chimera badger share <session>``."""
    try:
        from chimera.badger.sessions import cmd_session_share
    except Exception as exc:  # noqa: BLE001
        print(f"badger share: handler unavailable ({exc})", file=sys.stderr)
        return 2

    args.sessions_target = getattr(args, "sub_action", None)
    return int(cmd_session_share(args))


def _dispatch_agents(args: argparse.Namespace) -> int:
    """Stub for ``chimera badger agents [list|show <name>]``."""
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    print(
        f"badger agents: action={action!r} target={target!r} "
        "(scaffold; reuse 'chimera mink agents' for the canonical view).",
        file=sys.stderr,
    )
    return 2


def _dispatch_bench(args: argparse.Namespace) -> int:
    """Stub for ``chimera badger bench <suite>``."""
    suite = getattr(args, "sub_action", None)
    print(
        f"badger bench: suite={suite!r} (scaffold; reuse "
        "'chimera bench' for the canonical harness).",
        file=sys.stderr,
    )
    return 2


def _dispatch_parity(args: argparse.Namespace) -> int:
    """Run ``chimera badger parity --against <schema>``.

    Routes through :func:`chimera.badger.parity.run_parity_check`.
    Returns rc=0 when the live agent matches the schema, rc=1 with a
    diff report otherwise, rc=2 on usage error (missing schema, parse
    error, etc.).
    """
    try:
        from chimera.badger.parity import run_parity_check
    except Exception as exc:  # noqa: BLE001
        print(f"badger parity: handler unavailable ({exc})", file=sys.stderr)
        return 2
    return int(run_parity_check(args))


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "serve": _dispatch_serve,
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
    "agents": _dispatch_agents,
    "bench": _dispatch_bench,
    "parity": _dispatch_parity,
}


# ---------------------------------------------------------------------------
# One-shot --print path with rerun-on-failure wiring.
# ---------------------------------------------------------------------------


def _run_print_mode(args: argparse.Namespace) -> int:
    """Run ``chimera badger -p PROMPT``.

    Wires the harness-rewrite default tool surface, max-step budget, and
    optional rerun-on-failure loop. Late-binds every sibling import so
    an absent module degrades to a sensible default.

    Args:
        args: Parsed namespace; reads ``print_mode``, ``model``, ``cwd``,
            ``max_steps``, ``output_format``, ``rerun_on_failure``,
            ``max_reruns``, ``allowed_tools``.

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
        print("badger -p: missing PROMPT argument", file=sys.stderr)
        return 2

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    output_format = getattr(args, "output_format", "text") or "text"

    # Provider resolution.
    try:
        provider = _build_provider(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    base_env = LocalEnvironment(workdir=cwd)
    base_env.setup()

    cancel = CancellationToken()
    config = LoopConfig(cancellation=cancel)
    loop = ReAct(
        max_steps=int(getattr(args, "max_steps", _DEFAULT_MAX_STEPS) or _DEFAULT_MAX_STEPS),
        config=config,
    )
    base_prompt = (
        "You are Badger, a Chimera coding agent in the harness-rewrite "
        "tradition. Plan briefly, act with a tight tool budget, and "
        "verify before declaring success."
    )
    chimera_prompt = Prompt.from_string(base_prompt)
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
        prompt=chimera_prompt,
    )

    try:
        if getattr(args, "rerun_on_failure", False):
            from chimera.badger.rerun import run_with_rerun

            result = asyncio.run(
                run_with_rerun(
                    agent,
                    prompt_text,
                    env=base_env,
                    max_reruns=int(getattr(args, "max_reruns", 2) or 0),
                )
            )
        else:
            result = asyncio.run(agent.async_run(prompt_text, env=base_env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        base_env.cleanup()

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
# Entry point
# ---------------------------------------------------------------------------


@friendly_errors
def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera badger``.

    Args:
        args: Parsed ``argparse.Namespace`` from the badger subparser.

    Returns:
        Process exit code (``0`` on success).
    """
    # A10-W11: ``--help-long`` prints the standard help plus per-flag long
    # descriptions and exits. Checked before subcommand dispatch so users
    # don't need to know the routing tree.
    if getattr(args, "help_long", False):
        from chimera.cli.help_long import print_help_long

        print_help_long(_PARSER, _LONG_HELP)
        return 0

    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    if getattr(args, "print_mode", None) is not None:
        try:
            return int(_run_print_mode(args))
        except Exception as exc:  # noqa: BLE001
            print(
                f"badger -p: one-shot path failed ({exc}).",
                file=sys.stderr,
            )
            return 2

    # Interactive REPL.
    try:
        from chimera.badger.repl import run_badger_repl

        return int(run_badger_repl(args))
    except Exception as exc:  # noqa: BLE001
        print(
            "badger: interactive REPL not yet wired in this scaffold "
            f"({exc}). Use --print/-p PROMPT for one-shot mode, "
            "--version for version, or --help for the full flag list.",
            file=sys.stderr,
        )
        return 2


__all__ = [
    "add_arguments",
    "run",
]
