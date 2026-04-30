"""``chimera ferret`` — Ferret, a Chimera coding agent in the IDE-first tradition.

Ferret is the third Chimera coding-agent CLI, paralleling :mod:`chimera.mink`
and :mod:`chimera.otter`. Where mink mirrors a TUI-first ergonomic and otter
mirrors a server-first / multi-client posture, ferret mirrors a sandbox-first /
IDE-first / OpenAI-flagship coding agent (the upstream reference).

This module ships the **scaffold**: a working ``add_arguments`` / ``run``
pair so ``chimera ferret --version`` and ``chimera ferret -p "..."`` route
through. Subcommand placeholders (``serve`` / ``sessions`` / ``share`` /
``agents`` / ``bench``) are recognised and dispatched to stub handlers;
sibling agents in the wave fill in the bodies (sandbox, approval, IDE, cloud
bridge, providers).

Conventions follow ``chimera/otter/cli.py`` closely so users moving between
``chimera otter`` and ``chimera ferret`` pay no surprise tax.

Trademark hygiene: this module never names the upstream IDE-first OpenAI-
flagship coding agent in source/docs/help text. ``~/.codex/config.toml`` is
referenced as a filesystem path (a fact, not a brand claim).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# WHY: only stdlib + chimera at import time. Provider deps (httpx, anthropic,
# openai SDKs) are pulled in lazily inside ``_build_provider`` so importing
# ``chimera.ferret.cli`` for ``--help`` / ``--version`` stays cheap. The
# sandbox / approval / IDE / cloud-bridge / providers siblings are similarly
# late-bound so this file loads even when the rest of the wave hasn't landed.

_DEFAULT_MODEL = "gpt-5"
"""Default model when neither ``--model`` nor ``$FERRET_MODEL`` is set.

WHY: the upstream IDE-first OpenAI-flagship coding agent's provider chain
prefers OpenAI's flagship model. We name it ``gpt-5`` per the spec's
provider chain (see ``research/ferret/SPEC.md``). When the OpenAI SDK
isn't configured, the downstream :mod:`chimera.ferret.providers` resolver
(FF6) falls through to Anthropic / OpenRouter; until that lands, the
provider factory raises a friendly "no API key" error.
"""

_VALID_OUTPUT_FORMATS = ("text", "json", "stream-json")
_VALID_SUBCOMMANDS = (None, "serve", "sessions", "share", "agents", "bench")
_VALID_SUB_ACTIONS = (None, "list", "show", "humaneval", "tau-bench")
_VALID_SANDBOX_MODES = (
    "read-only",
    "workspace-write",
    "workspace-write-network",
)
_VALID_APPROVAL_PRESETS = ("read-only", "auto", "full")


def _resolve_version() -> str:
    """Resolve the chimera package version for ``--version`` output.

    Mirrors :func:`chimera.otter.cli._resolve_version` so otter and ferret
    print the same semver under the same install.

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
    """Register ``chimera ferret`` flags on ``parser``.

    Mirrors otter's ``add_arguments`` shape so embedders / tests can attach
    the same flag surface to a parser they already own. Adds ferret-only
    flags ``--sandbox`` and ``--approval`` (FF2 + FF3) and the ferret
    config knob ``--config`` (FF1) on top of the shared otter set.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the ferret
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera ferret {_resolve_version()}",
    )
    # WHY: env precedence is --model > $FERRET_MODEL > _DEFAULT_MODEL. Lets
    # CI / shells pin a model once while keeping ad-hoc --model overrides
    # cheap. Mirrors otter's $OTTER_MODEL pattern.
    parser.add_argument(
        "--model",
        default=os.environ.get("FERRET_MODEL") or _DEFAULT_MODEL,
        help=(
            "Model identifier (default: $FERRET_MODEL or "
            f"{_DEFAULT_MODEL}). Resolved through "
            "``chimera.ferret.providers.build_provider`` (FF6) "
            "with fallback to ``chimera.providers.factory.create_provider``."
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
        "--output-format",
        choices=list(_VALID_OUTPUT_FORMATS),
        default="text",
        help=(
            "One-shot output format. 'stream-json' prints one JSON line per "
            "LoopEvent; 'json' prints a single result object on exit."
        ),
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum agent steps per turn (default: 50).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: current directory).",
    )
    parser.add_argument(
        "--allowed-tools",
        default="",
        help=(
            "Comma-separated tool names to allow (case-insensitive). "
            "Empty = all tools."
        ),
    )
    parser.add_argument(
        "--no-rich",
        action="store_true",
        default=False,
        help=(
            "Force the plain ConsoleStreamHandler even when stdout is a TTY. "
            "Default: auto-select rich on TTY, plain when piped."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help=(
            "Synonym for --no-rich. Also honored implicitly when the "
            "$NO_COLOR environment variable is set."
        ),
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        default=False,
        help=(
            "Do not persist the one-shot run to ~/.chimera/eventlog/. "
            "Default behavior saves the full message + tool history."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Override the auto-generated run id for the persisted "
            "eventlog directory. Useful for reproducible test fixtures."
        ),
    )
    # WHY (FF2): sandbox-first execution. The default is the safest mode
    # (read-only); opting up requires explicit selection. Sibling FF2 owns
    # the runner that consumes this flag.
    parser.add_argument(
        "--sandbox",
        choices=list(_VALID_SANDBOX_MODES),
        default="read-only",
        help=(
            "Sandbox mode for shell-style tools (default: read-only). "
            "'workspace-write' allows writes inside the project; "
            "'workspace-write-network' adds outbound network access."
        ),
    )
    # WHY (FF3): approval preset is a single-flag selection that maps to a
    # full permission policy (vs otter's fine-grained --allowed-tools).
    parser.add_argument(
        "--approval",
        choices=list(_VALID_APPROVAL_PRESETS),
        default="read-only",
        help=(
            "Approval preset (default: read-only). 'auto' approves "
            "low-risk tools and prompts for high-risk; 'full' approves "
            "all tool calls without prompting."
        ),
    )
    # WHY (FF1): the upstream IDE-first OpenAI-flagship coding agent ships
    # a TOML config at ``~/.codex/config.toml`` plus an optional project
    # ``.codex/config.toml``. Allow an explicit override path for tests
    # and one-off invocations.
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help=(
            "Override the ferret config file path. Default: merge "
            "~/.codex/config.toml with project ./.codex/config.toml."
        ),
    )
    # WHY (FF4): ACP is the *default* serve transport (IDE-first ergonomic);
    # HTTP requires the opt-in --http flag. This inverts otter's --acp gate.
    parser.add_argument(
        "--http",
        action="store_true",
        default=False,
        help=(
            "With 'serve': run the HTTP server instead of the default "
            "ACP (Agent Client Protocol) JSON-RPC server on stdio."
        ),
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "With 'serve --http': bind host (default: 127.0.0.1). "
            "Use 0.0.0.0 only with --auth-token."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="With 'serve --http': bind port (default: 5174).",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help=(
            "With 'serve --http': shared-secret bearer token required "
            "on every request except /healthz."
        ),
    )
    # WHY: subcommand placeholders are positionals so the orchestrator can
    # route ``chimera ferret serve``, ``chimera ferret sessions list``, etc.
    # without re-parsing. Sibling agents in the wave own the bodies.
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help=(
            "Optional: 'serve' (ACP/HTTP server), 'sessions' (list/show), "
            "'share' (share a session), 'agents' (list/show), 'bench' "
            "(benchmark suites)."
        ),
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        choices=list(_VALID_SUB_ACTIONS),
        metavar="ACTION",
        help="With 'sessions' or 'agents': 'list' or 'show <name>'.",
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Run/session id consumed by 'show' or 'share' actions.",
    )


# ---------------------------------------------------------------------------
# Allowed-tools filtering — mirrors otter's helper
# ---------------------------------------------------------------------------


class _UnknownAllowedTool(ValueError):
    """Raised when ``--allowed-tools`` names a tool that doesn't exist.

    Carrying the formatted error message on the exception keeps callers
    free of presentation logic — they ``print(exc)`` and exit 2.
    """


def _filter_allowed_tools(tools: list[Any], allowed: str) -> list[Any]:
    """Return *tools* filtered to the comma-separated names in *allowed*.

    Matching is case-insensitive so frontmatter-style ``Bash,Read`` matches
    the canonical lower-case ``BashTool.name``. An unknown name raises
    :class:`_UnknownAllowedTool`.

    Args:
        tools: Source tool list (typically ``AGENT_TOOLS``).
        allowed: Raw comma-separated string from ``--allowed-tools``.
            Empty / whitespace-only entries are ignored.

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
# Provider construction
# ---------------------------------------------------------------------------


def _build_provider(model: str) -> Any:
    """Construct a Provider for *model* via the ferret resolver.

    Late-binds :mod:`chimera.ferret.providers` (FF6) so the one-shot path,
    REPL, and serve paths share one routing logic. When FF6 hasn't landed
    yet, fall through to :func:`chimera.providers.factory.create_provider`
    so the scaffold remains usable end-to-end during the parallel build.

    Args:
        model: Model identifier (e.g. ``gpt-5``, ``gpt-4o``,
            ``claude-sonnet-4-6``, ``openai/gpt-5`` for OpenRouter).

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.
    """
    try:
        from chimera.ferret import providers as _ferret_providers  # type: ignore[attr-defined]
    except ImportError:
        _ferret_providers = None  # type: ignore[assignment]

    if _ferret_providers is not None and hasattr(
        _ferret_providers, "build_provider"
    ):
        return _ferret_providers.build_provider(argparse.Namespace(model=model))

    from chimera.providers.factory import create_provider

    return create_provider(model=model)


# ---------------------------------------------------------------------------
# Subcommand dispatch — placeholders filled in by sibling agents in the wave
# ---------------------------------------------------------------------------


def _dispatch_serve(args: argparse.Namespace) -> int:
    """Dispatch ``chimera ferret serve`` to ACP (default) or HTTP (``--http``).

    FF4 owns the IDE-first ACP schema; the HTTP variant is opt-in. Until
    the sibling modules land, return 2 with a stub message so shell
    pipelines don't silently treat an unimplemented command as success.
    """
    transport = "http" if getattr(args, "http", False) else "acp"
    print(
        f"ferret serve: transport={transport} (scaffold; see "
        "research/ferret/SPEC.md, agents FF4 / FF5).",
        file=sys.stderr,
    )
    return 2


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Wire ``chimera ferret sessions [list|show <id>]`` to FF1's handler.

    The wave-1 scaffold parser puts the sessions sub-action under
    ``args.sub_action`` and the optional id under ``args.sub_target``.
    :func:`chimera.ferret.sessions.dispatch_sessions` expects
    ``args.sessions_command="sessions"`` plus per-action filter dests.
    Read raw attributes off the namespace, fall back to sensible
    defaults, and forward.
    """
    try:
        from chimera.ferret.sessions import dispatch_sessions
    except Exception as exc:  # noqa: BLE001
        print(
            f"ferret sessions: handler unavailable ({exc})", file=sys.stderr,
        )
        return 2

    args.sessions_command = "sessions"
    args.sessions_action = getattr(args, "sub_action", None) or "list"
    args.sessions_target = getattr(args, "sub_target", None)
    args.sessions_since = getattr(args, "sessions_since", None)
    args.sessions_model = getattr(args, "sessions_model", None)
    args.sessions_limit = getattr(args, "sessions_limit", 50)
    args.sessions_json = getattr(args, "sessions_json", False)
    if not hasattr(args, "full"):
        args.full = True
    rc = dispatch_sessions(args)
    return rc if rc is not None else 0


def _dispatch_share(args: argparse.Namespace) -> int:
    """Stub for ``chimera ferret share <session>``.

    A future agent (FF5 cloud bridge) owns the share body. Returning 2
    keeps shell pipelines from silently treating an unimplemented
    command as success.
    """
    target = getattr(args, "sub_action", None)
    print(
        f"ferret share: target={target!r} (scaffold; see "
        "research/ferret/SPEC.md, agent FF5).",
        file=sys.stderr,
    )
    return 2


def _dispatch_agents(args: argparse.Namespace) -> int:
    """Stub for ``chimera ferret agents [list|show <name>]``.

    FF7 owns the agents preset surface. Returning 2 keeps shell
    pipelines honest about the scaffold state.
    """
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    print(
        f"ferret agents: action={action!r} target={target!r} "
        "(scaffold; see research/ferret/SPEC.md, agent FF7).",
        file=sys.stderr,
    )
    return 2


def _dispatch_bench(args: argparse.Namespace) -> int:
    """Stub for ``chimera ferret bench <suite>``.

    A future agent owns the benchmark suite wiring (mirrors otter's
    ``bench`` surface). Returning 2 keeps the scaffold contract honest.
    """
    suite = getattr(args, "sub_action", None)
    print(
        f"ferret bench: suite={suite!r} (scaffold; see "
        "research/ferret/SPEC.md).",
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
# Entry point
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera ferret``.

    Args:
        args: Parsed ``argparse.Namespace`` from the ferret subparser.

    Returns:
        Process exit code (``0`` on success).
    """
    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    if getattr(args, "print_mode", None) is not None:
        # One-shot path is owned by sibling agents (sandbox + approval +
        # providers). Until they land, fall through to the otter
        # one-shot delegation via the REPL module's run-code shim. Tests
        # can still exercise the dispatch routing without this path.
        try:
            from chimera.ferret.repl import run_ferret_print

            return int(run_ferret_print(args))
        except Exception as exc:  # noqa: BLE001
            print(
                f"ferret -p: one-shot path not yet wired ({exc}). "
                "See research/ferret/SPEC.md (FF2/FF3/FF6).",
                file=sys.stderr,
            )
            return 2

    # No print, no subcommand — emit a brief usage hint pointing at the
    # interactive REPL. Returning 2 (usage) is conventional for "user
    # forgot --print".
    try:
        from chimera.ferret.repl import run_ferret_repl

        return int(run_ferret_repl(args))
    except Exception as exc:  # noqa: BLE001
        print(
            "ferret: interactive REPL not yet wired in this scaffold "
            f"({exc}). Use --print/-p PROMPT for one-shot mode, "
            "--version for version, or --help for the full flag list. "
            "(see research/ferret/SPEC.md).",
            file=sys.stderr,
        )
        return 2


__all__ = [
    "add_arguments",
    "run",
]
