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

from chimera.cli.help_long import register_argument
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
# WHY (G3, w13): the cross-CLI ``--permission-mode`` 5-mode surface.
# ``read-only`` / ``suggest`` / ``auto`` / ``yolo`` / ``strict`` mirrors
# the spelling the other Chimera CLIs (ferret, mink) ship. Maps onto
# :class:`chimera.permissions.modes.ApprovalMode` and selects a preset
# :class:`chimera.permissions.base.PermissionPolicy` via ``policy_for_mode``.
_VALID_PERMISSION_MODES = ("read-only", "suggest", "auto", "yolo", "strict")

# W15-2 P2 (CLAW G14): three named discipline presets.
#
# * ``strict`` — read-only permission mode, max_steps=15, rerun-on-failure
#   on, max_reruns=2. Locks the agent to the most cautious posture so a
#   review-only run never accidentally writes.
# * ``balanced`` — suggest mode (default), max_steps=25, rerun on, two
#   reruns. Equivalent to today's defaults; explicit for documentation.
# * ``yolo`` — yolo mode, max_steps=50, rerun off. The "I'm pairing
#   loosely" preset.
#
# A profile fills *unset* slots only; explicit flags always win.
_VALID_PROFILES = ("strict", "balanced", "yolo")
_PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "strict": {
        "permission_mode": "read-only",
        "max_steps": 15,
        "rerun_on_failure": True,
        "max_reruns": 2,
    },
    "balanced": {
        "permission_mode": "suggest",
        "max_steps": 25,
        "rerun_on_failure": True,
        "max_reruns": 2,
    },
    "yolo": {
        "permission_mode": "yolo",
        "max_steps": 50,
        "rerun_on_failure": False,
        "max_reruns": 0,
    },
}

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
    "--permission-mode": (
        "5-mode approval surface (cross-CLI standard). 'read-only' "
        "denies all writes; 'suggest' allows reads and asks for "
        "writes/shell; 'auto' allows reads + edits and asks for "
        "shell; 'yolo' approves everything; 'strict' asks for every "
        "tool call (including reads). Default: suggest."
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
    # W14-9: routed through ``register_argument`` so future verbose
    # ``help=`` strings auto-promote to ``_LONG_HELP`` and never blow
    # past the 50-line ceiling on ``chimera badger --help``.
    register_argument(
        core,
        "--model",
        default=os.environ.get("BADGER_MODEL") or _DEFAULT_MODEL,
        metavar="MODEL",
        long_help=_LONG_HELP,
        help=f"Model id (default: ${{BADGER_MODEL}}|{_DEFAULT_MODEL}).",
    )
    register_argument(
        core,
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        metavar="PROMPT",
        long_help=_LONG_HELP,
        help="One-shot: run PROMPT, print, exit.",
    )
    register_argument(
        output,
        "--output-format",
        choices=list(_VALID_OUTPUT_FORMATS),
        default="text",
        metavar="FMT",
        long_help=_LONG_HELP,
        help="text | json | stream-json (default: text).",
    )
    # WHY: tighter default than the other CLIs. Harness-rewrite posture
    # prefers rerun discipline over a long single trajectory.
    register_argument(
        behavior,
        "--max-steps",
        type=int,
        default=_DEFAULT_MAX_STEPS,
        metavar="N",
        long_help=_LONG_HELP,
        help=f"Max agent steps per turn (default: {_DEFAULT_MAX_STEPS}).",
    )
    register_argument(
        core,
        "--cwd",
        default=None,
        long_help=_LONG_HELP,
        help="Working directory (default: cwd).",
    )
    register_argument(
        behavior,
        "--allowed-tools",
        default="",
        metavar="LIST",
        long_help=_LONG_HELP,
        help="Comma tool allowlist (empty = all).",
    )
    # WHY: rerun-on-failure is the load-bearing distinction. When set,
    # the agent's first attempt is checked for tell-tale failure markers
    # (test failures, syntax errors). On hit, we reset and retry with a
    # refined prompt up to --max-reruns extra attempts.
    register_argument(
        behavior,
        "--rerun-on-failure",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="Retry on test/syntax failure (uses --max-reruns).",
    )
    register_argument(
        behavior,
        "--max-reruns",
        type=int,
        default=2,
        metavar="N",
        long_help=_LONG_HELP,
        help="Extra attempts when --rerun-on-failure (default: 2).",
    )
    # WHY (G3, w13): cross-CLI ``--permission-mode`` 5-mode surface.
    # Default = ``suggest`` (allow reads, ask writes) — matches the
    # harness-rewrite "show your work" posture without locking the
    # agent out of side effects entirely.
    register_argument(
        behavior,
        "--permission-mode",
        dest="permission_mode",
        choices=list(_VALID_PERMISSION_MODES),
        default="suggest",
        metavar="MODE",
        long_help=_LONG_HELP,
        help="5-mode approval (default: suggest).",
    )
    # W15-2 P2 (CLAW G14): named profiles bundle permission-mode +
    # max-steps + rerun-on-failure + auto-approve so users can pick
    # one term ("strict" / "balanced" / "yolo") instead of three flags.
    # Profile values fill *unset* flags only — explicit flags always
    # win, so ``--profile strict --max-steps 50`` keeps max-steps=50.
    register_argument(
        behavior,
        "--profile",
        dest="profile",
        choices=list(_VALID_PROFILES),
        default=None,
        metavar="PROFILE",
        long_help=_LONG_HELP,
        help="discipline preset (strict | balanced | yolo).",
    )
    register_argument(
        output,
        "--no-rich",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="Force plain stream handler even on TTY.",
    )
    register_argument(
        output,
        "--no-color",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="Synonym for --no-rich (also honors $NO_COLOR).",
    )
    register_argument(
        persistence,
        "--no-save",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="Don't persist the one-shot run to eventlog.",
    )
    register_argument(
        persistence,
        "--run-id",
        default=None,
        metavar="ID",
        long_help=_LONG_HELP,
        help="Override auto-generated run id for the eventlog dir.",
    )
    # WHY (parity): the parity subcommand diffs current agent behaviour
    # against a declared schema. The schema lives at ``--against``.
    register_argument(
        serve_grp,
        "--against",
        dest="parity_against",
        default=None,
        metavar="PATH",
        long_help=_LONG_HELP,
        help="parity: path to the parity schema (JSON/YAML).",
    )
    register_argument(
        serve_grp,
        "--http",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="serve: run HTTP server (default for badger).",
    )
    register_argument(
        serve_grp,
        "--host",
        default=None,
        metavar="HOST",
        long_help=_LONG_HELP,
        help="serve --http: bind host (default: 127.0.0.1).",
    )
    register_argument(
        serve_grp,
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        long_help=_LONG_HELP,
        help="serve --http: bind port (default: 5176).",
    )
    register_argument(
        serve_grp,
        "--auth-token",
        default=None,
        metavar="TOKEN",
        long_help=_LONG_HELP,
        help="serve --http: bearer token required on requests.",
    )
    register_argument(
        parser,
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        long_help=_LONG_HELP,
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
    register_argument(
        persistence,
        "--all-clis",
        dest="sessions_all_clis",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
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
# Permission-mode resolution (G3, w13)
# ---------------------------------------------------------------------------


def _resolve_badger_permissions(args: argparse.Namespace) -> Any:
    """Resolve badger's permission policy from ``--permission-mode``.

    badger only exposes the 5-mode standard surface (no legacy
    ``--approval`` flag). Routes through
    :func:`chimera.permissions.modes.policy_for_mode` so the live
    :class:`~chimera.permissions.base.PermissionPolicy` matches what
    ferret and mink produce for the same flag value.

    A malformed mode degrades to ``None`` (default LoopConfig) with a
    stderr warning rather than crashing the runner.

    Args:
        args: Parsed badger argparse namespace.

    Returns:
        A live :class:`PermissionPolicy`, or ``None`` if the resolver
        fell through to the warning path.
    """
    from chimera.permissions.modes import (
        ApprovalMode,
        parse_mode,
        policy_for_mode,
    )

    raw = getattr(args, "permission_mode", None) or ApprovalMode.SUGGEST.value
    try:
        return policy_for_mode(parse_mode(str(raw)))
    except ValueError as exc:
        print(
            f"[badger] --permission-mode {raw!r} unrecognised ({exc}); "
            "falling back to default policy.",
            file=sys.stderr,
        )
        return None


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
    """``chimera badger bench <suite>`` — delegate to the canonical harness.

    Frontends do not reimplement evaluation; this hands off to the one
    ``bench-matrix`` runner. ``bench`` with no suite lists the benchmarks.
    """
    from chimera.cli.codename_bench import dispatch_codename_bench

    return dispatch_codename_bench(args, "badger")


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
    # WHY (G3, w13): wire ``--permission-mode`` into LoopConfig.permissions
    # so the badger one-shot path honours the standard 5-mode surface.
    permissions = _resolve_badger_permissions(args)
    config = LoopConfig(cancellation=cancel, permissions=permissions)
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


def apply_profile(args: argparse.Namespace) -> argparse.Namespace:
    """Fold ``--profile`` defaults onto *args* in-place (W15-2 P2 / CLAW G14).

    A profile fills only flags that the operator left at their default
    values. Detection is per-key:

    * ``permission_mode``: default is ``"suggest"``; non-``suggest`` value
      is treated as explicit.
    * ``max_steps``: default is :data:`_DEFAULT_MAX_STEPS`; any other
      value is treated as explicit.
    * ``rerun_on_failure`` / ``max_reruns``: ``False`` / ``0`` is the
      default — the profile fills only when those defaults are seen.

    The function is a no-op when ``args.profile`` is unset, so adding
    the call to ``run`` is safe for existing test fixtures.
    """
    profile = getattr(args, "profile", None)
    if not profile:
        return args
    defaults = _PROFILE_DEFAULTS.get(profile)
    if defaults is None:
        return args
    if getattr(args, "permission_mode", "suggest") == "suggest":
        args.permission_mode = defaults["permission_mode"]
    if getattr(args, "max_steps", _DEFAULT_MAX_STEPS) == _DEFAULT_MAX_STEPS:
        args.max_steps = defaults["max_steps"]
    if not getattr(args, "rerun_on_failure", False):
        args.rerun_on_failure = bool(defaults["rerun_on_failure"])
    if not getattr(args, "max_reruns", 0):
        max_reruns_default = defaults["max_reruns"]
        args.max_reruns = int(max_reruns_default) if isinstance(
            max_reruns_default, (int, float, str)
        ) else 0
    return args


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

    # W15-2 P2 (CLAW G14): apply --profile defaults before dispatch.
    apply_profile(args)

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
    "apply_profile",
    "run",
]
