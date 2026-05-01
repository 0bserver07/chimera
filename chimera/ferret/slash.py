"""Ferret slash-command palette.

This module defines the ferret REPL's slash-command set, mirroring the
upstream IDE-first / sandbox-first coding agent's command palette while
reusing Chimera's shared :mod:`chimera.cli.slash_commands` infrastructure
for the canonical handlers (``/help``, ``/model``, ``/cost``, ...).

The palette:

* Includes every command needed for parity with the upstream agent's
  command dialog: ``/help``, ``/exit`` (+ ``/quit``), ``/share``,
  ``/agent`` (+ ``/agents``), ``/model`` (+ ``/models``), ``/init``,
  ``/sessions`` (+ ``/new``, ``/clear``), ``/cost``, ``/tools``,
  ``/undo``, ``/redo``, ``/yolo``.
* Adds the **ferret-specific** trio:

  - ``/sandbox`` toggles the sandbox mode mid-session
    (``read-only`` -> ``workspace-write`` -> ``workspace-write-network``).
  - ``/approval`` toggles the approval preset
    (``read-only`` -> ``auto`` -> ``full``).
  - ``/diff`` shows pending file diffs the running agent has produced
    relative to the session-start baseline.

* Reuses the shared registry's handler whenever Chimera already has a
  near-equivalent (e.g. ferret ``/agents`` -> shared ``cmd_agent``).
* Ships friendly placeholder stubs for commands whose backing
  subsystems are owned by sibling F-agents in the wave-5 build (share,
  sessions list). Stubs print ``not yet wired (owner: F<n>)`` rather
  than raising so a bare ferret REPL is still useful end-to-end.

Two public surfaces are exposed:

* :data:`FERRET_SLASH_COMMANDS` — ``{name: handler}`` dict that the
  prompt-spec asks for; mirrors the wider Chimera slash-command
  contract (``handler(session, env, args, out)``).
* :data:`COMMANDS` — same dict, under the alias the ferret REPL wires
  in :mod:`chimera.ferret.repl` when merging overrides into the shared
  registry.

And one installer:

* :func:`register_ferret_slash` — install every command on a REPL state
  object that exposes a ``register(name, handler, help_text)`` method
  (the shape of :mod:`chimera.cli.slash_commands`). When the state
  object lacks ``register``, we fall back to writing into a
  ``commands`` / ``slash_commands`` mapping, then to setattr — so the
  helper composes with both the shared registry and tiny test fakes.

Trademark hygiene: this module deliberately uses neutral phrasing
("the upstream coding agent") in any user-visible string, per
``research/ferret/SPEC.md``.
"""
from __future__ import annotations

import copy
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.checkpoints import CheckpointInfo, CheckpointManager
    from chimera.ferret.commands import CustomCommand

__all__ = [
    "COMMANDS",
    "FERRET_SLASH_COMMANDS",
    "FERRET_SLASH_HELP",
    "PrintFn",
    "SlashHandler",
    "build_custom_command_handler",
    "clear_undo_state",
    "cmd_approval",
    "cmd_diff",
    "cmd_help",
    "cmd_sandbox",
    "get_command_origin",
    "get_undo_state",
    "mark_origin",
    "register_custom_commands",
    "register_ferret_slash",
    "register_plugin_commands",
    "snapshot_after_turn",
]


PrintFn = Callable[[str], None]
SlashHandler = Callable[[Any, Any, str, PrintFn], None]


# ---------------------------------------------------------------------------
# Shared-registry passthroughs
# ---------------------------------------------------------------------------
#
# Most ferret commands map directly onto handlers Chimera already ships in
# :mod:`chimera.cli.slash_commands`. We re-export those handlers (rather than
# re-implementing them) so behaviour stays in lockstep with ``chimera code``,
# ``chimera mink``, and ``chimera otter``.

from chimera.cli.slash_commands import (  # noqa: E402 -- intentional after docstring
    cmd_compact as _cmd_compact,
    cmd_config as _cmd_config,
    cmd_cost as _cmd_cost,
    cmd_doctor as _cmd_doctor,
    cmd_help as _cmd_help,
    cmd_status as _cmd_status,
)

# ``cmd_agent``, ``cmd_clear``, ``cmd_exit``, ``cmd_init``, ``cmd_model``,
# ``cmd_session``, ``cmd_tools``, ``cmd_yolo`` live in :mod:`chimera.cli.code`.
from chimera.cli.code import (  # noqa: E402
    cmd_agent as _cmd_agent,
    cmd_clear as _cmd_clear,
    cmd_exit as _cmd_exit,
    cmd_init as _cmd_init,
    cmd_model as _cmd_model,
    cmd_session as _cmd_session,
    cmd_tools as _cmd_tools,
    cmd_yolo as _cmd_yolo,
)


# ---------------------------------------------------------------------------
# Ferret-flavored placeholder stubs (owned by sibling F-agents)
# ---------------------------------------------------------------------------


def cmd_share(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Share the current ferret session.

    Late-binds :mod:`chimera.ferret.share_cmd` so this REPL command works
    once a sibling F-agent ships the share subcommand without touching
    the registry. Falls back to a stub message until the share module
    exists.
    """
    try:
        from chimera.ferret import share_cmd as _share  # type: ignore[attr-defined]
    except ImportError:
        out(
            "not yet wired: /share will be available once the share subcommand "
            "lands (owner: F-share)"
        )
        return

    runner = getattr(_share, "share_session", None) or getattr(_share, "run", None)
    if runner is None:
        out("not yet wired: /share handler missing (owner: F-share)")
        return
    try:
        runner(session=session, env=env, args=args, out=out)
    except Exception as exc:  # noqa: BLE001 -- surface, never crash REPL
        out(f"share failed: {exc}")


def cmd_sessions(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List or switch sessions via the ferret sessions command (FF1).

    Late-binds :mod:`chimera.ferret.sessions` so the listing surface
    stays in sync with whatever FF1 ships. Falls back to the shared
    ``cmd_session`` (save/list/fork) when the ferret-specific module
    doesn't expose a slash entry point yet.
    """
    try:
        from chimera.ferret import sessions as _sessions  # type: ignore[attr-defined]
    except ImportError:
        _cmd_session(session, env, args, out)
        return

    handler = getattr(_sessions, "slash_handler", None)
    if handler is None:
        _cmd_session(session, env, args, out)
        return
    try:
        handler(session, env, args, out)
    except Exception as exc:  # noqa: BLE001
        out(f"sessions failed: {exc}")


def cmd_new(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Start a new session: clear the current context.

    The upstream agent treats ``/new`` and ``/clear`` as aliases (both
    reset the live conversation). We honor that by delegating to the
    shared ``cmd_clear`` handler.
    """
    _cmd_clear(session, env, args, out)


# ---------------------------------------------------------------------------
# Ferret-specific commands: /sandbox, /approval, /diff
# ---------------------------------------------------------------------------
#
# These three commands have no equivalent in mink or otter — they map onto the
# sandbox-first / approval-preset posture that distinguishes ferret from the
# other Chimera CLI flavors. All three operate on per-session state stored on
# the session object itself (``session.sandbox_mode``, ``session.approval_preset``)
# so they round-trip cleanly with the FF2/FF3 modules and don't require any
# global registry. When those attributes are missing (tiny test fakes,
# pre-FF2/FF3 builds) the handlers degrade gracefully by reporting the
# defaults and updating attributes via ``setattr``.

# Cycling order for /sandbox. Matches the FF2 sandbox-mode triplet.
SANDBOX_MODES: tuple[str, ...] = (
    "read-only",
    "workspace-write",
    "workspace-write-network",
)

# Cycling order for /approval. Matches the FF3 approval-preset triplet.
APPROVAL_PRESETS: tuple[str, ...] = (
    "read-only",
    "auto",
    "full",
)


def _cycle(current: str, sequence: tuple[str, ...]) -> str:
    """Return the entry after *current* in *sequence*, wrapping around.

    A *current* value not present in *sequence* lands the user at the
    first entry — matching the "if you're off the map, go back to start"
    behaviour the upstream agent uses.
    """
    if current in sequence:
        idx = sequence.index(current)
        return sequence[(idx + 1) % len(sequence)]
    return sequence[0]


def _resolve_sandbox_env(session: Any, env: Any) -> Any:
    """Locate a :class:`SandboxedEnvironment` reachable from the session.

    Tries, in priority order: the explicit ``env`` argument, then
    ``session.env``, then ``session.agent.env``. Returns the first object
    that has a ``mode_holder`` attribute (the swappable
    :class:`~chimera.ferret.sandbox.MutableSandboxMode`) or a ``set_mode``
    method. Returns ``None`` when no live sandbox env is reachable —
    callers fall back to the session-attribute path so the slash command
    still updates the visible state.
    """
    candidates = [env]
    candidates.append(getattr(session, "env", None))
    agent = getattr(session, "agent", None)
    if agent is not None:
        candidates.append(getattr(agent, "env", None))
    for candidate in candidates:
        if candidate is None:
            continue
        if hasattr(candidate, "mode_holder") or hasattr(candidate, "set_mode"):
            return candidate
    return None


def _resolve_permission_proxy(session: Any) -> Any:
    """Locate the :class:`MutablePermissionPolicy` for *session*.

    Walks the standard surfaces an agent exposes its loop config under:

    * ``session.permissions`` — short-circuit if the REPL stashed the
      proxy directly on the session.
    * ``session.agent.loop.config.permissions`` — the canonical home; the
      ferret CLI installs the proxy onto the live :class:`LoopConfig` so
      the next tool call sees the swap.
    * ``session.config.permissions`` — fallback for sessions that hold a
      LoopConfig directly.

    Returns the proxy when found, or ``None`` when the session predates
    proxy installation. The caller falls back to the visible-state-only
    path in that case.
    """
    direct = getattr(session, "permissions", None)
    if direct is not None and hasattr(direct, "set_inner"):
        return direct
    agent = getattr(session, "agent", None)
    loop = getattr(agent, "loop", None) if agent is not None else None
    config = getattr(loop, "config", None) if loop is not None else None
    if config is not None:
        perms = getattr(config, "permissions", None)
        if perms is not None and hasattr(perms, "set_inner"):
            return perms
    config = getattr(session, "config", None)
    if config is not None:
        perms = getattr(config, "permissions", None)
        if perms is not None and hasattr(perms, "set_inner"):
            return perms
    return None


def cmd_sandbox(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Show or cycle the active sandbox mode and rewire the env.

    Usage:
        ``/sandbox`` — print the current mode and cycle to the next.
        ``/sandbox <mode>`` — set the mode explicitly. Valid values:
        ``read-only``, ``workspace-write``, ``workspace-write-network``.

    The handler operates on two surfaces:

    1. **Visible state** — :attr:`session.sandbox_mode` is updated so
       slash commands and the REPL display agree on the current mode.
    2. **Live env** — when a :class:`~chimera.ferret.sandbox.SandboxedEnvironment`
       is reachable from the session (``session.env`` /
       ``session.agent.env``), its :class:`MutableSandboxMode` holder is
       swapped via :meth:`SandboxedEnvironment.set_mode` so the next
       tool call sees the new policy. The wrap is atomic; concurrent
       agent turns never observe a torn read.
    """
    current = getattr(session, "sandbox_mode", SANDBOX_MODES[0]) or SANDBOX_MODES[0]
    requested = args.strip()

    if requested:
        if requested not in SANDBOX_MODES:
            valid = ", ".join(SANDBOX_MODES)
            out(f"/sandbox: unknown mode {requested!r} (valid: {valid})")
            return
        new_mode = requested
    else:
        new_mode = _cycle(current, SANDBOX_MODES)

    try:
        setattr(session, "sandbox_mode", new_mode)
    except (AttributeError, TypeError):
        # Read-only session object — surface the intent instead of silently
        # failing. The session can still observe the printed feedback.
        out(f"/sandbox: cannot persist mode on session ({type(session).__name__})")
        return

    sandbox_env = _resolve_sandbox_env(session, env)
    if sandbox_env is not None:
        try:
            setter = getattr(sandbox_env, "set_mode", None)
            if callable(setter):
                setter(new_mode)
            else:
                holder = getattr(sandbox_env, "mode_holder", None)
                if holder is not None and hasattr(holder, "set"):
                    holder.set(new_mode)
        except Exception as exc:  # noqa: BLE001 — surface, never crash REPL
            out(f"/sandbox: live env swap failed: {exc}")
            return

    out(f"/sandbox: {current} -> {new_mode}")


def cmd_approval(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Show or cycle the active approval preset and rewire LoopConfig.

    Usage:
        ``/approval`` — print the current preset and cycle to the next.
        ``/approval <preset>`` — set explicitly. Valid: ``read-only``,
        ``auto``, ``full``.

    The handler operates on two surfaces:

    1. **Visible state** — :attr:`session.approval_preset` is updated.
    2. **Live LoopConfig** — when the session's agent was built with a
       :class:`~chimera.ferret.approval.MutablePermissionPolicy` proxy
       at ``LoopConfig.permissions``, the proxy's inner policy is
       swapped via :meth:`MutablePermissionPolicy.set_inner`. The next
       tool call's permission evaluation hits the new policy without
       any agent rebuild.
    """
    current = (
        getattr(session, "approval_preset", APPROVAL_PRESETS[0])
        or APPROVAL_PRESETS[0]
    )
    requested = args.strip()

    if requested:
        if requested not in APPROVAL_PRESETS:
            valid = ", ".join(APPROVAL_PRESETS)
            out(f"/approval: unknown preset {requested!r} (valid: {valid})")
            return
        new_preset = requested
    else:
        new_preset = _cycle(current, APPROVAL_PRESETS)

    try:
        setattr(session, "approval_preset", new_preset)
    except (AttributeError, TypeError):
        out(
            f"/approval: cannot persist preset on session "
            f"({type(session).__name__})"
        )
        return

    proxy = _resolve_permission_proxy(session)
    if proxy is not None:
        try:
            from chimera.ferret.approval import (
                policy_for_preset,
                preset_from_string,
            )

            new_policy = policy_for_preset(preset_from_string(new_preset))
            proxy.set_inner(new_policy)
        except Exception as exc:  # noqa: BLE001 — surface, never crash REPL
            out(f"/approval: live policy swap failed: {exc}")
            return

    out(f"/approval: {current} -> {new_preset}")


def cmd_diff(session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Show pending file diffs the running agent has produced.

    Resolution order:

    1. Session-level :attr:`session.file_tracker` (the canonical
       :class:`chimera.core.file_tracker.FileTracker`) — when present we
       list ``modified`` and ``read`` files reported by the loop's
       file-tracking middleware.
    2. Environment-level diff hooks: any of
       ``env.pending_diff()`` / ``env.diff()`` / ``env.git_diff()``
       (best-effort, late-bound). The first one that returns text is
       printed.
    3. Otherwise, a friendly "no pending changes" notice.

    No diff is ever computed inline — this handler is a *display* surface
    over whichever tracker subsystem is wired up; computing a diff lives
    with FF2/FF4.
    """
    tracker = getattr(session, "file_tracker", None)
    if tracker is not None:
        modified = list(getattr(tracker, "modified_files", []) or [])
        read = list(getattr(tracker, "read_files", []) or [])
        if modified or read:
            out("Pending file activity:")
            if modified:
                out(f"  modified ({len(modified)}): " + ", ".join(modified))
            if read:
                out(f"  read     ({len(read)}): " + ", ".join(read))
            return

    if env is not None:
        for attr in ("pending_diff", "diff", "git_diff"):
            hook = getattr(env, attr, None)
            if not callable(hook):
                continue
            try:
                rendered = hook()
            except Exception as exc:  # noqa: BLE001 -- never crash REPL
                out(f"/diff: {attr}() failed: {exc}")
                return
            if isinstance(rendered, str) and rendered.strip():
                out(rendered.rstrip())
                return

    out("/diff: no pending changes")


# ---------------------------------------------------------------------------
# /undo and /redo: per-session checkpoint stacks
# ---------------------------------------------------------------------------


@dataclass
class _UndoState:
    """Per-session undo/redo state.

    Attributes:
        manager: Lazily constructed :class:`CheckpointManager` bound to the
            session's environment. ``None`` until the first snapshot lands or
            the session has no environment at all.
        undo_stack: Checkpoints captured at end-of-turn, oldest first.
        redo_stack: Checkpoints popped by ``/undo`` and awaiting ``/redo``.
        message_snapshots: Maps a checkpoint id to the deep-copied messages
            present on the session at the time the snap was taken.
        initial_messages: Deep copy of the session's messages *before* any
            snapshots were taken.
    """

    manager: CheckpointManager | None = None
    undo_stack: list[CheckpointInfo] = field(default_factory=list)
    redo_stack: list[CheckpointInfo] = field(default_factory=list)
    message_snapshots: dict[str, list[Any]] = field(default_factory=dict)
    initial_messages: list[Any] | None = None


_UNDO_STATES: dict[int, _UndoState] = {}


def get_undo_state(session: Any) -> _UndoState:
    """Return the :class:`_UndoState` for *session*, creating one if needed."""
    key = id(session)
    state = _UNDO_STATES.get(key)
    if state is None:
        state = _UndoState()
        _UNDO_STATES[key] = state
    return state


def clear_undo_state(session: Any) -> None:
    """Forget the undo/redo state for *session*."""
    _UNDO_STATES.pop(id(session), None)


def _snapshot_messages(session: Any) -> list[Any]:
    """Deep-copy the current session messages, or return ``[]`` if absent."""
    ctx = getattr(session, "context", None)
    msgs = getattr(ctx, "messages", None) if ctx is not None else None
    if msgs is None:
        msgs = getattr(session, "messages", None)
    if msgs is None:
        return []
    try:
        return copy.deepcopy(list(msgs))
    except Exception:  # noqa: BLE001 -- best-effort; never crash the REPL
        return list(msgs)


def _restore_messages(session: Any, messages: list[Any]) -> None:
    """Replace the session's conversation context with *messages*."""
    ctx = getattr(session, "context", None)
    if ctx is not None:
        ctx_msgs = getattr(ctx, "messages", None)
        if isinstance(ctx_msgs, list):
            ctx_msgs.clear()
            ctx_msgs.extend(copy.deepcopy(messages))
            return
    session_msgs = getattr(session, "messages", None)
    if isinstance(session_msgs, list):
        session_msgs.clear()
        session_msgs.extend(copy.deepcopy(messages))


def _ensure_manager(state: _UndoState, env: Any) -> CheckpointManager | None:
    """Lazily build a :class:`CheckpointManager` for *env*, if one exists."""
    if state.manager is not None:
        return state.manager
    if env is None or not hasattr(env, "checkpoint"):
        return None
    from chimera.checkpoints import CheckpointManager

    state.manager = CheckpointManager(env)
    return state.manager


def snapshot_after_turn(session: Any, env: Any) -> CheckpointInfo | None:
    """Snap session state after an assistant turn.

    A new turn invalidates any pending redo entries — that mirrors the
    upstream agent's behaviour and avoids the well-known "redo to a
    parallel universe" footgun.
    """
    state = get_undo_state(session)
    if state.initial_messages is None:
        state.initial_messages = _snapshot_messages(session)

    manager = _ensure_manager(state, env)
    msgs = _snapshot_messages(session)

    info: CheckpointInfo | None = None
    if manager is not None:
        try:
            info = manager.create(description="ferret turn snapshot")
        except Exception:  # noqa: BLE001
            info = None

    if info is not None:
        state.message_snapshots[info.id] = msgs
        state.undo_stack.append(info)
    else:
        from chimera.checkpoints import CheckpointInfo as _CI

        sentinel = _CI(
            id=f"ferret-msg-{len(state.message_snapshots) + 1}",
            name=f"ferret-msg-{len(state.message_snapshots) + 1}",
            timestamp=0.0,
            description="ferret turn snapshot (messages only)",
        )
        state.message_snapshots[sentinel.id] = msgs
        state.undo_stack.append(sentinel)

    state.redo_stack.clear()
    return info


def cmd_undo(session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Roll the session back one turn."""
    state = get_undo_state(session)
    if not state.undo_stack:
        out("/undo: nothing to undo")
        return

    popped = state.undo_stack.pop()
    state.redo_stack.append(popped)

    if state.undo_stack:
        target = state.undo_stack[-1]
        target_messages = state.message_snapshots.get(target.id, [])
    else:
        target = None
        target_messages = state.initial_messages or []

    if target is not None and state.manager is not None:
        try:
            state.manager.restore_by_id(target.id)
        except (KeyError, Exception):  # noqa: BLE001
            pass

    _restore_messages(session, target_messages)
    out(f"/undo: rewound 1 turn ({len(state.undo_stack)} remaining)")


def cmd_redo(session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Re-apply a turn previously rewound by :func:`cmd_undo`."""
    state = get_undo_state(session)
    if not state.redo_stack:
        out("/redo: nothing to redo")
        return

    target = state.redo_stack.pop()
    state.undo_stack.append(target)

    if state.manager is not None:
        try:
            state.manager.restore_by_id(target.id)
        except (KeyError, Exception):  # noqa: BLE001
            pass

    target_messages = state.message_snapshots.get(target.id, [])
    _restore_messages(session, target_messages)
    out(f"/redo: replayed 1 turn ({len(state.redo_stack)} remaining)")


def cmd_models(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List or cycle models (alias for the shared ``/model``)."""
    _cmd_model(session, env, args, out)


def cmd_agents(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List agent presets (alias for the shared ``/agent``)."""
    _cmd_agent(session, env, args, out)


def cmd_quit(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Leave the REPL (alias for ``/exit``)."""
    _cmd_exit(session, env, args, out)


# ---------------------------------------------------------------------------
# Command origin tracking + grouped /help
# ---------------------------------------------------------------------------

ORIGIN_BUILTIN = "builtin"
ORIGIN_CUSTOM = "custom"
ORIGIN_PLUGIN = "plugin"

_ORIGIN_SECTIONS: list[tuple[str, str]] = [
    (ORIGIN_BUILTIN, "Built-in commands"),
    (ORIGIN_CUSTOM, "Custom commands"),
    (ORIGIN_PLUGIN, "Plugin commands"),
]

_COMMAND_ORIGINS: dict[str, str] = {}
_COMMAND_HELP: dict[str, str] = {}


def mark_origin(name: str, origin: str, help_text: str | None = None) -> None:
    """Tag a slash-command name with its origin (and optional help text)."""
    _COMMAND_ORIGINS[name] = origin
    if help_text is not None:
        _COMMAND_HELP[name] = help_text


def get_command_origin(name: str) -> str | None:
    """Return the origin tag for ``name``, or ``None`` if unknown."""
    return _COMMAND_ORIGINS.get(name)


def _list_help_entries(origin: str) -> list[tuple[str, str]]:
    """Return ``(name, help_text)`` pairs registered under ``origin``, sorted."""
    try:
        from chimera.cli import slash_commands as _shared
        live: dict[str, str] = {
            name: ht for name, ht in _shared.list_commands()
        }
    except Exception:  # noqa: BLE001
        live = {}

    rows: list[tuple[str, str]] = []
    for name, tag in _COMMAND_ORIGINS.items():
        if tag != origin:
            continue
        help_text = (
            _COMMAND_HELP.get(name)
            or live.get(name)
            or FERRET_SLASH_HELP.get(name, "")
        )
        rows.append((name, help_text))
    rows.sort(key=lambda row: row[0])
    return rows


def cmd_help(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Render the ferret ``/help`` output, grouped by command origin."""
    if not _COMMAND_ORIGINS:
        _cmd_help(_session, _env, _args, out)
        return

    out("Available commands:")

    rendered_origins: set[str] = set()
    for tag, label in _ORIGIN_SECTIONS:
        rows = _list_help_entries(tag)
        if not rows:
            continue
        rendered_origins.add(tag)
        out("")
        out(f"{label}:")
        for name, help_text in rows:
            if help_text:
                out(f"  /{name:<14} {help_text}")
            else:
                out(f"  /{name}")

    leftover_tags = sorted(set(_COMMAND_ORIGINS.values()) - rendered_origins)
    leftover_rows: list[tuple[str, str]] = []
    for tag in leftover_tags:
        leftover_rows.extend(_list_help_entries(tag))
    if leftover_rows:
        out("")
        out("Other commands:")
        for name, help_text in sorted(leftover_rows, key=lambda row: row[0]):
            if help_text:
                out(f"  /{name:<14} {help_text}")
            else:
                out(f"  /{name}")


# ---------------------------------------------------------------------------
# The palette
# ---------------------------------------------------------------------------

FERRET_SLASH_COMMANDS: dict[str, SlashHandler] = {
    # Session
    "sessions": cmd_sessions,
    "new": cmd_new,
    "clear": _cmd_clear,
    "share": cmd_share,
    "undo": cmd_undo,
    "redo": cmd_redo,
    # Agent
    "agent": _cmd_agent,
    "agents": cmd_agents,
    "model": _cmd_model,
    "models": cmd_models,
    "tools": _cmd_tools,
    "yolo": _cmd_yolo,
    # Ferret-specific (sandbox-first / approval-preset / IDE-first)
    "sandbox": cmd_sandbox,
    "approval": cmd_approval,
    "diff": cmd_diff,
    # System
    "help": cmd_help,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "config": _cmd_config,
    "cost": _cmd_cost,
    "compact": _cmd_compact,
    "init": _cmd_init,
    "exit": _cmd_exit,
    "quit": cmd_quit,
}

# Alias used by the ferret REPL.
COMMANDS: dict[str, SlashHandler] = FERRET_SLASH_COMMANDS


FERRET_SLASH_HELP: dict[str, str] = {
    # Session
    "sessions": "list or switch sessions",
    "new": "start a new session (clears context)",
    "clear": "clear the current context",
    "share": "share the current session",
    "undo": "undo the last turn",
    "redo": "redo a previously undone turn",
    # Agent
    "agent": "list agent presets",
    "agents": "list agent presets",
    "model": "show or cycle the active model",
    "models": "show or cycle the active model",
    "tools": "list available tools",
    "yolo": "toggle auto-approve mode",
    # Ferret-specific
    "sandbox": "show or cycle the sandbox mode (read-only / workspace-write / workspace-write-network)",
    "approval": "show or cycle the approval preset (read-only / auto / full)",
    "diff": "show pending file diffs from the running agent",
    # System
    "help": "show this list",
    "status": "one-screen status summary",
    "doctor": "environment health checks",
    "config": "print effective merged settings",
    "cost": "show cumulative cost",
    "compact": "force a HARD threshold compaction now",
    "init": "summarise the project",
    "exit": "leave the REPL",
    "quit": "leave the REPL",
}


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

def _install_one(
    repl_state: Any, name: str, handler: SlashHandler, help_text: str,
) -> bool:
    """Install a single ``(name, handler, help_text)`` triple onto *repl_state*."""
    register = getattr(repl_state, "register", None)
    if callable(register):
        try:
            register(name, handler, help_text)
            return True
        except TypeError:
            try:
                register(name, handler)
                return True
            except Exception:  # noqa: BLE001
                return False

    for attr in ("commands", "slash_commands"):
        bag = getattr(repl_state, attr, None)
        if isinstance(bag, dict):
            bag[name] = handler
            return True

    try:
        setattr(repl_state, name, handler)
        return True
    except (AttributeError, TypeError):
        return False


def register_ferret_slash(
    repl_state: Any,
    *,
    custom_commands: list["CustomCommand"] | None = None,
) -> int:
    """Install every ferret slash command onto ``repl_state``.

    This composes with three flavors of REPL state, in priority order:

    1. The shared :mod:`chimera.cli.slash_commands` module itself, or
       any object exposing ``register(name, handler, help_text)``.
    2. A state object exposing a ``commands`` or ``slash_commands``
       mapping (for ad-hoc REPL fakes used in tests).
    3. Anything else: we ``setattr(repl_state, name, handler)`` so the
       commands at least become discoverable as attributes.

    Args:
        repl_state: Target onto which the ferret palette is installed.
        custom_commands: Optional list of user-defined
            :class:`~chimera.ferret.commands.CustomCommand` instances loaded
            from ``.codex/command/*.md``. Customs land **after** the
            built-in palette so a same-named user command wins.

    Returns:
        The count of commands successfully installed (built-ins + customs).
    """
    installed = 0
    for name, handler in FERRET_SLASH_COMMANDS.items():
        help_text = FERRET_SLASH_HELP.get(name, "")
        if _install_one(repl_state, name, handler, help_text):
            mark_origin(name, ORIGIN_BUILTIN, help_text)
            installed += 1

    if custom_commands:
        installed += register_custom_commands(repl_state, custom_commands)
    return installed


# ---------------------------------------------------------------------------
# Custom-command bridge (.codex/command/*.md -> slash handler)
# ---------------------------------------------------------------------------


def _split_custom_args(raw: str) -> tuple[list[str], dict[str, str]]:
    """Split a slash-command argument line into positional + named pieces."""
    cleaned = (raw or "").strip()
    if not cleaned:
        return [], {}
    try:
        tokens = shlex.split(cleaned, posix=True)
    except ValueError:
        tokens = cleaned.split()

    positional: list[str] = []
    named: dict[str, str] = {}
    for tok in tokens:
        if "=" in tok:
            key, _, value = tok.partition("=")
            key = key.strip()
            if key and not key.startswith("="):
                named[key] = value
                continue
        positional.append(tok)
    return positional, named


def build_custom_command_handler(cmd: "CustomCommand") -> SlashHandler:
    """Wrap a :class:`CustomCommand` as a slash-registry handler."""

    def _handler(session: Any, _env: Any, args: str, out: PrintFn) -> None:
        positional, named = _split_custom_args(args)
        try:
            rendered = cmd.render(*positional, **named)
        except Exception as exc:  # noqa: BLE001
            out(f"/{cmd.name} render failed: {exc}")
            return

        queue = getattr(session, "queue", None)
        if callable(queue):
            try:
                queue(rendered)
                out(f"/{cmd.name} queued ({len(rendered)} chars)")
                return
            except Exception as exc:  # noqa: BLE001
                out(f"/{cmd.name} queue failed: {exc}")

        steer = getattr(session, "steer", None)
        if callable(steer):
            try:
                steer(rendered)
                out(f"/{cmd.name} steered ({len(rendered)} chars)")
                return
            except Exception as exc:  # noqa: BLE001
                out(f"/{cmd.name} steer failed: {exc}")

        out(rendered)

    _handler.__name__ = f"cmd_custom_{cmd.name}"
    _handler.__doc__ = (
        f"User-defined command from {cmd.source or '<memory>'}: "
        f"{cmd.description or cmd.name}"
    )
    return _handler


def register_custom_commands(
    repl_state: Any, commands: list["CustomCommand"],
) -> int:
    """Install user-defined commands onto a slash registry."""
    if not commands:
        return 0
    installed = 0
    for cmd in commands:
        handler = build_custom_command_handler(cmd)
        help_text = cmd.description or f"user command: /{cmd.name}"
        if _install_one(repl_state, cmd.name, handler, help_text):
            mark_origin(cmd.name, ORIGIN_CUSTOM, help_text)
            installed += 1
    if installed:
        _refresh_completion(repl_state)
    return installed


def register_plugin_commands(
    repl_state: Any,
    commands: list[Any],
    *,
    handler_factory: Callable[[Any], SlashHandler] | None = None,
) -> int:
    """Install plugin-contributed slash commands onto a slash registry."""
    if not commands:
        return 0

    def _default_factory(cmd: Any) -> SlashHandler:
        handler = getattr(cmd, "handler", None)
        if not callable(handler):
            raise TypeError(
                f"plugin command {getattr(cmd, 'name', '?')!r} has no callable handler"
            )
        return handler  # type: ignore[no-any-return]

    factory = handler_factory or _default_factory

    installed = 0
    for cmd in commands:
        name = getattr(cmd, "name", "")
        if not name:
            continue
        try:
            handler = factory(cmd)
        except Exception:  # noqa: BLE001
            continue
        help_text = getattr(cmd, "description", "") or f"plugin command: /{name}"
        if _install_one(repl_state, name, handler, help_text):
            mark_origin(name, ORIGIN_PLUGIN, help_text)
            installed += 1
    if installed:
        _refresh_completion(repl_state)
    return installed


def _refresh_completion(repl_state: Any) -> None:
    """Resync tab-completion after custom commands land on *repl_state*."""
    refresh = getattr(repl_state, "refresh_command_names", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # noqa: BLE001
            pass

    try:
        import readline
    except ImportError:
        return
    try:
        completer = readline.get_completer()
    except Exception:  # noqa: BLE001
        return
    if completer is None:
        return
    try:
        readline.set_completer(completer)
    except Exception:  # noqa: BLE001
        return
