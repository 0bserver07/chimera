"""``chimera otter`` — Otter, a Chimera coding agent in the open-source agent tradition.

Otter is the second Chimera coding-agent CLI, paralleling :mod:`chimera.mink`.
Where mink mirrors a TUI-first ergonomic, otter mirrors a server-first /
multi-client open-source coding agent (TUI + HTTP + ACP).

This module ships the **scaffold**: a working ``add_arguments`` / ``run``
pair so ``chimera otter --version`` and ``chimera otter -p "..."`` route
through. Subcommand placeholders (``serve`` / ``sessions`` / ``share`` /
``agents``) are recognised and dispatched to stub handlers; subsequent
agents in the wave fill in the bodies.

Conventions follow ``chimera/mink/cli.py`` closely so users moving between
``chimera mink`` and ``chimera otter`` pay no surprise tax.

Trademark hygiene: this module never names the upstream open-source coding
agent in source/docs/help text. ``~/.opencode/config.json`` is referenced as
a filesystem path (a fact, not a brand claim).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from chimera.errors import friendly_errors

# WHY: only stdlib + chimera at import time. Provider deps (httpx, anthropic,
# openai SDKs) are pulled in lazily inside ``_build_provider`` so importing
# ``chimera.otter.cli`` for ``--help`` / ``--version`` stays cheap.

_DEFAULT_MODEL = "claude-sonnet-4-6"
"""Default model when neither ``--model`` nor ``$OTTER_MODEL`` is set.

WHY: ``gpt-5`` is not a real model id today; the upstream open-source
coding agent uses provider chains rather than a single default. We pick
``claude-sonnet-4-6`` so the otter scaffold has a concrete, working default
that ``chimera.providers.factory.create_provider`` recognizes via the
Anthropic provider auto-detection.
"""

_VALID_OUTPUT_FORMATS = ("text", "json", "stream-json")
_VALID_SUBCOMMANDS = (None, "serve", "sessions", "share", "agents", "bench", "mcp")
# WHY (O18): ``bench`` repurposes the ``sub_action`` positional slot for
# the benchmark name (``humaneval`` / ``tau-bench``). The choices below
# are the union of all sub_action shapes any otter subcommand accepts so
# argparse keeps validating the slot consistently across handlers.
# WHY (server-mgmt): ``status`` and ``stop`` are added so ``otter serve
# status`` / ``otter serve stop`` reuse the ``sub_action`` positional slot
# without breaking argparse choices validation across other subcommands.
# WHY (W9-O2): ``add`` + ``auth`` are added for ``otter mcp add`` and
# ``otter mcp auth``; ``list`` is reused from sessions/agents.
_VALID_SUB_ACTIONS = (
    None,
    "list",
    "show",
    "create",
    "cost",
    "humaneval",
    "tau-bench",
    "status",
    "stop",
    "add",
    "auth",
    # WHY (O4-W9): ``rename`` lets ``otter sessions rename <id> <title>``
    # update the title key in summary.json after the run finished.
    "rename",
)


def _resolve_version() -> str:
    """Resolve the chimera package version for ``--version`` output.

    Mirrors :func:`chimera.mink.cli._resolve_version` so otter and mink
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
    """Register ``chimera otter`` flags on ``parser``.

    Mirrors mink's ``add_arguments`` shape so embedders / tests can attach
    the same flag surface to a parser they already own.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the otter
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera otter {_resolve_version()}",
    )
    # WHY: env precedence is --model > $OTTER_MODEL > _DEFAULT_MODEL. Lets
    # CI / shells pin a model once while keeping ad-hoc --model overrides
    # cheap. Mirrors mink's $CHIMERA_MINK_MODEL pattern.
    parser.add_argument(
        "--model",
        default=os.environ.get("OTTER_MODEL") or _DEFAULT_MODEL,
        help=(
            "Model identifier (default: $OTTER_MODEL or "
            f"{_DEFAULT_MODEL}). Resolved through "
            "``chimera.providers.factory.create_provider``."
        ),
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        help="One-shot: run a single turn with PROMPT, print, exit.",
    )
    # WHY (O4-W9): a hand-authored ``--title`` lets users tag a one-shot
    # ``-p`` run so ``chimera otter sessions list`` surfaces a friendly
    # label instead of the truncated prompt heuristic. When unset, the
    # ``prompt`` field continues to drive the rendered title (back-compat).
    # The value is persisted into ``summary.json`` under the ``title`` key
    # so ``sessions show`` and ``sessions list`` can both surface it.
    parser.add_argument(
        "--title",
        dest="session_title",
        default=None,
        help=(
            "With -p: human-friendly label stored in summary.json and shown "
            "by 'chimera otter sessions list'. When unset, the prompt is "
            "used as the title."
        ),
    )
    # WHY (O3-W9): ``--file/-f`` lets ``-p`` invocations attach the contents
    # of one or more files to the prompt without copy-paste. Each path is
    # read at run time and wrapped in a ``<file path="..." lines="N">``
    # block (XML-like, mirrors mink's prompt attachment shape) which is
    # concatenated *before* the ``-p`` text. ``-`` reads from stdin once;
    # repeating ``-`` is allowed but only the first stdin read returns
    # bytes. Multiple ``-f`` invocations stack via ``action="append"``.
    parser.add_argument(
        "-f",
        "--file",
        dest="files",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "With -p: attach a file's contents to the prompt. May be passed "
            "multiple times to stack attachments. Use '-' to read from stdin."
        ),
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
    # WHY (W5): LSP tools (diagnostics, completion, rename, definition,
    # references) are part of otter's default tool group, mirroring the
    # upstream open-source coding agent's "LSP as primary capability"
    # posture. The flag is opt-out so users on machines without language
    # servers can disable detection (and the warning) entirely.
    parser.add_argument(
        "--no-lsp",
        action="store_true",
        default=False,
        help=(
            "Disable the otter LSP tool group (diagnostics/completion/"
            "rename/definition/references). LSP is on by default; "
            "tools degrade gracefully when no language server is found."
        ),
    )
    # WHY (W3): project + user rules from AGENTS.md, .cursor/rules/*.mdc,
    # and .opencode/rules.md are appended to the otter system prompt by
    # default via :func:`chimera.otter.rules.load_otter_rules`. ``--no-rules``
    # skips that ingestion (useful for repro tests + CI fixtures that want
    # a deterministic prompt independent of repo state).
    parser.add_argument(
        "--no-rules",
        action="store_true",
        default=False,
        help=(
            "Skip ingestion of AGENTS.md, .cursor/rules/*.mdc, and "
            ".opencode/rules.md into the system prompt. Default behavior "
            "appends a '## Project Rules' section when any source exists."
        ),
    )
    # WHY (W4): user-defined slash commands from ``.opencode/command/*.md``
    # are loaded by default at REPL startup so projects can ship reusable
    # prompt templates. Locked-down environments (CI, untrusted project
    # trees) can opt out so a malicious project file can't shadow a
    # built-in slash command like ``/exit``.
    parser.add_argument(
        "--no-custom-commands",
        dest="no_custom_commands",
        action="store_true",
        default=False,
        help=(
            "Skip loading user-defined commands from .opencode/command/*.md "
            "at REPL startup. Default behavior loads both user-scope "
            "(~/.opencode/command/) and project-scope ones."
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
    # WHY (C1, wave 9): --resume / --continue mirror mink and the otter
    # interactive REPL so a one-shot ``-p`` invocation can pick up where
    # the previous run left off. ``--resume <id>`` loads the named
    # ``~/.chimera/eventlog/otter-*`` directory; ``-c`` /
    # ``--continue`` resolves the newest otter run for the current cwd
    # via :func:`chimera.sessions.eventlog.find_latest_run`.
    parser.add_argument(
        "--resume",
        default=None,
        help=(
            "Resume a persisted otter run by id (matches "
            "~/.chimera/eventlog/<id>/). The replayed conversation is "
            "prepended to the new turn so the agent has full context."
        ),
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="continue_latest",
        action="store_true",
        default=False,
        help=(
            "Resume the most-recent otter run under the current "
            "working directory. Equivalent to "
            "``--resume <newest-otter-id-in-cwd>``."
        ),
    )
    # WHY: ``--acp`` re-routes ``chimera otter serve`` to the JSON-RPC ACP
    # server (agent O6) instead of the HTTP server (agent O14). External
    # IDE / TUI clients drive the agent over stdio when this flag is set.
    parser.add_argument(
        "--acp",
        action="store_true",
        default=False,
        help=(
            "With 'serve': run the ACP (Agent Client Protocol) JSON-RPC "
            "server on stdio instead of the HTTP server."
        ),
    )
    # WHY: the HTTP variant of ``chimera otter serve`` (agent O14) takes a
    # bind host + port + optional shared-secret bearer token. Defaults
    # match :mod:`chimera.otter.server` so flags-omitted use is sane.
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "With 'serve' (HTTP mode): bind host (default: 127.0.0.1). "
            "Use 0.0.0.0 only with --auth-token."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="With 'serve' (HTTP mode): bind port (default: 5173).",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help=(
            "With 'serve' (HTTP mode): shared-secret bearer token "
            "required on every request except /healthz."
        ),
    )
    # WHY (O-SERVER-3): bearer auth over plain HTTP exposes the token to
    # any on-path observer once the server binds off-localhost. Pair the
    # bearer with TLS by passing both flags; the server wraps its listen
    # socket via ``ssl.SSLContext`` (stdlib only) so clients use HTTPS.
    parser.add_argument(
        "--tls-cert",
        dest="tls_cert",
        default=None,
        help=(
            "With 'serve' (HTTP mode): path to a PEM-encoded server "
            "certificate. Must be paired with --tls-key. When set the "
            "server serves HTTPS instead of HTTP."
        ),
    )
    parser.add_argument(
        "--tls-key",
        dest="tls_key",
        default=None,
        help=(
            "With 'serve' (HTTP mode): path to the PEM-encoded private "
            "key matching --tls-cert."
        ),
    )
    # WHY (W2): plugins under ``~/.opencode/plugin/*`` and
    # ``<project>/.opencode/plugin/*`` are wired into every otter session by
    # default. ``--no-plugins`` lets users / CI disable directory-based
    # plugin discovery without removing the on-disk dirs.
    parser.add_argument(
        "--no-plugins",
        dest="no_plugins",
        action="store_true",
        default=False,
        help=(
            "Skip directory-based plugin discovery under "
            "~/.opencode/plugin/* and <project>/.opencode/plugin/* "
            "(default: load all discovered plugins)."
        ),
    )
    # WHY: MCP tool ingest is on by default (symmetric with mink's
    # ``_load_mcp_tools`` which always runs). ``--no-mcp`` lets users
    # opt out when ``~/.opencode/config.json`` or
    # ``.opencode/{config,mcp}.json`` configures servers they don't
    # want spawned for this invocation (e.g. CI runs, offline tests).
    parser.add_argument(
        "--no-mcp",
        dest="no_mcp",
        action="store_true",
        default=False,
        help=(
            "Skip MCP server discovery from ~/.opencode/config.json and "
            ".opencode/{config,mcp}.json. Default: MCP servers are loaded "
            "and their tools attached to the otter agent."
        ),
    )
    # WHY (C5): ``--tui`` boots a textual-based prototype frontend bound
    # to an in-process :class:`OtterServer`. The default REPL stays
    # readline-based; this flag is opt-in so users without the optional
    # ``[tui]`` extra never pay the textual import cost. See
    # ``docs/otter/tui.md`` for the install + key bindings.
    parser.add_argument(
        "--tui",
        dest="tui",
        action="store_true",
        default=False,
        help=(
            "Launch the textual-based otter TUI prototype (requires "
            "the [tui] extra: 'pip install chimera-run[tui]'). The "
            "TUI talks to an in-process OtterServer over the same "
            "HTTP+SSE surface a remote client would use."
        ),
    )
    # WHY (O1 — wave 9): ``--user`` flips ``otter agents create`` from
    # project-scope (``<cwd>/.opencode/agent/<name>.md``) to user-scope
    # (``~/.opencode/agent/<name>.md``). The flag is generic enough to
    # repurpose for any future ``--user``-vs-project agents subcommand
    # (think ``agents delete --user reviewer``) without re-parsing.
    parser.add_argument(
        "--user",
        dest="agents_user",
        action="store_true",
        default=False,
        help=(
            "With 'agents create': write the new agent file to the "
            "user-scope directory (~/.opencode/agent/) instead of the "
            "project-scope directory (<cwd>/.opencode/agent/)."
        ),
    )
    # WHY: subcommand placeholders are positionals so the orchestrator can
    # route ``chimera otter serve``, ``chimera otter sessions list``, etc.
    # without re-parsing. Other agents in the wave own the bodies; we just
    # stub the dispatch.
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help=(
            "Optional: 'serve' (HTTP server placeholder), 'sessions' "
            "(list/show), 'share' (share a session), 'agents' (list/show)."
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
    # WHY (W9-O2): ``mcp add <name> <command...>`` needs a variadic tail
    # after ``sub_target`` so the entire stdio command is captured as
    # one argv slice. ``nargs="*"`` keeps the slot optional for the
    # other subcommands (defaults to []) so this surface is purely
    # additive.
    parser.add_argument(
        "mcp_extra",
        nargs="*",
        default=[],
        metavar="MCP_EXTRA",
        help=(
            "With 'mcp add <name>': trailing executable + args for the "
            "stdio MCP server (ignored when --mcp-http <url> is set)."
        ),
    )
    # WHY (W9-O2): ``mcp``-specific flags. The ``--user`` flag is
    # already registered (dest ``agents_user``) so the mcp dispatcher
    # reuses it; new flags use the ``--mcp-*`` prefix so they don't
    # shadow flags other subcommands might want later.
    parser.add_argument(
        "--mcp-http",
        dest="mcp_http",
        default=None,
        help=(
            "With 'mcp add': add an HTTP MCP server at this URL "
            "(mutually exclusive with the trailing stdio command)."
        ),
    )
    parser.add_argument(
        "--mcp-header",
        dest="mcp_header",
        action="append",
        default=None,
        help=(
            "With 'mcp add --mcp-http': repeatable KEY=VALUE HTTP header. "
            "Use once per header."
        ),
    )
    parser.add_argument(
        "--mcp-env",
        dest="mcp_env",
        action="append",
        default=None,
        help=(
            "With 'mcp add' (stdio): repeatable KEY=VALUE subprocess "
            "environment override. Use once per variable."
        ),
    )
    parser.add_argument(
        "--yes",
        dest="mcp_yes",
        action="store_true",
        default=False,
        help=(
            "With 'mcp add': skip the interactive 'write this entry?' "
            "y/N confirmation. Use in CI scripts."
        ),
    )
    # WHY (O18): bench-specific flags. Kept under their own ``--bench-*``
    # prefix so ``otter bench humaneval --limit 20`` is unambiguous against
    # the future ``otter sessions list --limit 20`` surface.
    # WHY (G6): ``sessions cost`` flags — parity with ``mink runs cost``
    # (see :mod:`chimera.mink.cost`) and the ``GET /runs/cost`` HTTP route.
    # Naming uses ``sessions_*`` dest names so the dispatcher
    # :func:`chimera.otter.sessions.dispatch_sessions` reads them
    # uniformly across ``list``, ``show``, and ``cost`` actions.
    parser.add_argument(
        "--since",
        dest="sessions_since",
        default=None,
        help=(
            "With 'sessions cost' (or 'sessions list'): filter window. "
            "Accepts shorthand ('7d', '24h', '30m') or ISO-8601 date."
        ),
    )
    parser.add_argument(
        "--format",
        dest="sessions_format",
        choices=["text", "json", "csv"],
        default="text",
        help=(
            "With 'sessions cost': output format (default: text)."
        ),
    )
    parser.add_argument(
        "--sessions-model",
        dest="sessions_model",
        default=None,
        help=(
            "With 'sessions list/cost': filter to sessions whose model "
            "matches (case-insensitive substring; 'all' = no filter)."
        ),
    )
    parser.add_argument(
        "--sessions-limit",
        dest="sessions_limit_flag",
        type=int,
        default=None,
        help=(
            "With 'sessions list/cost': cap rows considered "
            "(newest first; <=0 / unset = no cap)."
        ),
    )
    parser.add_argument(
        "--sessions-json",
        dest="sessions_json",
        action="store_true",
        default=False,
        help=(
            "With 'sessions list/show': emit JSON instead of the table. "
            "(``sessions cost`` uses ``--format json`` instead.)"
        ),
    )
    parser.add_argument(
        "--bench-limit",
        dest="bench_limit",
        type=int,
        default=5,
        help=(
            "With 'bench': max tasks to run (default: 5; pass 0 for full run)."
        ),
    )
    parser.add_argument(
        "--bench-domain",
        dest="bench_domain",
        default="airline",
        help=(
            "With 'bench tau-bench': domain to evaluate "
            "(airline/retail/telecom/banking/mock; default: airline)."
        ),
    )
    # WHY (server-mgmt): ``serve stop`` accepts ``--port`` (already declared
    # above) to target one server or ``--all`` to graceful-stop every
    # backgrounded otter server. ``--serve-timeout`` widens the SIGTERM
    # window for slow shutdowns; default 10s matches the project
    # graceful-shutdown rule (see CLAUDE.md).
    parser.add_argument(
        "--all",
        dest="serve_stop_all",
        action="store_true",
        default=False,
        help=(
            "With 'serve stop': stop every backgrounded server of this "
            "flavor (otter/ferret). Mutually exclusive with --port."
        ),
    )
    parser.add_argument(
        "--serve-timeout",
        dest="serve_stop_timeout",
        type=float,
        default=10.0,
        help=(
            "With 'serve stop': seconds to wait after SIGTERM before "
            "escalating to SIGKILL (default: 10.0)."
        ),
    )


# ---------------------------------------------------------------------------
# Allowed-tools filtering — mirrors mink's helper
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
# LSP tool attachment — wires chimera/otter/lsp.py into the default tool group
# ---------------------------------------------------------------------------


def _attach_lsp_tools(
    tools: list[Any], no_lsp: bool, project_root: Path,
) -> list[Any]:
    """Append otter LSP tools to ``tools`` unless ``--no-lsp`` is set.

    Mirrors the upstream open-source coding agent's posture of LSP as a
    primary capability rather than a peripheral integration. The factory
    in :mod:`chimera.otter.lsp` lazily detects language servers; when none
    are reachable the tools degrade to ``"LSP not configured"`` errors at
    call time. We never crash the agent over a missing server — failures
    here fall through with a one-line warning to stderr.

    Args:
        tools: The current list of tools (e.g. ``list(AGENT_TOOLS)``).
        no_lsp: When True, short-circuit and return *tools* unchanged.
        project_root: Workspace root passed to ``auto_detect_provider``.

    Returns:
        A new list with the LSP tools appended, or *tools* unchanged on
        opt-out / detection failure.
    """
    if no_lsp:
        return list(tools)
    try:
        from chimera.otter.lsp import build_lsp_tool_group

        group = build_lsp_tool_group(workdir=str(project_root))
        result = list(tools)
        result.extend(list(group))
        return result
    except Exception as exc:  # noqa: BLE001 — never crash the agent
        sys.stderr.write(
            f"[otter] LSP detection failed; continuing without LSP tools: {exc}\n"
        )
        sys.stderr.flush()
        return list(tools)


# ---------------------------------------------------------------------------
# MCP tool attachment — wires chimera/otter/mcp.py into the default tool group
# ---------------------------------------------------------------------------


def _attach_mcp_tools(tools: list[Any], project_root: Path) -> list[Any]:
    """Append MCP server tools (from ``~/.opencode`` + ``.opencode``) to *tools*.

    Calls :func:`chimera.otter.mcp.load_mcp_servers` to discover servers
    configured in ``~/.opencode/config.json`` (user scope) and the project
    ``.opencode/{config,mcp}.json`` (project scope), feeds each enabled
    entry into a fresh :class:`chimera.mcp.client.MCPClient`, and returns
    the augmented tool list. Per-server connection failures are logged to
    stderr but never crash the agent build — symmetric with how
    :func:`_attach_lsp_tools` handles missing language servers.

    Callsites are expected to gate on ``--no-mcp`` themselves before
    invoking this helper, so the helper itself is a pure tools-list
    transform; that keeps the gate symmetric with the LSP path.

    Args:
        tools: The current list of tools (e.g. ``list(AGENT_TOOLS)``).
        project_root: Workspace root passed to ``load_mcp_servers``.

    Returns:
        A new list with each connected MCP server's tools appended. On
        any unexpected error, *tools* is returned unchanged.
    """
    try:
        from chimera.otter.mcp import load_mcp_servers
    except Exception as exc:  # noqa: BLE001 — never crash the agent
        sys.stderr.write(
            f"[otter] MCP loader unavailable; continuing without MCP tools: {exc}\n"
        )
        sys.stderr.flush()
        return list(tools)

    try:
        servers = load_mcp_servers(project_root)
    except Exception as exc:  # noqa: BLE001 — never crash the agent
        sys.stderr.write(
            f"[otter] MCP discovery failed; continuing without MCP tools: {exc}\n"
        )
        sys.stderr.flush()
        return list(tools)

    enabled = [s for s in servers if s.enabled]
    if not enabled:
        return list(tools)

    # WHY (F2): route through the per-process MCP client cache so
    # repeated agent builds (HTTP/ACP serve calls a fresh factory per
    # session) don't re-spawn the same stdio MCP server subprocess.
    # ``mcp_cache.get_or_create`` owns the build/connect/memoise dance
    # and falls back to a fresh client on any cache miss.
    try:
        from chimera.otter.mcp_cache import get_or_create as _mcp_get_or_create
    except Exception as exc:  # noqa: BLE001 — never crash the agent
        sys.stderr.write(
            f"[otter] MCP cache import failed; continuing without MCP tools: {exc}\n"
        )
        sys.stderr.flush()
        return list(tools)

    entries: list[tuple[str, dict[str, Any]]] = [
        (s.name, s.to_client_spec()) for s in enabled
    ]
    try:
        client = _mcp_get_or_create(entries)
    except Exception as exc:  # noqa: BLE001 — defensive: cache must never crash the agent
        sys.stderr.write(
            f"[otter] MCP cache lookup failed; continuing without MCP tools: {exc}\n"
        )
        sys.stderr.flush()
        return list(tools)

    if client is None:
        return list(tools)

    result = list(tools)
    try:
        result.extend(list(client.tools))
    except Exception as exc:  # noqa: BLE001 — defensive
        sys.stderr.write(
            f"[otter] MCP tool wrap failed; continuing without MCP tools: {exc}\n"
        )
        sys.stderr.flush()
        return list(tools)
    return result


# ---------------------------------------------------------------------------
# Plugin extension wiring (W2)
# ---------------------------------------------------------------------------


def _attach_plugin_extensions(
    tools: list[Any],
    hooks: list[Any],
    agent_registry: Any | None,
    project_root: Path,
    *,
    mcp_servers: list[Any] | None = None,
    enabled: bool = True,
    loader: Any | None = None,
    slash_register: Any | None = None,
) -> list[Any]:
    """Materialize otter plugins and graft them onto an in-construction agent.

    Each directory plugin under ``~/.opencode/plugin/<name>`` and
    ``<project>/.opencode/plugin/<name>`` may contribute any subset of:
    parsed :class:`AgentConfig` records (``agents/*.md``), slash-command
    descriptors (``command/*.md`` / ``commands/*.md``), MCP server configs
    (``mcp.json``), and event-driven shell hooks (``hooks/hooks.json``).
    This helper merges every contribution into the live mutable
    structures the caller is about to hand to :class:`Agent` /
    :class:`LoopConfig` so a single call site fully wires the plugin
    surface.

    The function is **best-effort**: any exception raised by an
    individual plugin is downgraded to a one-line stderr warning so a
    bad manifest cannot crash the otter session. Tests inject a
    ``loader`` callable to bypass the filesystem entirely.

    Args:
        tools: Live tool list passed to :class:`Agent`. Plugin-contributed
            ``BaseTool`` instances (when present on a plugin via the
            opt-in ``_extra_tools`` slot) are appended in-place.
        hooks: Live hook list. Plugin :class:`Hook` records are appended
            in-place; the caller wires this list onto a
            :class:`HookEmitter` once W3 lands the matcher conversion.
        agent_registry: Optional :class:`AgentRegistry`. When non-``None``,
            plugin :class:`AgentConfig` instances are registered onto it
            so ``--agent <name>`` resolves plugin-contributed agents.
        project_root: Project directory; ``<project_root>/.opencode/plugin``
            is scanned for project-scoped plugins.
        mcp_servers: Optional MCP server list (W1 owns the live wiring).
            When supplied, ``(name, config)`` tuples are appended in-place.
        enabled: When ``False`` (e.g. ``--no-plugins``), the function is
            a no-op and returns ``[]``. Default: ``True``.
        loader: Optional override for :func:`load_otter_plugins`. Tests
            inject a fake to bypass the filesystem.
        slash_register: Optional ``(name, handler, help_text) -> None``
            callable for plugin commands. Defaults to the shared
            :func:`chimera.cli.slash_commands.register` (collab with W4).

    Returns:
        The list of materialized plugin instances (empty on disabled or
        when nothing was discovered).
    """
    if not enabled:
        return []

    if loader is None:
        from chimera.otter.plugins import load_otter_plugins as loader_fn
    else:
        loader_fn = loader

    try:
        plugins = list(loader_fn(project_root))
    except Exception as exc:  # noqa: BLE001  (loader is best-effort)
        sys.stderr.write(f"[otter] plugin discovery failed: {exc}\n")
        return []

    if not plugins:
        return []

    if slash_register is None:
        try:
            from chimera.cli.slash_commands import register as _shared_register

            slash_register = _shared_register
        except Exception:  # noqa: BLE001  (shared registry optional)
            slash_register = None

    for plugin in plugins:
        plugin_name = getattr(plugin, "name", "?")

        # 1. Agents -> AgentRegistry
        if agent_registry is not None:
            for cfg in getattr(plugin, "agents", []) or []:
                try:
                    agent_registry.register(cfg)
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(
                        f"[otter] plugin {plugin_name!r} agent register "
                        f"failed: {exc}\n"
                    )

        # 2. Hooks -> caller's hook list
        for hook in getattr(plugin, "hooks", []) or []:
            hooks.append(hook)

        # 3. MCP servers -> caller's mcp_servers list (W1 wires the client)
        if mcp_servers is not None:
            for srv_name, cfg in (getattr(plugin, "mcp_servers", {}) or {}).items():
                mcp_servers.append((srv_name, cfg))

        # 4. Slash commands -> shared slash registry (W4 owns dispatch)
        if slash_register is not None:
            for cmd in getattr(plugin, "commands", []) or []:
                try:
                    handler = _make_plugin_command_handler(cmd)
                    slash_register(
                        getattr(cmd, "name", ""),
                        handler,
                        getattr(cmd, "description", ""),
                    )
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(
                        f"[otter] plugin {plugin_name!r} command "
                        f"{getattr(cmd, 'name', '?')!r} register failed: {exc}\n"
                    )

        # 5. Tools — directory plugins do not contribute tool *instances*
        #    today; the slot is reserved for future entry-point style
        #    plugins (or test plugins that opt-in via ``_extra_tools``).
        extra_tools = getattr(plugin, "_extra_tools", None)
        if extra_tools:
            for tool in extra_tools:
                tools.append(tool)

    return plugins


def _build_plugin_hook_emitter(plugin_hooks: list[Any]) -> Any | None:
    """Convert plugin :class:`chimera.plugins.base.Hook` records to a HookEmitter.

    Plugin hooks ship as ``Hook(command, event_type, working_dir, timeout, env)``
    records collected by :func:`_attach_plugin_extensions`. mink consumes its
    settings-style hooks via :class:`HookEmitter` wired onto
    :attr:`LoopConfig.hook_emitter`; this helper does the equivalent
    conversion for otter so directory-plugin hooks actually fire when
    :mod:`chimera.core.tool_executor` emits ``PreToolUse`` (and any future
    events) during a tool dispatch.

    Each plugin :class:`Hook` becomes a :class:`HookMatcher` wrapping a
    single :class:`CommandHook` with no tool-name fnmatch (matcher=None
    means "match every tool"). All matchers land in one
    :class:`HookExecutor` so the emitter fires every appropriate hook
    on each ``emit()``.

    Args:
        plugin_hooks: List of plugin Hook records (the same list mutated
            in-place by :func:`_attach_plugin_extensions`).

    Returns:
        A configured :class:`HookEmitter`, or ``None`` if the input list
        is empty / no usable hooks were found.
    """
    if not plugin_hooks:
        return None

    from chimera.hooks.emitter import HookEmitter
    from chimera.hooks.executor import HookExecutor
    from chimera.hooks.hook_types import CommandHook, HookMatcher
    from chimera.plugins.base import Hook as PluginHook

    matchers: list[Any] = []
    for raw in plugin_hooks:
        if not isinstance(raw, PluginHook):
            # Defensive: skip anything that isn't a recognised Hook record.
            continue
        if not getattr(raw, "command", ""):
            continue
        try:
            timeout = int(raw.timeout) if raw.timeout else 60
        except (TypeError, ValueError):
            timeout = 60
        cmd = CommandHook(
            command=str(raw.command),
            timeout=timeout,
            cwd=str(raw.working_dir) if raw.working_dir else None,
            extra_env=dict(raw.env) if raw.env else {},
        )
        matchers.append(
            HookMatcher(
                hooks=[cmd],
                matcher=None,
                source="plugin",
            ),
        )

    if not matchers:
        return None

    executor = HookExecutor()
    return HookEmitter(executor=executor, matchers=matchers)


def _make_plugin_command_handler(cmd: Any) -> Any:
    """Build a slash-command handler that prints the plugin command body.

    The directory plugin format ships a markdown body which is treated
    as the prompt the slash command injects. The shared slash contract
    is ``handler(session, env, args, out)``; we keep the implementation
    deliberately small so W4 can swap in richer dispatch later without
    breaking the wiring point this helper sets up.
    """

    def _handler(_session: Any, _env: Any, _args: str, out: Any) -> None:
        body = getattr(cmd, "body", "") or ""
        printer = out if callable(out) else print
        if body:
            printer(body)
        else:
            printer(
                f"(plugin command {getattr(cmd, 'name', '?')!r} has no body)"
            )

    return _handler


# ---------------------------------------------------------------------------
# Prompt composition — wires chimera/otter/rules.py into the system prompt
# ---------------------------------------------------------------------------


def _compose_prompt(base: str, project_root: Path, no_rules: bool) -> str:
    """Append project + user rules onto ``base`` for the otter system prompt.

    Mirrors the way :mod:`chimera.context.agent_memory` ingests CLAUDE.md
    for mink: rules are loaded from the conventional sources discovered by
    :func:`chimera.otter.rules.load_otter_rules` and appended under a
    ``## Project Rules`` section header. When ``no_rules`` is set, or no
    rule files exist, ``base`` is returned unchanged.

    Failures inside :func:`load_otter_rules` are caught and logged to
    stderr — the agent should never refuse to start because rule discovery
    threw. Trademark hygiene is preserved by the upstream loader (rules
    are user-authored markdown; we never re-emit brand names).

    Args:
        base: The hand-authored otter system prompt (usually one
            sentence describing the agent's role).
        project_root: Project root directory used to locate ``AGENTS.md``,
            ``.cursor/rules/*.mdc``, and ``.opencode/rules.md``.
        no_rules: When True, short-circuit and return ``base`` unchanged.

    Returns:
        Either ``base`` (no rules wanted / available) or
        ``f"{base}\\n\\n## Project Rules\\n\\n{rules}"`` ready for
        :func:`chimera.core.prompt.Prompt.from_string`.
    """
    if no_rules:
        return base
    try:
        from chimera.otter.rules import load_otter_rules

        rules = load_otter_rules(project_root)
    except Exception as exc:  # noqa: BLE001 — never crash the agent on rules
        sys.stderr.write(
            f"[otter] rules ingest failed; continuing without rules: {exc}\n"
        )
        sys.stderr.flush()
        return base
    if not rules:
        return base
    return f"{base}\n\n## Project Rules\n\n{rules}"


# ---------------------------------------------------------------------------
# Run id + eventlog persistence — mirrors mink's layout under otter-* prefix
# ---------------------------------------------------------------------------


def _make_run_id() -> str:
    """Generate a sortable, unique run id for a persisted ``-p`` invocation.

    The id is ``otter-<utc_compact>-<uuid8>`` so it sorts chronologically
    and never collides under concurrent invocations.

    Returns:
        A new run id string.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"otter-{stamp}-{suffix}"


def _eventlog_root() -> Path:
    """Root directory for all persisted otter runs.

    Returns:
        ``~/.chimera/eventlog/`` honoring the current ``Path.home()``.
    """
    return Path.home() / ".chimera" / "eventlog"


def _apply_resume_prefix(
    args: argparse.Namespace,
    *,
    prefix: str,
    default_prompt: str,
) -> str:
    """Resolve ``--resume`` / ``--continue`` and return the effective prompt.

    When neither flag is set this is the identity over ``default_prompt``.
    When either is set, the matching JSONL eventlog is replayed via
    :func:`chimera.sessions.eventlog.resume_run` and its message history
    is rendered as a ``<prior_conversation>`` block prepended to
    ``default_prompt`` so the agent's new turn has the full context.

    Args:
        args: The parsed otter argparse namespace.
        prefix: CLI-specific prefix (``"otter-"``) used by ``-c``
            resolution.
        default_prompt: The user's ``-p`` text. Returned unchanged when
            no resume id is set or when the resume target is empty /
            unreadable.

    Returns:
        The prompt to feed into ``agent.async_run`` — either the raw
        ``-p`` text or the rendered transcript-prefixed variant.
    """
    from chimera.sessions.eventlog.resume_helpers import (
        build_resume_prefix,
        resolve_resume_id,
        resume_run,
    )

    target_id = resolve_resume_id(
        explicit_id=getattr(args, "resume", None),
        continue_latest=bool(getattr(args, "continue_latest", False)),
        prefix=prefix,
        eventlog_root=_eventlog_root(),
        cwd=os.path.abspath(args.cwd or os.getcwd()),
    )
    if target_id is None:
        return default_prompt

    try:
        session = resume_run(target_id, eventlog_root=_eventlog_root())
    except (ValueError, OSError) as exc:
        print(
            f"[otter] --resume / --continue: failed to load run "
            f"{target_id!r}: {exc}",
            file=sys.stderr,
        )
        return default_prompt

    messages = list(getattr(session, "messages", []) or [])
    if not messages:
        return default_prompt

    sys.stderr.write(
        f"[otter] resumed run {target_id} ({len(messages)} messages)\n"
    )
    sys.stderr.flush()
    transcript = build_resume_prefix(messages)
    return f"{transcript}{default_prompt}"


def _utc_iso8601() -> str:
    """ISO-8601 UTC timestamp with second precision and ``Z`` suffix."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _open_run_log(run_id: str | None) -> tuple[Any, Path]:
    """Open (or create) an :class:`EventLog` for ``run_id``.

    Args:
        run_id: The persisted run identifier. When ``None``, a fresh id
            is minted via :func:`_make_run_id`.

    Returns:
        Tuple of ``(EventLog, run_dir)``.
    """
    from chimera.sessions.eventlog.log import EventLog

    resolved = run_id or _make_run_id()
    run_dir = _eventlog_root() / resolved
    run_dir.mkdir(parents=True, exist_ok=True)
    return EventLog(run_dir), run_dir


def _append_user_message(log: Any, content: str) -> None:
    """Mirror :func:`chimera.mink.cli._append_user_message` for parity."""
    from chimera.events.base import Event

    log.append(Event(type="user_message", metadata={"content": content}))


def _append_agent_result(log: Any, result: Any) -> None:
    """Mirror :func:`chimera.mink.cli._append_agent_result` for parity."""
    from chimera.events.base import Event

    log.append(
        Event(
            type="agent_result",
            metadata={
                "output": getattr(result, "output", ""),
                "steps": getattr(result, "steps", 0),
                "tool_calls_total": getattr(result, "tool_calls_total", 0),
                "cost": getattr(result, "cost", 0.0),
                "success": getattr(result, "success", False),
                "error": getattr(result, "error", None),
            },
        )
    )


def _write_run_summary(
    run_dir: Path,
    *,
    run_id: str,
    started_at: str,
    ended_at: str,
    model: str,
    prompt: str,
    result: Any,
    cwd: str,
    title: str | None = None,
) -> Path:
    """Write a ``summary.json`` next to the eventlog for quick inspection.

    Schema mirrors :func:`chimera.mink.cli._write_run_summary` so the
    ``chimera mink runs list`` viewer (and any future otter equivalent)
    can read both flavors with the same parser.

    Args:
        run_dir: Directory in which to write ``summary.json``.
        run_id: Stable id for this run (also used as ``session_id``).
        started_at: ISO-8601 UTC start timestamp.
        ended_at: ISO-8601 UTC end timestamp.
        model: Provider model name actually used.
        prompt: User prompt that drove the run.
        result: AgentResult-like object with steps/tool_calls_total/etc.
        cwd: Working directory the run executed in.
        title: Optional hand-authored label (``--title`` flag). When
            ``None`` the field is omitted; ``sessions list`` falls back
            to the prompt for the displayed title.

    Returns:
        The path to the written ``summary.json``.
    """
    payload: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "model": model,
        "prompt": prompt,
        "cwd": cwd,
        "agent": "otter",
        "steps": int(getattr(result, "steps", 0) or 0),
        "tool_calls_total": int(getattr(result, "tool_calls_total", 0) or 0),
        "success": bool(getattr(result, "success", False)),
        "cost_usd": float(getattr(result, "cost", 0.0) or 0.0),
        "total_tokens": 0,
        "error": getattr(result, "error", None),
    }
    if title is not None and str(title).strip():
        payload["title"] = str(title)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary_path


def _announce_saved_run(run_id: str, run_dir: Path) -> None:
    """Print the persisted-run pointer to stderr (one line, never to stdout)."""
    sys.stderr.write(f"[otter] run saved as {run_id} at {run_dir}/\n")
    sys.stderr.flush()


@dataclasses.dataclass
class _EmptyResult:
    """Stand-in for an :class:`AgentResult` when the run aborted pre-completion."""

    output: str = ""
    steps: int = 0
    tool_calls_total: int = 0
    cost: float = 0.0
    success: bool = False
    error: str | None = "aborted"


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def _build_provider(model: str) -> Any:
    """Construct a Provider for *model* via the otter resolver.

    Delegates to :func:`chimera.otter.providers.build_provider` so the
    one-shot path, REPL, HTTP serve, and ACP serve all share one
    routing logic (Ollama-tag detection, OpenRouter routing, factory
    fallback). Lazy-imported to keep SDK imports out of the
    ``--help`` / ``--version`` path.

    Args:
        model: Model identifier (e.g. ``claude-sonnet-4-6``,
            ``glm-5.1:cloud``, ``deepseek-v4-pro:cloud``,
            ``anthropic/claude-sonnet-4`` for OpenRouter).

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.
    """
    from chimera.otter.providers import build_provider as _bp

    return _bp(argparse.Namespace(model=model))

# ---------------------------------------------------------------------------
# File attachment helpers (O3-W9) -- ``-f/--file`` for ``chimera otter -p``
# ---------------------------------------------------------------------------


_FILE_ATTACHMENT_PER_FILE_WARN_BYTES = 100 * 1024
"""Per-file size at/above which a ``[otter]`` warning is emitted to stderr.

WHY: 100 KB is a generous source-file ceiling -- most modern CLI files
clock in well under that. Crossing it usually means the user attached a
data dump or a transcript by accident. We *warn* but still attach so the
caller stays in control; truncation only kicks in past the cumulative
total cap below.
"""

_FILE_ATTACHMENT_TOTAL_CAP_BYTES = 500 * 1024
"""Cumulative byte cap across all ``-f`` attachments.

WHY: 500 KB is roughly the prompt budget where even long-context models
start paying real latency / cost penalties. Past this we truncate the
*current* file (not previous ones) and emit a one-line stderr warning so
the user can re-run with smaller attachments if they need full content.
"""

_FILE_ATTACHMENT_TRUNCATION_MARKER = "<!-- truncated -->"
"""Sentinel appended to a truncated file body so the model knows there's more.

Kept verbatim (no surrounding whitespace) so test assertions can match
the substring deterministically.
"""


def _read_attachment(
    path: str, *, stdin: Any | None = None,
) -> tuple[str, str, int]:
    """Read one ``-f/--file`` attachment and return ``(label, content, size)``.

    WHY: factored out of :func:`_format_file_attachments` so tests can
    drive a single read deterministically (in particular the ``-`` /
    stdin path) without rebuilding the namespace + asyncio scaffolding
    of :func:`_run_print_mode`.

    Args:
        path: User-supplied path. ``"-"`` reads from *stdin*.
        stdin: Optional override for :data:`sys.stdin` (test seam).

    Returns:
        Tuple ``(label, content, byte_size)`` where ``label`` is the
        path string used in the ``<file path=...>`` opener (``"<stdin>"``
        for the ``-`` case) and ``byte_size`` is the UTF-8 byte length of
        ``content`` *before* any truncation. Raises :class:`OSError` on
        unreadable real paths so the caller can surface a usage error.
    """
    if path == "-":
        source = stdin if stdin is not None else sys.stdin
        # ``sys.stdin.read()`` returns ``str`` already; decode-ish work
        # is a no-op. Empty stdin yields ``""``.
        content = source.read()
        if not isinstance(content, str):  # defensive: bytes-mode stdin
            content = content.decode("utf-8", errors="replace")
        return ("<stdin>", content, len(content.encode("utf-8")))
    p = Path(path)
    raw = p.read_bytes()
    content = raw.decode("utf-8", errors="replace")
    return (str(p), content, len(raw))


def _format_file_attachments(
    paths: list[str] | None,
    *,
    stdin: Any | None = None,
    stderr: Any | None = None,
) -> str:
    """Read each ``-f`` path and format them as ``<file>``-tagged blocks.

    Each attachment lands as::

        <file path="X" lines="N">
        <full content>
        </file>

    Blocks are joined by a single blank line and the whole bundle is
    returned ready to be concatenated *before* the ``-p`` prompt. A
    trailing blank line keeps the prompt readable when the caller
    appends the user's prompt directly afterwards.

    Size policy (per-file 100 KB / cumulative 500 KB) is enforced here:
    we *warn* on the per-file ceiling and *truncate* the current file
    when the cumulative cap would be exceeded, appending the
    ``<!-- truncated -->`` marker before ``</file>``.

    Args:
        paths: Raw list of ``-f`` values (may include ``"-"``). ``None``
            or empty returns ``""``.
        stdin: Optional :data:`sys.stdin` override (test seam).
        stderr: Optional :data:`sys.stderr` override (test seam). Used
            for size-cap warnings.

    Returns:
        The formatted attachment bundle, or ``""`` when no paths were
        supplied. Never raises on size overflow -- it always truncates.
    """
    if not paths:
        return ""
    err = stderr if stderr is not None else sys.stderr

    blocks: list[str] = []
    cumulative = 0
    for raw_path in paths:
        try:
            label, content, size = _read_attachment(raw_path, stdin=stdin)
        except OSError as exc:
            err.write(f"[otter] -f {raw_path!r}: {exc}\n")
            err.flush()
            continue

        if size >= _FILE_ATTACHMENT_PER_FILE_WARN_BYTES:
            err.write(
                f"[otter] -f {label!r}: {size} bytes exceeds "
                f"{_FILE_ATTACHMENT_PER_FILE_WARN_BYTES}-byte per-file "
                "soft cap (attaching anyway)\n"
            )
            err.flush()

        truncated = False
        if cumulative + size > _FILE_ATTACHMENT_TOTAL_CAP_BYTES:
            remaining = max(0, _FILE_ATTACHMENT_TOTAL_CAP_BYTES - cumulative)
            # Truncate by encoded-byte budget so the cumulative cap is a
            # hard byte ceiling. ``errors="ignore"`` makes us safely cut
            # off mid-multi-byte char without raising.
            encoded = content.encode("utf-8")
            content = encoded[:remaining].decode("utf-8", errors="ignore")
            size = len(content.encode("utf-8"))
            truncated = True
            err.write(
                f"[otter] -f {label!r}: cumulative attachments would "
                f"exceed {_FILE_ATTACHMENT_TOTAL_CAP_BYTES}-byte cap; "
                "truncating\n"
            )
            err.flush()

        cumulative += size
        line_count = content.count("\n") + (
            0 if (not content or content.endswith("\n")) else 1
        )
        body = content
        if truncated:
            # Marker on its own line so it's easy to grep / assert.
            sep = "" if body.endswith("\n") else "\n"
            body = f"{body}{sep}{_FILE_ATTACHMENT_TRUNCATION_MARKER}\n"
        else:
            if not body.endswith("\n"):
                body = f"{body}\n"
        blocks.append(
            f'<file path="{label}" lines="{line_count}">\n{body}</file>'
        )

    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n\n"


# ---------------------------------------------------------------------------
# One-shot --print path
# ---------------------------------------------------------------------------


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single turn and emit results in the requested format.

    Mirrors :func:`chimera.mink.cli._run_print_mode` but lighter: no agent
    preset resolution, no MCP config ingest, no remote SSH (those are
    follow-up agent scopes — O7 plugins, O4 MCP, etc.).

    Args:
        args: Parsed CLI namespace from :func:`add_arguments`.

    Returns:
        Process exit code.
    """
    import asyncio

    from chimera.core.agent import Agent
    from chimera.core.cancellation import CancellationToken
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.message_queue import MessageQueues
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment

    cwd = os.path.abspath(args.cwd or os.getcwd())

    provider = _build_provider(args.model)
    env = LocalEnvironment(workdir=cwd)
    env.setup()

    cancel = CancellationToken()
    queues = MessageQueues()

    handler: Any = None
    if args.output_format == "text":
        from chimera.cli.render import build_stream_handler

        handler = build_stream_handler(
            no_color=bool(getattr(args, "no_rich", False))
            or bool(getattr(args, "no_color", False)),
        )

    config = LoopConfig(
        handler=handler,
        cancellation=cancel,
        message_queues=queues,
    )
    loop = ReAct(max_steps=args.max_steps, config=config)

    base_prompt = (
        "You are Otter, a Chimera coding agent. Use tools to inspect and "
        "modify the user's repo. Plan briefly, then act."
    )
    composed = _compose_prompt(
        base_prompt,
        project_root=Path(cwd),
        no_rules=bool(getattr(args, "no_rules", False)),
    )
    prompt = Prompt.from_string(composed)

    tools = list(AGENT_TOOLS)
    tools = _attach_lsp_tools(
        tools, no_lsp=bool(getattr(args, "no_lsp", False)), project_root=Path(cwd),
    )
    if not bool(getattr(args, "no_mcp", False)):
        tools = _attach_mcp_tools(tools, project_root=Path(cwd))
    # WHY (W2): plugin contributions land *after* MCP/LSP so plugin
    # ``_extra_tools`` (when an entry-point plugin opts in) can override
    # earlier groups by name. Hooks accumulate in a local list and are
    # then converted into a :class:`HookEmitter` (W3 — F3) so they fire
    # through ``LoopConfig.hook_emitter`` on PreToolUse.
    plugin_hooks: list[Any] = []
    plugin_mcp_servers: list[Any] = []
    _attach_plugin_extensions(
        tools,
        plugin_hooks,
        agent_registry=None,
        project_root=Path(cwd),
        mcp_servers=plugin_mcp_servers,
        enabled=not bool(getattr(args, "no_plugins", False)),
    )
    plugin_emitter = _build_plugin_hook_emitter(plugin_hooks)
    if plugin_emitter is not None and config.hook_emitter is None:
        config.hook_emitter = plugin_emitter
    allowed = (getattr(args, "allowed_tools", "") or "").strip()
    if allowed:
        try:
            tools = _filter_allowed_tools(tools, allowed)
        except _UnknownAllowedTool as exc:
            print(str(exc), file=sys.stderr)
            env.cleanup()
            return 2

    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)

    # WHY (C1, wave 9): when ``--resume <id>`` or ``-c`` / ``--continue``
    # is set we replay the prior run via :func:`resume_run` and prepend
    # its rendered transcript to ``args.print_mode`` so the new turn
    # has full context. ``_apply_resume_prefix`` is a no-op when
    # neither flag is supplied.
    effective_prompt = _apply_resume_prefix(
        args, prefix="otter-", default_prompt=args.print_mode,
    )
    # WHY (O3-W9): prepend each ``-f``/``--file`` attachment
    # (formatted as a ``<file>`` block) to the resolved prompt
    # so the model sees attachments + (optional resume
    # transcript) + user prompt in that order. The bundle
    # is empty when no ``-f`` flags were passed.
    _otter_attachments = _format_file_attachments(
        getattr(args, "files", None),
    )
    if _otter_attachments:
        effective_prompt = f"{_otter_attachments}{effective_prompt}"

    save_enabled = not getattr(args, "no_save", False)
    run_id: str | None = None
    run_dir: Path | None = None
    log: Any | None = None
    started_at = _utc_iso8601()
    if save_enabled:
        run_id = getattr(args, "run_id", None) or _make_run_id()
        log, run_dir = _open_run_log(run_id)
        _append_user_message(log, effective_prompt)

    if args.output_format == "stream-json":
        return _run_stream_json(
            agent,
            env,
            effective_prompt,
            cancel,
            log=log,
            run_id=run_id,
            run_dir=run_dir,
            started_at=started_at,
            model=provider.model_name,
            cwd=cwd,
            title=getattr(args, "session_title", None),
        )

    result: Any = None
    try:
        result = asyncio.run(agent.async_run(effective_prompt, env=env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        env.cleanup()

    if save_enabled and log is not None and run_dir is not None and run_id is not None:
        if result is not None:
            _append_agent_result(log, result)
        _write_run_summary(
            run_dir,
            run_id=run_id,
            started_at=started_at,
            ended_at=_utc_iso8601(),
            model=provider.model_name,
            prompt=args.print_mode,
            result=result if result is not None else _EmptyResult(),
            cwd=cwd,
            title=getattr(args, "session_title", None),
        )
        _announce_saved_run(run_id, run_dir)

    if args.output_format == "json":
        payload: dict[str, Any] = {
            "output": getattr(result, "output", ""),
            "steps": getattr(result, "steps", 0),
            "cost": getattr(result, "cost", 0.0),
            "success": getattr(result, "success", False),
            "model": provider.model_name,
        }
        if save_enabled and run_id is not None:
            payload["run_id"] = run_id
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:  # text
        out = getattr(result, "output", None)
        if out:
            print(out)
    return 0 if getattr(result, "success", False) else 1


def _run_stream_json(
    agent: Any,
    env: Any,
    prompt: str,
    cancel: Any,
    *,
    log: Any | None = None,
    run_id: str | None = None,
    run_dir: Path | None = None,
    started_at: str | None = None,
    model: str = "",
    cwd: str = "",
    title: str | None = None,
) -> int:
    """Stream one JSON line per ``LoopEvent`` to stdout.

    A minimal mirror of :func:`chimera.mink.cli._run_stream_json` — emits
    ``{"type", "turn", "data"}`` lines and writes the final summary on
    completion. Redaction wiring is deferred to a follow-up agent (O17
    trademark scrub already gates the live source; secret redaction
    parity will track mink's middleware).
    """
    import asyncio

    def _emit(line: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()

    last_result_holder: dict[str, Any] = {"value": None}

    async def _drive() -> int:
        last_success = False
        events_method = getattr(agent, "async_run_events", None)
        if events_method is None:
            events_method = getattr(agent, "async_iter_events", None)
        try:
            if events_method is not None:
                async for event in events_method(prompt, env=env):
                    line = {
                        "type": getattr(event.type, "value", str(event.type)),
                        "turn": getattr(event, "turn", 0),
                        "data": _safe_event_data(event.data),
                    }
                    _emit(line)
                    if line["type"] == "result":
                        last_success = bool(
                            getattr(event.data, "reason", "") != "error"
                        )
                        last_result_holder["value"] = event.data
            else:
                result = await agent.async_run(prompt, env=env)
                last_result_holder["value"] = result
                _emit(
                    {
                        "type": "result",
                        "turn": getattr(result, "steps", 0),
                        "data": {
                            "output": getattr(result, "output", ""),
                            "cost": getattr(result, "cost", 0.0),
                            "success": getattr(result, "success", False),
                        },
                    }
                )
                last_success = bool(getattr(result, "success", False))
        except KeyboardInterrupt:
            cancel.cancel()
            return 130
        except Exception as exc:  # noqa: BLE001
            _emit(
                {
                    "type": "error",
                    "turn": 0,
                    "data": {"message": str(exc), "exception": type(exc).__name__},
                }
            )
            return 1
        return 0 if last_success else 1

    try:
        rc = asyncio.run(_drive())
    finally:
        env.cleanup()

    if (
        log is not None
        and run_dir is not None
        and run_id is not None
        and started_at is not None
    ):
        result = last_result_holder["value"]
        if result is not None:
            _append_agent_result(log, result)
        _write_run_summary(
            run_dir,
            run_id=run_id,
            started_at=started_at,
            ended_at=_utc_iso8601(),
            model=model,
            prompt=prompt,
            result=result if result is not None else _EmptyResult(),
            cwd=cwd,
            title=title,
        )
        _announce_saved_run(run_id, run_dir)
    return rc


def _safe_event_data(data: Any) -> Any:
    """Best-effort JSON view of arbitrary ``LoopEvent.data`` payloads."""
    if data is None or isinstance(data, (str, int, float, bool)):
        return data
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        try:
            return dataclasses.asdict(data)
        except Exception:  # noqa: BLE001
            return repr(data)
    if isinstance(data, dict):
        return {k: _safe_event_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_safe_event_data(v) for v in data]
    return repr(data)


# ---------------------------------------------------------------------------
# Subcommand dispatch — placeholders filled in by other agents in the wave
# ---------------------------------------------------------------------------


def _dispatch_serve(args: argparse.Namespace) -> int:
    """Dispatch ``chimera otter serve`` to ACP, HTTP, or management commands.

    Routing precedence:

    1. ``otter serve status`` / ``otter serve stop`` — pidfile-based
       management subcommands (server-mgmt). These don't bind a socket;
       they read ``~/.chimera/run/otter-*.pid`` and dispatch SIGTERM (then
       SIGKILL on timeout) per the graceful-shutdown rule in CLAUDE.md.
    2. ``--acp`` — run the stdio JSON-RPC ACP server.
    3. Default — boot the HTTP + SSE server defined in
       :mod:`chimera.otter.server`.
    """
    sub_action = getattr(args, "sub_action", None)
    if sub_action in ("status", "stop"):
        return _dispatch_serve_management(args, action=sub_action, prefix="otter")
    if getattr(args, "acp", False):
        return _dispatch_serve_acp(args)
    return _dispatch_serve_http(args)


def _dispatch_serve_management(
    args: argparse.Namespace,
    *,
    action: str,
    prefix: str,
) -> int:
    """Run ``serve status`` / ``serve stop`` — both flavors share the impl.

    The dispatcher is shared between otter and ferret because the pidfile
    layout (``~/.chimera/run/<prefix>-<port>.pid``) only differs in the
    filename prefix. Imports stay inside the function so ``--help`` stays
    cheap.

    Args:
        args: The parsed namespace. Reads ``port``, ``serve_stop_all``,
            ``serve_stop_timeout``.
        action: ``"status"`` or ``"stop"``.
        prefix: ``"otter"`` or ``"ferret"``.

    Returns:
        ``0`` on success, ``1`` when ``stop`` couldn't terminate every
        targeted process, ``2`` on usage error (e.g. ``stop`` without
        ``--port`` or ``--all`` when multiple servers are running).
    """
    from chimera.otter import server_pidfile

    if action == "status":
        records = server_pidfile.list_pidfiles(prefix=prefix)
        if not records:
            print(f"No backgrounded {prefix} servers found.")
            return 0
        for rec in records:
            auth = "yes" if rec.get("auth_token_hash") else "no"
            alive = "yes" if rec.get("alive") else "no (stale)"
            print(
                f"{prefix} port={rec.get('port')} pid={rec.get('pid')} "
                f"alive={alive} scheme={rec.get('scheme', 'http')} "
                f"auth={auth} {rec.get('path', '')}"
            )
        return 0

    # action == "stop"
    port = getattr(args, "port", None)
    stop_all = bool(getattr(args, "serve_stop_all", False))
    timeout = float(getattr(args, "serve_stop_timeout", 10.0) or 10.0)

    if port is None and not stop_all:
        # A single matching pidfile is targeted automatically; otherwise
        # the user must pass ``--port`` or ``--all`` explicitly.
        records = server_pidfile.list_pidfiles(prefix=prefix)
        if len(records) == 1:
            port = int(records[0].get("port", 0))
        elif not records:
            print(f"No backgrounded {prefix} servers found.", file=sys.stderr)
            return 0
        else:
            print(
                f"error: multiple {prefix} servers running; pass --port <n> "
                "or --all to disambiguate.",
                file=sys.stderr,
            )
            return 2

    results = server_pidfile.stop_all(
        prefix=prefix,
        port=int(port) if port is not None else None,
        timeout=timeout,
    )
    if not results:
        target = f"port {port}" if port is not None else "any"
        print(
            f"No matching {prefix} server (target={target}).", file=sys.stderr,
        )
        return 0
    failed = 0
    for r in results:
        signaled = r.get("signaled", "none")
        verb = {
            "none": "already stopped",
            "sigterm": "stopped (SIGTERM)",
            "sigterm+sigkill": "stopped (SIGTERM, then SIGKILL)",
        }.get(signaled, f"signaled={signaled}")
        ok = "ok" if r.get("stopped") else "FAILED"
        print(
            f"{prefix} port={r.get('port')} pid={r.get('pid')} {ok}: {verb}"
        )
        if not r.get("stopped"):
            err = r.get("error")
            if err:
                print(f"  error: {err}", file=sys.stderr)
            failed += 1
    return 0 if failed == 0 else 1


def _dispatch_serve_http(args: argparse.Namespace) -> int:
    """Run the HTTP + SSE otter server.

    Wires the same lazy provider/agent factory used by ``-p`` so the
    server's first ``POST /session/<id>/message`` builds an agent on
    demand. Imports stay inside the function so ``--help`` is cheap.
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.otter.server import (
        DEFAULT_HOST,
        DEFAULT_PORT,
        OtterSessionState,
        serve_http,
    )

    cwd = os.path.abspath(args.cwd or os.getcwd())
    model = args.model
    max_steps = int(args.max_steps)

    host = str(getattr(args, "host", None) or DEFAULT_HOST)
    port = int(getattr(args, "port", None) or DEFAULT_PORT)
    auth_token = getattr(args, "auth_token", None)
    tls_cert = getattr(args, "tls_cert", None)
    tls_key = getattr(args, "tls_key", None)
    # WHY: surface the typo-paired-flag mistake here (rather than deeper
    # in OtterServer) so the user gets a CLI-level error before any other
    # provider/MCP/LSP wiring fires.
    if bool(tls_cert) ^ bool(tls_key):
        print(
            "error: --tls-cert and --tls-key must be set together",
            file=sys.stderr,
        )
        return 2

    no_lsp = bool(getattr(args, "no_lsp", False))
    no_rules = bool(getattr(args, "no_rules", False))
    no_mcp = bool(getattr(args, "no_mcp", False))
    no_plugins = bool(getattr(args, "no_plugins", False))

    def _factory(state: OtterSessionState) -> Any:
        provider = _build_provider(model)
        workdir = state.working_dir or cwd
        env = LocalEnvironment(workdir=workdir)
        env.setup()
        config = LoopConfig()
        loop = ReAct(max_steps=max_steps, config=config)
        composed = _compose_prompt(
            "You are Otter, a Chimera coding agent driven over HTTP.",
            project_root=Path(workdir),
            no_rules=no_rules,
        )
        prompt = Prompt.from_string(composed)
        tools = _attach_lsp_tools(
            list(AGENT_TOOLS), no_lsp=no_lsp, project_root=Path(workdir),
        )
        if not no_mcp:
            tools = _attach_mcp_tools(tools, project_root=Path(workdir))
        # WHY (W2/W3 — F3): plugins augment the per-session agent. Hooks
        # accumulate in a local list and are converted into a
        # :class:`HookEmitter` wired onto ``config.hook_emitter`` so
        # PreToolUse hooks fire from :mod:`chimera.core.tool_executor`.
        # MCP descriptors are collected but not auto-spawned (W1 owns
        # that wiring step).
        plugin_hooks: list[Any] = []
        plugin_mcp_servers: list[Any] = []
        _attach_plugin_extensions(
            tools,
            plugin_hooks,
            agent_registry=None,
            project_root=Path(workdir),
            mcp_servers=plugin_mcp_servers,
            enabled=not no_plugins,
        )
        plugin_emitter = _build_plugin_hook_emitter(plugin_hooks)
        if plugin_emitter is not None and config.hook_emitter is None:
            config.hook_emitter = plugin_emitter
        return Agent(
            provider=provider,
            tools=tools,
            loop=loop,
            prompt=prompt,
        )

    scheme = "https" if (tls_cert and tls_key) else "http"
    sys.stderr.write(
        f"[otter] HTTP server listening on {scheme}://{host}:{port}\n"
    )
    sys.stderr.flush()
    return serve_http(
        _factory,
        host=host,
        port=port,
        auth_token=auth_token,
        tls_cert=tls_cert,
        tls_key=tls_key,
        # WHY (server-mgmt): write ``~/.chimera/run/otter-<port>.pid`` so a
        # separate shell can run ``chimera otter serve status`` / ``stop``
        # against this backgrounded process.
        pidfile_prefix="otter",
    )


def _dispatch_serve_acp(args: argparse.Namespace) -> int:
    """Run the ACP server on stdio.

    Builds an agent lazily per session via the same factory used by the
    one-shot ``-p`` flow. Imports are inside the function so ``--help``
    stays cheap.
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.otter.acp import OtterACPServer, serve_stdio

    cwd = os.path.abspath(args.cwd or os.getcwd())
    model = args.model
    max_steps = int(args.max_steps)

    no_lsp = bool(getattr(args, "no_lsp", False))
    no_rules = bool(getattr(args, "no_rules", False))
    no_mcp = bool(getattr(args, "no_mcp", False))
    no_plugins = bool(getattr(args, "no_plugins", False))

    def _factory(state: Any) -> Any:
        # WHY: build a fresh provider/loop/agent per ACP session so the
        # session's working_dir is honored and turn cancellation is local
        # to that session.
        from chimera.env.local import LocalEnvironment

        provider = _build_provider(model)
        workdir = state.working_dir or cwd
        env = LocalEnvironment(workdir=workdir)
        env.setup()
        config = LoopConfig()
        loop = ReAct(max_steps=max_steps, config=config)
        composed = _compose_prompt(
            "You are Otter, a Chimera coding agent driven over ACP.",
            project_root=Path(workdir),
            no_rules=no_rules,
        )
        prompt = Prompt.from_string(composed)
        tools = _attach_lsp_tools(
            list(AGENT_TOOLS), no_lsp=no_lsp, project_root=Path(workdir),
        )
        if not no_mcp:
            tools = _attach_mcp_tools(tools, project_root=Path(workdir))
        # WHY (W2/W3 — F3): mirror the HTTP factory's plugin-attach surface
        # so ACP and HTTP sessions see the same plugin set, including hook
        # wiring through ``config.hook_emitter``.
        plugin_hooks: list[Any] = []
        plugin_mcp_servers: list[Any] = []
        _attach_plugin_extensions(
            tools,
            plugin_hooks,
            agent_registry=None,
            project_root=Path(workdir),
            mcp_servers=plugin_mcp_servers,
            enabled=not no_plugins,
        )
        plugin_emitter = _build_plugin_hook_emitter(plugin_hooks)
        if plugin_emitter is not None and config.hook_emitter is None:
            config.hook_emitter = plugin_emitter
        return Agent(
            provider=provider,
            tools=tools,
            loop=loop,
            prompt=prompt,
        )

    # ``OtterACPServer`` is referenced for symmetry with tests that
    # instantiate the class directly; the CLI path uses the convenience
    # ``serve_stdio`` helper that wraps it.
    _ = OtterACPServer
    return serve_stdio(_factory)


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Wire ``chimera otter sessions [list|show <id>]`` to O3's handler.

    The wave-1 scaffold parser puts the sessions sub-action under
    ``args.sub_action`` and the optional id under ``args.sub_target``.
    O3's :func:`chimera.otter.sessions.dispatch_sessions` expects
    ``args.sessions_command="sessions"`` plus per-action filter dests
    (``sessions_since``, ``sessions_limit``, ``sessions_model``,
    ``sessions_json``, ``sessions_full``). Read raw attributes off the
    namespace, fall back to sensible defaults, and forward.

    Falls back to the old scaffold message if the sessions module fails
    to import for any reason.
    """
    try:
        from chimera.otter.sessions import dispatch_sessions
    except Exception as exc:  # noqa: BLE001
        print(
            f"otter sessions: handler unavailable ({exc})", file=sys.stderr,
        )
        return 2

    args.sessions_command = "sessions"
    args.sessions_action = getattr(args, "sub_action", None) or "list"
    args.sessions_id = getattr(args, "sub_target", None)
    # WHY (G6): the ``show`` action keys off ``sessions_target`` (the
    # SESSION_ID positional in slot 3) — bridge ``sub_target`` so the
    # dispatcher's ``getattr(args, "sessions_target")`` finds the id.
    args.sessions_target = getattr(args, "sub_target", None)
    args.sessions_since = getattr(args, "sessions_since", None)
    args.sessions_model = getattr(args, "sessions_model", None)
    # WHY: ``--sessions-limit`` (G6) lives under ``sessions_limit_flag``
    # so we don't shadow the bench ``--limit`` argparse dest. Fall back
    # to 50 when the flag is unset, matching the prior O3 behavior.
    sessions_limit = getattr(args, "sessions_limit_flag", None)
    args.sessions_limit = sessions_limit if sessions_limit is not None else 50
    args.sessions_json = getattr(args, "sessions_json", False)
    args.sessions_full = getattr(args, "sessions_full", False)
    args.sessions_format = getattr(args, "sessions_format", "text") or "text"
    # WHY (O4-W9): ``sessions rename <id> <title...>`` reuses the
    # variadic trailing positional slot (``mcp_extra``, also used by
    # ``mcp add`` for the stdio command tail) to capture the new title.
    # We join with spaces inside ``cmd_sessions_rename`` so users don't
    # have to quote multi-word titles. ``None`` when the slot wasn't
    # registered keeps non-rename actions unaffected.
    args.sessions_title = list(getattr(args, "mcp_extra", []) or []) or None
    rc = dispatch_sessions(args)
    return rc if rc is not None else 0


def _dispatch_share(args: argparse.Namespace) -> int:
    """Stub for ``chimera otter share <session>``.

    Agent O13 owns the share body. Returning 2 (usage error) keeps shell
    pipelines from silently treating an unimplemented command as success.
    """
    target = getattr(args, "sub_action", None)  # share takes the id in slot 2
    print(
        f"otter share: target={target!r} (scaffold; see "
        "research/otter/SPEC.md, agent O13).",
        file=sys.stderr,
    )
    return 2


def _dispatch_agents(args: argparse.Namespace) -> int:
    """Implement ``chimera otter agents [list|show <name>|create [<name>]]``.

    Delegates to :mod:`chimera.otter.agents` so the same project > user
    > built-in chain ``--agent <name>`` walks is what gets listed/shown,
    and so ``create`` writes into the same ``.opencode/agent/`` tree the
    discovery chain reads back.
    """
    from chimera.otter.agents import (
        cmd_agents_create,
        cmd_agents_list,
        cmd_agents_show,
    )

    no_color = bool(
        getattr(args, "no_color", False) or getattr(args, "no_rich", False)
    )
    cwd_arg = getattr(args, "cwd", None)
    cwd = Path(cwd_arg) if cwd_arg else None
    action = getattr(args, "sub_action", None)
    target = getattr(args, "sub_target", None)
    if action is None or action == "list":
        # ``otter agents`` (no action) defaults to list — friendlier than
        # a usage error for the most common inspection call.
        return cmd_agents_list(no_color=no_color, cwd=cwd)
    if action == "show":
        return cmd_agents_show(target, no_color=no_color, cwd=cwd)
    if action == "create":
        # ``target`` is the optional NAME positional; cmd_agents_create
        # treats ``None`` as "prompt for the name" and any provided
        # value as the prompt default.
        return cmd_agents_create(
            target,
            user=bool(getattr(args, "agents_user", False)),
            cwd=cwd,
        )
    print(
        f"error: unknown 'agents' action: {action!r} "
        "(supported: list, show, create)",
        file=sys.stderr,
    )
    return 2


def _dispatch_bench(args: argparse.Namespace) -> int:
    """Late-bind ``chimera otter bench`` to :mod:`chimera.otter.benchmarks`.

    Importing :mod:`chimera.otter.benchmarks` here (instead of at module
    top-level) keeps the eval-harness imports out of the ``--help`` /
    ``--version`` path, mirroring how :func:`_dispatch_agents` lazy-imports
    :mod:`chimera.otter.agents`.
    """
    from chimera.otter.benchmarks import dispatch_bench

    return dispatch_bench(args)


def _dispatch_mcp(args: argparse.Namespace) -> int:
    """Late-bind ``chimera otter mcp`` to :mod:`chimera.otter.mcp_cli`.

    Routes ``mcp list`` / ``mcp add`` / ``mcp auth``. The lazy import
    keeps the auth + credential-store dependencies out of the
    ``--help`` / ``--version`` path, symmetric with how
    :func:`_dispatch_bench` lazy-imports the benchmark module.
    """
    from chimera.otter.mcp_cli import dispatch_mcp

    return dispatch_mcp(args)


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "serve": _dispatch_serve,
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
    "agents": _dispatch_agents,
    "bench": _dispatch_bench,
    "mcp": _dispatch_mcp,
}


# ---------------------------------------------------------------------------
# TUI dispatcher (C5)
# ---------------------------------------------------------------------------


def _dispatch_tui(args: argparse.Namespace) -> int:
    """Boot the textual TUI prototype against an in-process server.

    Builds an :class:`OtterServer` whose ``agent_factory`` reuses the
    same provider/loop/tools wiring as ``--print`` / ``serve`` so the
    TUI sees the same agent surface a real client would. The server is
    *not* bound to a socket — every TUI call goes through the in-process
    Python API, which keeps the prototype self-contained and avoids
    needing a free port.

    Args:
        args: Parsed CLI namespace.

    Returns:
        Process exit code. Returns ``2`` when the optional ``[tui]``
        extra is missing so shell pipelines can detect the install gap.
    """
    try:
        from chimera.otter.tui import TUIConfig, run_tui
    except ImportError as exc:
        sys.stderr.write(f"otter --tui: {exc}\n")
        return 2

    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.otter.server import OtterServer, OtterSessionState

    cwd = os.path.abspath(args.cwd or os.getcwd())
    model = args.model
    max_steps = int(args.max_steps)
    no_lsp = bool(getattr(args, "no_lsp", False))
    no_rules = bool(getattr(args, "no_rules", False))
    no_mcp = bool(getattr(args, "no_mcp", False))
    no_plugins = bool(getattr(args, "no_plugins", False))

    def _factory(state: OtterSessionState) -> Any:
        provider = _build_provider(model)
        workdir = state.working_dir or cwd
        env = LocalEnvironment(workdir=workdir)
        env.setup()
        config = LoopConfig()
        loop = ReAct(max_steps=max_steps, config=config)
        composed = _compose_prompt(
            "You are Otter, a Chimera coding agent driven over the local TUI.",
            project_root=Path(workdir),
            no_rules=no_rules,
        )
        prompt = Prompt.from_string(composed)
        tools = _attach_lsp_tools(
            list(AGENT_TOOLS), no_lsp=no_lsp, project_root=Path(workdir),
        )
        if not no_mcp:
            tools = _attach_mcp_tools(tools, project_root=Path(workdir))
        plugin_hooks: list[Any] = []
        plugin_mcp_servers: list[Any] = []
        _attach_plugin_extensions(
            tools,
            plugin_hooks,
            agent_registry=None,
            project_root=Path(workdir),
            mcp_servers=plugin_mcp_servers,
            enabled=not no_plugins,
        )
        plugin_emitter = _build_plugin_hook_emitter(plugin_hooks)
        if plugin_emitter is not None and config.hook_emitter is None:
            config.hook_emitter = plugin_emitter
        return Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)

    server = OtterServer(_factory)
    cfg = TUIConfig(model=model, working_dir=cwd)
    return run_tui(server, cfg)



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@friendly_errors
def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera otter``.

    Args:
        args: Parsed ``argparse.Namespace`` from the otter subparser.

    Returns:
        Process exit code (``0`` on success).
    """
    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    if args.print_mode is not None:
        return _run_print_mode(args)

    # WHY (C5): ``--tui`` boots the textual-based TUI prototype against
    # an in-process :class:`OtterServer`. The TUI is opt-in (gated by
    # the ``[tui]`` extra) so the readline REPL stays the default and
    # the import cost of textual only fires when the user asks for it.
    if bool(getattr(args, "tui", False)):
        return _dispatch_tui(args)

    # No print, no subcommand — emit a brief usage hint pointing at the
    # interactive REPL placeholder (agent O2). Returning 0 here would mask
    # "user forgot -p"; returning 2 (usage) is the conventional answer.
    print(
        "otter: interactive REPL not yet wired in this scaffold. "
        "Use --print/-p PROMPT for one-shot mode, --version for version, "
        "or --help for the full flag list. "
        "(see research/otter/SPEC.md, agent O2).",
        file=sys.stderr,
    )
    return 2


__all__ = [
    "add_arguments",
    "run",
]
