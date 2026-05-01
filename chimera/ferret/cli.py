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
_VALID_SUBCOMMANDS = (
    None,
    "serve",
    "sessions",
    "share",
    "agents",
    "bench",
    "bridge",
)
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
    # WHY (F1/W8): TLS pair gates the HTTP path off-localhost so the
    # bearer token is never exposed in cleartext. Both halves must be
    # set together; ``_dispatch_serve_http`` rejects half-pairs at the
    # CLI layer (mirrors ``chimera otter serve``'s contract).
    parser.add_argument(
        "--tls-cert",
        dest="tls_cert",
        default=None,
        help=(
            "With 'serve --http': path to a PEM-encoded server "
            "certificate. Must be paired with --tls-key. When set the "
            "server speaks HTTPS and Authorization headers stay encrypted."
        ),
    )
    parser.add_argument(
        "--tls-key",
        dest="tls_key",
        default=None,
        help=(
            "With 'serve --http': path to a PEM-encoded private key "
            "matching --tls-cert."
        ),
    )
    # WHY (FF5): cloud-bridge flags. ``--remote-url`` selects the HTTPS
    # base URL of the remote bridge service; ``--bridge-token`` supplies
    # the bearer token (falls back to ``$FERRET_BRIDGE_TOKEN``). Both are
    # consumed by ``chimera.ferret.cloud_bridge.build_bridge_from_args``.
    parser.add_argument(
        "--remote-url",
        default=None,
        help=(
            "With 'bridge': HTTPS base URL of the remote bridge service "
            "(default: see chimera.ferret.cloud_bridge.DEFAULT_REMOTE_URL, "
            "which points at a placeholder .invalid domain — operators "
            "must opt in to a real remote)."
        ),
    )
    parser.add_argument(
        "--bridge-token",
        default=None,
        help=(
            "With 'bridge': shared-secret bearer token sent on every "
            "request as 'Authorization: Bearer <token>'. Falls back to "
            "$FERRET_BRIDGE_TOKEN."
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

    FF4 owns the IDE-first ACP schema; the HTTP variant is opt-in. The
    real ACP server is reached via :func:`chimera.ferret.ide.maybe_serve_ide_acp`
    (late-bound). When ``--http`` is set, F1/W8 wires the HTTP + SSE
    server by delegating to :func:`chimera.otter.server.serve_http` with
    a ferret-flavored agent factory (provider via FF6, sandbox via FF2,
    approval via FF3).
    """
    if not getattr(args, "http", False):
        # WHY: ACP is the IDE-first default. Late-bind so cli.py loads even
        # if FF4 hasn't shipped, and so ``--help`` stays cheap.
        try:
            from chimera.ferret.ide import maybe_serve_ide_acp
        except Exception as exc:  # noqa: BLE001
            print(
                f"ferret serve: ACP transport unavailable ({exc}). "
                "Pass --http for the HTTP server.",
                file=sys.stderr,
            )
            return 2
        rc = maybe_serve_ide_acp(args)
        if rc is not None:
            return int(rc)
        # ACP module declined (rc=None): fall through to HTTP. Rare; the
        # current ACP helper only returns ``None`` when ``--http`` is set,
        # which is already handled above.
        return _dispatch_serve_http(args)
    return _dispatch_serve_http(args)


# Default HTTP bind port for ``chimera ferret serve --http``. Distinct from
# the otter default (5173) so the two servers can coexist on a single host.
_FERRET_DEFAULT_HTTP_PORT = 5174


def _dispatch_serve_http(args: argparse.Namespace) -> int:
    """Run the HTTP + SSE ferret server.

    Thin wrapper around :func:`chimera.otter.server.serve_http`. The
    server protocol (``/healthz``, ``/session``, SSE event stream,
    ``/tool/approve``) is identical to otter's; what differs is the
    per-session agent factory: ferret routes through its own provider
    chain (FF6), sandbox wrapper (FF2), and approval preset (FF3).

    All heavy imports (``Agent``, ``ReAct``, ``LocalEnvironment``,
    ``OtterSessionState``) stay inside the function so ``chimera ferret
    --help`` and ``chimera ferret serve --help`` remain cheap.

    Args:
        args: Parsed argparse namespace. Reads ``host``, ``port``,
            ``auth_token``, ``tls_cert``, ``tls_key``, ``model``,
            ``cwd``, ``max_steps``, ``sandbox``, ``approval``.

    Returns:
        Process exit code: 0 on graceful shutdown, 2 on usage error
        (e.g. half-paired ``--tls-cert`` / ``--tls-key``).
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.otter.server import (
        DEFAULT_HOST,
        OtterSessionState,
        serve_http,
    )

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    model = getattr(args, "model", None) or _DEFAULT_MODEL
    max_steps = int(getattr(args, "max_steps", 50) or 50)

    host = str(getattr(args, "host", None) or DEFAULT_HOST)
    port = int(getattr(args, "port", None) or _FERRET_DEFAULT_HTTP_PORT)
    auth_token = getattr(args, "auth_token", None)
    tls_cert = getattr(args, "tls_cert", None)
    tls_key = getattr(args, "tls_key", None)
    # WHY: surface the typo-paired-flag mistake here so the user gets a
    # CLI-level error before any provider/sandbox wiring fires. Mirrors
    # otter's ``_dispatch_serve_http`` contract.
    if bool(tls_cert) ^ bool(tls_key):
        print(
            "error: --tls-cert and --tls-key must be set together",
            file=sys.stderr,
        )
        return 2

    sandbox_value = getattr(args, "sandbox", "read-only") or "read-only"
    approval_value = getattr(args, "approval", "read-only") or "read-only"

    def _factory(state: OtterSessionState) -> Any:
        # Provider — late-bind FF6 so the factory uses the ferret chain
        # (gpt-5 → gpt-4o → claude-sonnet-4-6 → openrouter), with a
        # generic fallback when FF6 is absent.
        provider = _build_provider(model)

        # Environment — wrap LocalEnvironment with the ferret sandbox
        # (FF2) per ``--sandbox``. Falls through to the unsandboxed env
        # when the sandbox module isn't importable, matching the print-
        # mode contract.
        workdir = state.working_dir or cwd
        base_env = LocalEnvironment(workdir=workdir)
        base_env.setup()
        env: Any = base_env
        try:
            from chimera.ferret import sandbox as _sandbox_mod

            mode = _sandbox_mod.parse_sandbox_mode(sandbox_value)
            env = _sandbox_mod.SandboxedEnvironment(base_env, mode=mode)
        except Exception:  # noqa: BLE001 - keep base env on missing/error
            env = base_env

        # Approval preset (FF3) → LoopConfig.permissions.
        permissions: Any = None
        try:
            from chimera.ferret import approval as _approval_mod

            permissions = _approval_mod.policy_for_preset(
                _approval_mod.preset_from_string(approval_value)
            )
        except Exception:  # noqa: BLE001 - default LoopConfig on miss
            permissions = None

        config = LoopConfig(permissions=permissions)
        loop = ReAct(max_steps=max_steps, config=config)
        prompt = Prompt.from_string(
            "You are Ferret, a Chimera coding agent driven over HTTP."
        )
        agent = Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=loop,
            prompt=prompt,
        )
        # Surface the sandboxed env onto the agent so future tool calls
        # routed through ``state.agent`` honor the per-session sandbox.
        # OtterServer's ``_drive_agent`` passes ``env=None`` (the agent
        # carries its own env reference), so we attach via attribute for
        # downstream tooling that expects ``agent.env``.
        try:
            agent.env = env  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - best-effort, never crash factory
            pass
        return agent

    scheme = "https" if (tls_cert and tls_key) else "http"
    sys.stderr.write(
        f"[ferret] HTTP server listening on {scheme}://{host}:{port}\n"
    )
    sys.stderr.flush()
    return serve_http(
        _factory,
        host=host,
        port=port,
        auth_token=auth_token,
        tls_cert=tls_cert,
        tls_key=tls_key,
    )


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
    """Wire ``chimera ferret agents [list|show <name>]`` to the FF7 handlers.

    Routes through :func:`chimera.ferret.agents.cmd_agents_list` and
    :func:`chimera.ferret.agents.cmd_agents_show`. The handler module is
    late-bound so a missing ``chimera.ferret.agents`` falls back to a
    scaffold message with rc=2.
    """
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    no_color = bool(getattr(args, "no_color", False) or getattr(args, "no_rich", False))
    try:
        from chimera.ferret.agents import cmd_agents_list, cmd_agents_show
    except Exception as exc:  # noqa: BLE001
        print(
            f"ferret agents: handler unavailable ({exc}). action={action!r} "
            f"target={target!r}.",
            file=sys.stderr,
        )
        return 2
    # Status line — keeps the "ferret agents" tag in stderr so callers /
    # CI greps that key off the prefix continue to work.
    print(
        f"ferret agents: action={action!r} target={target!r}",
        file=sys.stderr,
    )
    if action == "list":
        return int(cmd_agents_list(no_color=no_color))
    if action == "show":
        return int(cmd_agents_show(target, no_color=no_color))
    print(
        f"ferret agents: unknown action {action!r} (use 'list' or 'show').",
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


def _default_bridge_inbound_handler(message: Any) -> None:
    """Default no-op ``inbound_handler`` used by ``ferret bridge``.

    The bridge spec leaves wiring of inbound prompts to the local agent
    as a wave-9 concern (live REPL attachment). Until that lands, the
    CLI dispatcher uses a stderr-logging handler so operators can verify
    the round-trip without a live agent. The handler stays synchronous
    on purpose — the bridge owns its own daemon thread.
    """
    text = getattr(message, "text", "")
    msg_id = getattr(message, "message_id", "")
    print(
        f"[ferret bridge] inbound message_id={msg_id!r} text={text!r}",
        file=sys.stderr,
    )


def _dispatch_bridge(args: argparse.Namespace) -> int:
    """Dispatch ``chimera ferret bridge`` to the FF5 cloud-bridge runner.

    Reads ``--remote-url`` and ``--bridge-token`` (with fallbacks
    documented in :mod:`chimera.ferret.cloud_bridge`), connects, and
    blocks on the inbound poll loop until ``Ctrl-C``. Late-binds the
    cloud-bridge module so an absent FF5 surfaces a friendly error
    rather than an :class:`ImportError` traceback.

    Returns:
        Process exit code: 0 on graceful shutdown, 2 on auth failure or
        when FF5 is missing, 1 on any other bridge-level error.
    """
    try:
        from chimera.ferret import cloud_bridge as _cloud_bridge
    except Exception as exc:  # noqa: BLE001
        print(
            f"ferret bridge: cloud-bridge module unavailable ({exc}). "
            "See research/ferret/SPEC.md (FF5).",
            file=sys.stderr,
        )
        return 2
    return int(_cloud_bridge.run_bridge(args, _default_bridge_inbound_handler))


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "serve": _dispatch_serve,
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
    "agents": _dispatch_agents,
    "bench": _dispatch_bench,
    "bridge": _dispatch_bridge,
}


# ---------------------------------------------------------------------------
# One-shot --print path with sandbox + approval + provider wiring
# ---------------------------------------------------------------------------


def _run_print_mode(args: argparse.Namespace) -> int:
    """Run ``chimera ferret -p PROMPT`` with full sandbox + approval wiring.

    This is the wave-6 "live-driven" one-shot path: it wraps the
    :class:`~chimera.env.local.LocalEnvironment` with a
    :class:`~chimera.ferret.sandbox.SandboxedEnvironment` per ``--sandbox``
    and constructs a :class:`~chimera.core.loop_config.LoopConfig` whose
    :attr:`permissions` slot is populated from
    :func:`chimera.ferret.approval.policy_for_preset` per ``--approval``.
    The provider is resolved through
    :func:`chimera.ferret.providers.build_provider` (FF6) so the OpenAI-
    flagship chain (gpt-5 → gpt-4o → claude-sonnet-4-6 → openai/gpt-5
    via OpenRouter, plus ``:cloud`` Ollama tags) is honored.

    Late-binds every sibling import so an absent module degrades to a
    sensible default rather than crashing the runner.

    Args:
        args: Parsed ferret namespace; reads ``print_mode``, ``model``,
            ``cwd``, ``max_steps``, ``output_format``, ``sandbox``,
            ``approval``.

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

    # 1. Provider (FF6) — late-bind, fall back to generic factory.
    _providers_mod: Any = None
    try:
        import chimera.ferret.providers as _providers_mod  # noqa: F811
    except Exception:  # noqa: BLE001
        _providers_mod = None
    if _providers_mod is not None and hasattr(_providers_mod, "build_provider"):
        try:
            provider = _providers_mod.build_provider(args)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        from chimera.providers.factory import create_provider

        provider = create_provider(model=getattr(args, "model", None))

    # 2. Environment + sandbox (FF2) — wrap LocalEnvironment when available.
    base_env = LocalEnvironment(workdir=cwd)
    base_env.setup()
    env: Any = base_env
    _sandbox_mod: Any = None
    try:
        import chimera.ferret.sandbox as _sandbox_mod  # noqa: F811 — module ref.
    except Exception:  # noqa: BLE001 — FF2 not present; keep LocalEnvironment.
        _sandbox_mod = None
    if _sandbox_mod is not None and hasattr(_sandbox_mod, "SandboxedEnvironment"):
        try:
            mode = _sandbox_mod.parse_sandbox_mode(
                getattr(args, "sandbox", "read-only")
            )
            env = _sandbox_mod.SandboxedEnvironment(base_env, mode=mode)
        except Exception as exc:  # noqa: BLE001 — keep base env on parse error.
            print(
                f"[ferret] --sandbox {getattr(args, 'sandbox', None)!r} "
                f"unrecognised ({exc}); falling back to unsandboxed env.",
                file=sys.stderr,
            )

    # 3. Approval (FF3) — populate LoopConfig.permissions from preset.
    permissions: Any = None
    _approval_mod: Any = None
    try:
        import chimera.ferret.approval as _approval_mod  # noqa: F811 — module ref.
    except Exception:  # noqa: BLE001 — FF3 not present; default LoopConfig.
        _approval_mod = None
    approval_value = getattr(args, "approval", "read-only") or "read-only"
    if _approval_mod is not None and hasattr(_approval_mod, "policy_for_preset"):
        try:
            permissions = _approval_mod.policy_for_preset(
                _approval_mod.preset_from_string(approval_value)
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[ferret] --approval {approval_value!r} unrecognised "
                f"({exc}); falling back to default policy.",
                file=sys.stderr,
            )

    cancel = CancellationToken()
    config = LoopConfig(cancellation=cancel, permissions=permissions)
    loop = ReAct(
        max_steps=int(getattr(args, "max_steps", 50) or 50),
        config=config,
    )
    base_prompt = (
        "You are Ferret, a Chimera coding agent. Plan briefly, then act."
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
        result = asyncio.run(agent.async_run(prompt_text, env=env))
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
        # Wave-6: full one-shot path with sandbox + approval + provider
        # wiring. Falls back to the wave-5 REPL one-shot only if the
        # internal entry point fails to import (defence in depth).
        try:
            return int(_run_print_mode(args))
        except Exception as exc:  # noqa: BLE001
            print(
                f"ferret -p: one-shot path failed ({exc}). "
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
