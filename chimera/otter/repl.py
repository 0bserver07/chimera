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
    "install_snapshot_hooks",
    "load_otter_custom_commands",
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
# Custom command loading (.opencode/command/*.md -> slash registry)
# ---------------------------------------------------------------------------

def load_otter_custom_commands(
    project_root: Path | str | None = None,
) -> list[Any]:
    """Load custom slash commands from user + project ``.opencode/command/``.

    Wraps :func:`chimera.otter.commands.load_custom_commands` so callers
    in this module (and tests) can pull the project-aware merged set
    without re-implementing the precedence walk. Returns an empty list
    when the commands module isn't importable yet so the REPL stays
    usable during partial-install scenarios.

    Args:
        project_root: Project root path. Defaults to ``os.getcwd()`` when
            ``None`` so callers don't need to pre-resolve the cwd.

    Returns:
        A list of :class:`~chimera.otter.commands.CustomCommand`
        instances ordered by name. Empty when no files exist or the
        commands module isn't present.
    """
    try:
        from chimera.otter import commands as _otter_commands  # type: ignore[attr-defined]
    except ImportError:
        return []

    loader = getattr(_otter_commands, "load_custom_commands", None)
    if loader is None:
        return []
    root = Path(project_root) if project_root is not None else Path(os.getcwd())
    try:
        merged = loader(root)
    except Exception as exc:  # noqa: BLE001 -- never crash REPL
        print(
            f"[otter] custom-command load failed: {exc}",
            file=sys.stderr,
        )
        return []
    return [merged[name] for name in sorted(merged)]


# ---------------------------------------------------------------------------
# /undo + /redo snapshot wiring (collaborates with F6)
# ---------------------------------------------------------------------------
#
# F6 shipped a real ``/undo`` and ``/redo`` palette backed by per-session
# checkpoint stacks, but the contract requires the REPL to call
# :func:`chimera.otter.slash.snapshot_after_turn` once at session start
# (baseline) and once after every assistant turn. Without that, the stacks
# stay empty and the slash commands fall through to "nothing to undo".
#
# This module owns the wiring. :func:`install_snapshot_hooks` is the single
# entry point: it captures the baseline snap immediately and wraps the
# session's ``iter_chat`` / ``chat`` methods so each subsequent turn snaps
# its post-turn state. The wrapping is idempotent — re-installing on the
# same session is a no-op so partial-init failures don't double-snap.

# Sentinel attribute marking a session whose iter_chat/chat methods have
# already been wrapped by :func:`install_snapshot_hooks`. Idempotent
# semantics: calling the installer twice is a no-op the second time.
_OTTER_SNAPSHOT_INSTALLED_ATTR = "_otter_snapshot_hooks_installed"


def install_snapshot_hooks(session: Any, env: Any) -> int:
    """Wire ``snapshot_after_turn`` calls onto *session* for the lifetime of the REPL.

    F6's contract:

    1. ``snapshot_after_turn(session, env)`` is called ONCE at session
       start so ``/undo`` from the empty stack returns to the pristine
       pre-conversation state.
    2. ``snapshot_after_turn(session, env)`` is called ONCE after every
       assistant turn so the turn's filesystem mutations + message
       deltas are captured on the undo stack.

    This helper achieves both by (a) taking the baseline snap inline and
    (b) wrapping ``session.iter_chat`` and ``session.chat`` so each
    invocation triggers a fresh snap on completion. The wrappers preserve
    the original return type — :meth:`Session.chat` is a regular function
    and :meth:`Session.iter_chat` is a generator that yields steps and
    returns the final :class:`AgentResult`. Both are wrapped without
    losing semantics.

    Args:
        session: The active Chimera session. Must expose ``iter_chat``
            and/or ``chat`` for the per-turn hook to fire. The slash
            module's :func:`chimera.otter.slash.snapshot_after_turn` is
            duck-typed via ``id(session)`` so any object with a stable
            identity works as a key (the REPL passes a real
            :class:`chimera.sessions.session.Session`; the HTTP server
            passes its :class:`OtterSessionState`).
        env: The environment paired with *session*. May be ``None`` for
            sessions without a filesystem (a sentinel ``CheckpointInfo``
            backs message-only undo in that case).

    Returns:
        The number of session methods wrapped (``2`` when both
        ``iter_chat`` and ``chat`` are present, ``1`` when only one is
        present, ``0`` when neither exists — message-only baseline still
        lands in that case).

    The wrap is idempotent: calling this a second time on the same
    session installs nothing and returns ``0``.
    """
    # Late import keeps the slash module optional — same hygiene as the
    # rest of the otter REPL module.
    from chimera.otter import slash as _slash

    if getattr(session, _OTTER_SNAPSHOT_INSTALLED_ATTR, False):
        return 0

    # 1. Baseline snapshot — fires unconditionally so the deepest /undo
    #    can return the user to a pristine pre-conversation state. F6's
    #    snapshot_after_turn is itself idempotent on baseline (it only
    #    captures ``initial_messages`` once), so calling it here before
    #    any user turn lands the right thing.
    try:
        _slash.snapshot_after_turn(session, env)
    except Exception as exc:  # noqa: BLE001 - never crash the REPL
        print(
            f"[otter] baseline snapshot failed: {exc}",
            file=sys.stderr,
        )

    wrapped = 0

    # 2. Wrap iter_chat — the path used by ``chimera code``'s interactive
    #    REPL (it iterates per step and drains to a final AgentResult).
    iter_chat = getattr(session, "iter_chat", None)
    if callable(iter_chat):
        original_iter_chat = iter_chat

        def _iter_chat_with_snap(message: str) -> Any:
            gen = original_iter_chat(message)
            try:
                while True:
                    yield next(gen)
            except StopIteration as stop:
                # ``Session.iter_chat`` returns the final AgentResult via
                # StopIteration.value (standard generator return contract).
                # Snap once the turn fully completes, then propagate.
                try:
                    _slash.snapshot_after_turn(session, env)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[otter] post-turn snapshot failed: {exc}",
                        file=sys.stderr,
                    )
                return stop.value

        try:
            session.iter_chat = _iter_chat_with_snap  # type: ignore[method-assign]
            wrapped += 1
        except (AttributeError, TypeError):
            # Some duck-typed test fakes refuse method assignment. Fall
            # through silently — the baseline snap still landed.
            pass

    # 3. Wrap chat — the synchronous one-shot path (legacy / tests).
    chat = getattr(session, "chat", None)
    if callable(chat):
        original_chat = chat

        def _chat_with_snap(message: str) -> Any:
            try:
                return original_chat(message)
            finally:
                try:
                    _slash.snapshot_after_turn(session, env)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[otter] post-turn snapshot failed: {exc}",
                        file=sys.stderr,
                    )

        try:
            session.chat = _chat_with_snap  # type: ignore[method-assign]
            wrapped += 1
        except (AttributeError, TypeError):
            pass

    try:
        setattr(session, _OTTER_SNAPSHOT_INSTALLED_ATTR, True)
    except (AttributeError, TypeError):
        pass

    return wrapped


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
    shimmed = argparse.Namespace(
        model=getattr(args, "model", None),
        workdir=cwd,
        max_steps=getattr(args, "max_steps", 50) or 50,
        mode="interactive",
        models=getattr(args, "models", "") or "",
        preset=getattr(args, "agent", None) or getattr(args, "preset", None),
        print_mode=None,
    )
    # Wire the F6 ``/undo`` + ``/redo`` snapshot stack: ``run_code`` calls
    # this hook with ``(session, env)`` immediately after building the
    # session so the baseline snap + per-turn snaps fire from the otter
    # REPL without forking the shared interactive loop.
    shimmed._post_session_init = install_snapshot_hooks  # type: ignore[attr-defined]
    return shimmed


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
    from chimera.otter.cli import (
        _attach_lsp_tools,
        _attach_mcp_tools,
        _attach_plugin_extensions,
        _build_plugin_hook_emitter,
        _compose_prompt,
    )

    resolved_provider = provider if provider is not None else _build_otter_provider(args)
    max_steps = int(getattr(args, "max_steps", 50) or 50)
    config = LoopConfig()
    loop = ReAct(max_steps=max_steps, config=config)
    cwd = os.path.abspath(
        getattr(args, "cwd", None) or getattr(args, "workdir", None) or os.getcwd(),
    )
    composed = _compose_prompt(
        "You are an interactive coding assistant in the otter REPL. Use "
        "the available tools to read, edit, search, and run code. Be "
        "concise and direct.",
        project_root=Path(cwd),
        no_rules=bool(getattr(args, "no_rules", False)),
    )
    prompt = Prompt.from_string(composed)
    tools = _attach_lsp_tools(
        list(AGENT_TOOLS),
        no_lsp=bool(getattr(args, "no_lsp", False)),
        project_root=Path(cwd),
    )
    # WHY: wire MCP server discovery into the REPL agent factory. Symmetric
    # with the ``-p`` / ``serve`` paths in :mod:`chimera.otter.cli` — MCP is
    # on by default; ``--no-mcp`` opts out.
    if not bool(getattr(args, "no_mcp", False)):
        tools = _attach_mcp_tools(tools, project_root=Path(cwd))
    # WHY (W2/W3 — F3): plugin contributions are wired at the same call
    # site as MCP/LSP. Hooks accumulate locally and then convert into a
    # :class:`HookEmitter` wired onto ``config.hook_emitter`` so
    # PreToolUse hooks fire through :mod:`chimera.core.tool_executor`
    # the same way mink's settings hooks do.
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
    return Agent(
        provider=resolved_provider,
        tools=tools,
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

    # 2b. Load user-defined slash commands from .opencode/command/*.md and
    #     register them on the shared slash registry so the REPL picks
    #     them up via the same dispatch path as the built-in palette.
    #     ``--no-custom-commands`` opts out for locked-down environments.
    if not getattr(args, "no_custom_commands", False):
        try:
            from chimera.cli import slash_commands as _shared_slash
            from chimera.otter.slash import register_custom_commands

            cwd = (
                getattr(args, "cwd", None)
                or getattr(args, "workdir", None)
                or os.getcwd()
            )
            customs = load_otter_custom_commands(cwd)
            if customs:
                installed = register_custom_commands(_shared_slash, customs)
                if installed and not getattr(args, "_quiet_run_dir", False):
                    print(
                        f"[otter] loaded {installed} custom command(s) "
                        "from .opencode/command/"
                    )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[otter] custom commands not registered: {exc}",
                file=sys.stderr,
            )

    # 2c. Wire directory plugins (W2). Plugin agents land on the otter
    #     AgentRegistry so ``--agent <name>`` (and the ``/agents`` slash)
    #     resolve them; plugin slash commands install on the shared
    #     registry; plugin hooks/MCP descriptors are recorded for the
    #     downstream factory call (build_otter_agent).
    if not bool(getattr(args, "no_plugins", False)):
        try:
            from chimera.otter.cli import _attach_plugin_extensions

            project_root = Path(
                getattr(args, "cwd", None)
                or getattr(args, "workdir", None)
                or os.getcwd()
            )
            agent_registry: Any | None = None
            try:
                from chimera.agents.loader import create_default_registry

                agent_registry = create_default_registry()
            except Exception:  # noqa: BLE001  (registry optional)
                agent_registry = None

            tools_sink: list[Any] = []
            hooks_sink: list[Any] = []
            mcp_sink: list[Any] = []
            plugins_loaded = _attach_plugin_extensions(
                tools_sink,
                hooks_sink,
                agent_registry=agent_registry,
                project_root=project_root,
                mcp_servers=mcp_sink,
                enabled=True,
            )
            if plugins_loaded and not getattr(args, "_quiet_run_dir", False):
                print(
                    f"[otter] loaded {len(plugins_loaded)} plugin(s) from "
                    "~/.opencode/plugin/ + .opencode/plugin/"
                )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[otter] plugins not registered: {exc}",
                file=sys.stderr,
            )

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
