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
_VALID_SUBCOMMANDS = (None, "serve", "sessions", "share", "agents", "bench")
# WHY (O18): ``bench`` repurposes the ``sub_action`` positional slot for
# the benchmark name (``humaneval`` / ``tau-bench``). The choices below
# are the union of all sub_action shapes any otter subcommand accepts so
# argparse keeps validating the slot consistently across handlers.
_VALID_SUB_ACTIONS = (None, "list", "show", "humaneval", "tau-bench")


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
    # WHY (O18): bench-specific flags. Kept under their own ``--bench-*``
    # prefix so ``otter bench humaneval --limit 20`` is unambiguous against
    # the future ``otter sessions list --limit 20`` surface.
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
) -> Path:
    """Write a ``summary.json`` next to the eventlog for quick inspection.

    Schema mirrors :func:`chimera.mink.cli._write_run_summary` so the
    ``chimera mink runs list`` viewer (and any future otter equivalent)
    can read both flavors with the same parser.

    Returns:
        The path to the written ``summary.json``.
    """
    payload = {
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
    """Construct a Provider for *model* via the chimera factory.

    Lazy import of :func:`chimera.providers.factory.create_provider` keeps
    SDK imports out of the ``--help`` / ``--version`` path.

    Args:
        model: Model identifier (e.g. ``claude-sonnet-4-6`` / ``gpt-4o`` /
            ``gemini-2.0-flash``). Provider type is auto-detected.

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.
    """
    from chimera.providers.factory import create_provider

    return create_provider(model=model)


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
    prompt = Prompt.from_string(base_prompt)

    tools = list(AGENT_TOOLS)
    allowed = (getattr(args, "allowed_tools", "") or "").strip()
    if allowed:
        try:
            tools = _filter_allowed_tools(tools, allowed)
        except _UnknownAllowedTool as exc:
            print(str(exc), file=sys.stderr)
            env.cleanup()
            return 2

    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)

    save_enabled = not getattr(args, "no_save", False)
    run_id: str | None = None
    run_dir: Path | None = None
    log: Any | None = None
    started_at = _utc_iso8601()
    if save_enabled:
        run_id = getattr(args, "run_id", None) or _make_run_id()
        log, run_dir = _open_run_log(run_id)
        _append_user_message(log, args.print_mode)

    if args.output_format == "stream-json":
        return _run_stream_json(
            agent,
            env,
            args.print_mode,
            cancel,
            log=log,
            run_id=run_id,
            run_dir=run_dir,
            started_at=started_at,
            model=provider.model_name,
            cwd=cwd,
        )

    result: Any = None
    try:
        result = asyncio.run(agent.async_run(args.print_mode, env=env))
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
    """Dispatch ``chimera otter serve`` to ACP (O6) or HTTP (O14).

    When ``--acp`` is set, run the stdio JSON-RPC ACP server. Otherwise
    boot the HTTP + SSE server defined in :mod:`chimera.otter.server`.
    """
    if getattr(args, "acp", False):
        return _dispatch_serve_acp(args)
    return _dispatch_serve_http(args)


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

    def _factory(state: OtterSessionState) -> Any:
        provider = _build_provider(model)
        env = LocalEnvironment(workdir=state.working_dir or cwd)
        env.setup()
        config = LoopConfig()
        loop = ReAct(max_steps=max_steps, config=config)
        prompt = Prompt.from_string(
            "You are Otter, a Chimera coding agent driven over HTTP."
        )
        return Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=loop,
            prompt=prompt,
        )

    sys.stderr.write(
        f"[otter] HTTP server listening on http://{host}:{port}\n"
    )
    sys.stderr.flush()
    return serve_http(
        _factory, host=host, port=port, auth_token=auth_token
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

    def _factory(state: Any) -> Any:
        # WHY: build a fresh provider/loop/agent per ACP session so the
        # session's working_dir is honored and turn cancellation is local
        # to that session.
        from chimera.env.local import LocalEnvironment

        provider = _build_provider(model)
        env = LocalEnvironment(workdir=state.working_dir or cwd)
        env.setup()
        config = LoopConfig()
        loop = ReAct(max_steps=max_steps, config=config)
        prompt = Prompt.from_string(
            "You are Otter, a Chimera coding agent driven over ACP."
        )
        return Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=loop,
            prompt=prompt,
        )

    # ``OtterACPServer`` is referenced for symmetry with tests that
    # instantiate the class directly; the CLI path uses the convenience
    # ``serve_stdio`` helper that wraps it.
    _ = OtterACPServer
    return serve_stdio(_factory)


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Stub for ``chimera otter sessions [list|show <id>]``.

    Agent O3 owns sessions; the scaffold acknowledges the action so the
    CLI surface stays predictable.
    """
    action = getattr(args, "sub_action", None)
    target = getattr(args, "sub_target", None)
    print(
        f"otter sessions: action={action!r} target={target!r} "
        "(scaffold; see research/otter/SPEC.md, agent O3).",
        file=sys.stderr,
    )
    return 2


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
    """Implement ``chimera otter agents [list|show <name>]``.

    Delegates to :mod:`chimera.otter.agents` so the same project > user
    > built-in chain ``--agent <name>`` walks is what gets listed/shown.
    """
    from chimera.otter.agents import cmd_agents_list, cmd_agents_show

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
    print(
        f"error: unknown 'agents' action: {action!r} "
        "(supported: list, show)",
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
