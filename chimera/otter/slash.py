"""Otter slash-command palette.

This module defines the otter REPL's slash-command set, mirroring the
upstream open-source coding agent's TUI command palette while reusing
Chimera's shared :mod:`chimera.cli.slash_commands` infrastructure for
the canonical handlers (``/help``, ``/model``, ``/cost``, ...).

The palette:

* Includes every command that the upstream agent exposes via its
  command dialog: ``/help``, ``/exit`` (+ ``/quit``), ``/share``,
  ``/agent`` (+ ``/agents``), ``/model`` (+ ``/models``), ``/init``,
  ``/sessions`` (+ ``/new``, ``/clear``), ``/cost``, ``/tools``,
  ``/undo``, ``/redo``, ``/edit``, ``/yolo``, ``/themes``,
  ``/status``, ``/mcps``, ``/connect``.
* Reuses the shared registry's handler whenever Chimera already has a
  near-equivalent (e.g. otter ``/agents`` -> shared ``cmd_agent``).
* Ships friendly placeholder stubs for commands whose backing
  subsystems are owned by sibling O-agents in the wave-1 build (share,
  sessions list, MCP toggles, theme switcher, edit/undo/redo). The
  stubs print ``not yet wired (owner: O<n>)`` rather than raising so a
  bare otter REPL is still useful end-to-end.

Two public surfaces are exposed:

* :data:`OTTER_SLASH_COMMANDS` — ``{name: handler}`` dict that the
  prompt-spec asks for; mirrors the wider Chimera slash-command
  contract (``handler(session, env, args, out)``).
* :data:`COMMANDS` — same dict, under the alias the otter REPL wires
  in :mod:`chimera.otter.repl` when merging overrides into the shared
  registry.

And one installer:

* :func:`register_otter_slash` — install every command on a REPL state
  object that exposes a ``register(name, handler, help_text)`` method
  (the shape of :mod:`chimera.cli.slash_commands`). When the state
  object lacks ``register``, we fall back to writing into a
  ``commands`` / ``slash_commands`` mapping, then to settattr — so the
  helper composes with both the shared registry and tiny test fakes.

Trademark hygiene: this module deliberately uses neutral phrasing
("the upstream coding agent") in any user-visible string, per
``research/otter/SPEC.md``.
"""
from __future__ import annotations

import copy
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.checkpoints import CheckpointInfo, CheckpointManager
    from chimera.otter.commands import CustomCommand
    from chimera.otter.snapshot import FileSnapshotStore

__all__ = [
    "COMMANDS",
    "OTTER_SLASH_COMMANDS",
    "OTTER_SLASH_HELP",
    "PrintFn",
    "SlashHandler",
    "build_custom_command_handler",
    "clear_undo_state",
    "cmd_help",
    "collect_modified_files",
    "get_command_origin",
    "get_file_snapshot_store",
    "get_undo_state",
    "mark_origin",
    "register_custom_commands",
    "register_otter_slash",
    "register_plugin_commands",
    "snapshot_after_turn",
]


PrintFn = Callable[[str], None]
SlashHandler = Callable[[Any, Any, str, PrintFn], None]


# ---------------------------------------------------------------------------
# Shared-registry passthroughs
# ---------------------------------------------------------------------------
#
# Most otter commands map directly onto handlers Chimera already ships in
# :mod:`chimera.cli.slash_commands`. We re-export those handlers (rather than
# re-implementing them) so behaviour stays in lockstep with ``chimera code``
# and ``chimera mink``. Imports are eager because the shared registry has no
# optional deps — and a circular import is not possible here (the shared
# module never reaches into ``chimera.otter``).

from chimera.cli.slash_commands import (  # noqa: E402 -- intentional after docstring
    cmd_compact as _cmd_compact,
    cmd_config as _cmd_config,
    cmd_cost as _cmd_cost,
    cmd_doctor as _cmd_doctor,
    cmd_help as _cmd_help,
    cmd_mcp as _cmd_mcp,
    cmd_status as _cmd_status,
)

# ``cmd_agent``, ``cmd_clear``, ``cmd_exit``, ``cmd_init``, ``cmd_model``,
# ``cmd_session``, ``cmd_tools``, ``cmd_yolo`` live in :mod:`chimera.cli.code`.
# They follow the same ``(session, env, args, out)`` signature.
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
# Otter-flavored placeholder stubs (owned by sibling O-agents)
# ---------------------------------------------------------------------------

def _stub(message: str) -> SlashHandler:
    """Build a stub handler that prints ``message`` and returns.

    Stubs are used for commands whose backing subsystems are owned by
    other O-agents in the wave-1 build. Once the sibling module lands
    (e.g. O13 ships share, O3 ships ``sessions list``), the stub is
    swapped out for a real handler in a follow-up patch.
    """

    def _handler(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
        out(message)

    return _handler


def cmd_share(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Share the current session via the otter share command (O13).

    Late-binds :mod:`chimera.otter.share_cmd` so this REPL command works
    once O13 lands without touching the registry. Falls back to a stub
    message until the share module exists.
    """
    try:
        from chimera.otter import share_cmd as _share  # type: ignore[attr-defined]
    except ImportError:
        out("not yet wired: /share will be available once the share subcommand lands (owner: O13)")
        return

    runner = getattr(_share, "share_session", None) or getattr(_share, "run", None)
    if runner is None:
        out("not yet wired: /share handler missing (owner: O13)")
        return
    try:
        runner(session=session, env=env, args=args, out=out)
    except Exception as exc:  # noqa: BLE001 -- surface, never crash REPL
        out(f"share failed: {exc}")


def cmd_sessions(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List or switch sessions via the otter sessions command (O3).

    Late-binds :mod:`chimera.otter.sessions` so the listing surface
    stays in sync with whatever O3 ships. Falls back to the shared
    ``cmd_session`` (save/list/fork) when the otter-specific module
    doesn't expose a slash entry point yet.
    """
    try:
        from chimera.otter import sessions as _sessions  # type: ignore[attr-defined]
    except ImportError:
        _cmd_session(session, env, args, out)
        return

    handler = getattr(_sessions, "slash_handler", None)
    if handler is None:
        # O3 hasn't published a slash handler yet; fall back to the
        # shared session command so the user still has save/list/fork.
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
# /undo and /redo: per-session checkpoint stacks
# ---------------------------------------------------------------------------
#
# Each REPL session owns an :class:`_UndoState` keyed by ``id(session)``. After
# every assistant turn the REPL calls :func:`snapshot_after_turn`, which:
#
# * Lazily builds a :class:`chimera.checkpoints.CheckpointManager` over the
#   session's environment.
# * Calls ``manager.create()`` to snap the workspace and append an entry to
#   the undo stack.
# * Captures a deep copy of the session's :class:`Context` messages so the
#   conversation can be rolled back alongside the filesystem.
# * Drops any pending redo entries (a fresh turn invalidates the redo path,
#   matching the upstream agent and most REPL-undo semantics).
#
# ``/undo`` pops the top of the undo stack, pushes it to the redo stack, then
# restores the resulting top-of-undo (or the initial state if the stack is
# empty). ``/redo`` is the inverse: pop redo, restore, push back onto undo.
#
# When the session has no environment (eg. tiny test fakes) we still drive
# the conversation-context snapshot so undo/redo work for plain message
# rewinds, even without filesystem checkpointing.


@dataclass
class _UndoState:
    """Per-session undo/redo state.

    Attributes:
        manager: Lazily constructed :class:`CheckpointManager` bound to the
            session's environment. ``None`` until the first snapshot lands or
            the session has no environment at all.
        undo_stack: Checkpoints captured at end-of-turn, oldest first. The
            top of the stack represents the current state.
        redo_stack: Checkpoints popped by ``/undo`` and awaiting ``/redo``.
        message_snapshots: Maps a checkpoint id to the deep-copied messages
            present on the session at the time the snap was taken. Used so
            ``/undo`` can restore the conversation context, not just the
            filesystem.
        initial_messages: Deep copy of the session's messages *before* any
            snapshots were taken — restored when ``/undo`` empties the
            undo stack.
        file_store: Lazily constructed :class:`FileSnapshotStore` for the
            session. Holds the on-disk shadow at
            ``~/.chimera/snapshots/<session-id>/`` so ``/undo`` can rewind
            files, not just messages.
        file_snaps: Maps a checkpoint id to the matching file snapshot id.
            ``None`` for checkpoints taken before the file store was
            attached (those still rewind messages cleanly).
    """

    manager: CheckpointManager | None = None
    undo_stack: list[CheckpointInfo] = field(default_factory=list)
    redo_stack: list[CheckpointInfo] = field(default_factory=list)
    message_snapshots: dict[str, list[Any]] = field(default_factory=dict)
    initial_messages: list[Any] | None = None
    file_store: FileSnapshotStore | None = None
    file_snaps: dict[str, str] = field(default_factory=dict)


# Module-level registry. Keyed by ``id(session)`` so different sessions can
# coexist without leaking state. Cleared via :func:`clear_undo_state` when a
# session is discarded (e.g. ``/new``).
_UNDO_STATES: dict[int, _UndoState] = {}


def get_undo_state(session: Any) -> _UndoState:
    """Return the :class:`_UndoState` for *session*, creating one if needed.

    Exposed for tests and the REPL — the slash handlers use it internally to
    look up or initialise the per-session stack.
    """
    key = id(session)
    state = _UNDO_STATES.get(key)
    if state is None:
        state = _UndoState()
        _UNDO_STATES[key] = state
    return state


def clear_undo_state(session: Any) -> None:
    """Forget the undo/redo state for *session*.

    Called by ``/new`` (and by the REPL on session teardown) so a fresh
    session doesn't inherit a stale stack from its predecessor. Also
    wipes the on-disk file shadow so blob storage doesn't leak across
    sessions.
    """
    state = _UNDO_STATES.pop(id(session), None)
    if state is not None and state.file_store is not None:
        try:
            state.file_store.clear()
        except Exception:  # noqa: BLE001 -- never crash on teardown
            pass


# ---------------------------------------------------------------------------
# File-snapshot helpers
# ---------------------------------------------------------------------------
#
# G5 elevates ``/undo`` from "rewind messages" to "rewind messages AND any
# files the agent touched in that turn". The on-disk shadow lives in
# :class:`chimera.otter.snapshot.FileSnapshotStore`; this helper layer is
# what wires it onto the per-session :class:`_UndoState`.
#
# Two surfaces:
#
# * :func:`get_file_snapshot_store` — lazily attaches a store to the
#   session's undo state. Honors ``session._otter_snapshot_root`` so
#   tests can redirect the shadow under ``tmp_path``.
# * :func:`collect_modified_files` — walks every place we might find a
#   :class:`~chimera.core.file_tracker.FileTracker` (the loop config,
#   the env, an explicit per-session attribute) and returns the union
#   of paths the agent touched. Returns an empty list when no tracker
#   is attached so message-only undo still works for tiny REPL fakes.


def _resolve_session_id(session: Any) -> str:
    """Return a filesystem-safe id for *session*'s shadow store.

    Walks several common surfaces in priority order so the helper
    works against:

    * :class:`chimera.sessions.session.Session` (``session_id`` property)
    * :class:`chimera.otter.server.OtterSessionState` (``session_id`` attr)
    * Bare test fakes (falls back to ``id(session)`` hex)
    """
    sid = getattr(session, "session_id", None)
    if isinstance(sid, str) and sid:
        return sid
    sid_method = getattr(session, "id", None)
    if isinstance(sid_method, str) and sid_method:
        return sid_method
    return f"otter-{id(session):x}"


def get_file_snapshot_store(session: Any) -> FileSnapshotStore | None:
    """Return the on-disk file shadow store for *session*, lazily attached.

    Args:
        session: Session whose undo state owns the store.

    Returns:
        A :class:`FileSnapshotStore` rooted at
        ``~/.chimera/snapshots/<session-id>/`` (or an override path if
        ``session._otter_snapshot_root`` is set, which tests use to keep
        the shadow under ``tmp_path``). ``None`` if the import of
        :mod:`chimera.otter.snapshot` fails — keeps the slash module
        loadable in stripped-down environments.
    """
    state = get_undo_state(session)
    if state.file_store is not None:
        return state.file_store
    try:
        from chimera.otter.snapshot import FileSnapshotStore as _Store
    except Exception:  # noqa: BLE001 -- snapshot module optional
        return None
    root_override = getattr(session, "_otter_snapshot_root", None)
    try:
        state.file_store = _Store(
            session_id=_resolve_session_id(session),
            root=root_override,
        )
    except Exception:  # noqa: BLE001 -- never crash REPL on filesystem issue
        return None
    return state.file_store


def collect_modified_files(session: Any, env: Any) -> list[str]:
    """Walk known surfaces for the session's modified-file list.

    Search order (first non-empty wins):

    1. ``session._otter_file_tracker`` — explicit per-session override
       used by tests and by the otter server.
    2. ``session._agent.loop.config.file_tracker`` — the canonical
       location for sessions built via :class:`Session`.
    3. ``env.file_tracker`` — sometimes hung off the environment.
    4. Empty list — no tracker found.

    Returns:
        The list of file paths reported as modified, deduplicated and
        in deterministic order. Empty when no source is available.
    """
    candidates: list[Any] = []
    explicit = getattr(session, "_otter_file_tracker", None)
    if explicit is not None:
        candidates.append(explicit)

    agent = getattr(session, "_agent", None)
    if agent is not None:
        loop = getattr(agent, "loop", None)
        if loop is not None:
            cfg = getattr(loop, "config", None)
            if cfg is not None:
                tracker = getattr(cfg, "file_tracker", None)
                if tracker is not None:
                    candidates.append(tracker)

    if env is not None:
        env_tracker = getattr(env, "file_tracker", None)
        if env_tracker is not None:
            candidates.append(env_tracker)

    seen: set[str] = set()
    out: list[str] = []
    for tracker in candidates:
        files = getattr(tracker, "modified_files", None)
        if not files:
            continue
        for f in files:
            if not isinstance(f, str) or f in seen:
                continue
            seen.add(f)
            out.append(f)
        if out:
            return out
    return out


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
    """Replace the session's conversation context with *messages*.

    Walks the same surfaces as :func:`_snapshot_messages` so duck-typed
    fakes round-trip cleanly. If neither ``context.messages`` nor
    ``session.messages`` is writable we silently skip — the env-level
    restore still ran.
    """
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
    """Lazily build a :class:`CheckpointManager` for *env*, if one exists.

    Returns ``None`` when *env* is missing or doesn't expose ``checkpoint()``,
    so the caller can fall back to message-only snapshots without crashing.
    """
    if state.manager is not None:
        return state.manager
    if env is None or not hasattr(env, "checkpoint"):
        return None
    from chimera.checkpoints import CheckpointManager

    state.manager = CheckpointManager(env)
    return state.manager


def snapshot_after_turn(session: Any, env: Any) -> CheckpointInfo | None:
    """Snap session state after an assistant turn.

    The REPL calls this once per completed turn (see ``chimera.otter.repl``).
    A new turn invalidates any pending redo entries — that mirrors the
    upstream agent's behaviour and avoids the well-known "redo to a parallel
    universe" footgun.

    Args:
        session: The active session whose state is being snapped.
        env: The environment paired with *session* (may be ``None`` for tiny
            REPL fakes that don't carry a filesystem).

    Returns:
        The :class:`CheckpointInfo` just created, or ``None`` if the session
        has no environment to checkpoint (a message-only snapshot still
        landed in that case).
    """
    state = get_undo_state(session)
    if state.initial_messages is None:
        # Capture the pre-turn-1 baseline exactly once, so /undo from the
        # bottom of the stack can return us to a clean session.
        state.initial_messages = _snapshot_messages(session)

    manager = _ensure_manager(state, env)
    msgs = _snapshot_messages(session)

    info: CheckpointInfo | None = None
    if manager is not None:
        try:
            info = manager.create(description="otter turn snapshot")
        except Exception:  # noqa: BLE001 -- env may refuse mid-test
            info = None

    if info is not None:
        state.message_snapshots[info.id] = msgs
        state.undo_stack.append(info)
        active_id = info.id
    else:
        # Synthesise a sentinel so message-only undo still has a stack to
        # walk. The id is unique-per-snapshot; the manager stays ``None``.
        from chimera.checkpoints import CheckpointInfo as _CI

        sentinel = _CI(
            id=f"otter-msg-{len(state.message_snapshots) + 1}",
            name=f"otter-msg-{len(state.message_snapshots) + 1}",
            timestamp=0.0,
            description="otter turn snapshot (messages only)",
        )
        state.message_snapshots[sentinel.id] = msgs
        state.undo_stack.append(sentinel)
        active_id = sentinel.id

    # G5: also snap the files the agent has touched so /undo can rewind
    # the workspace, not just messages. Best-effort — never crash the
    # REPL on a filesystem hiccup. Collected files are cumulative across
    # the session (the file store is content-addressed, so unchanged
    # files cost no extra disk).
    modified = collect_modified_files(session, env)
    if modified:
        store = get_file_snapshot_store(session)
        if store is not None:
            try:
                file_snap = store.snap(modified)
            except Exception:  # noqa: BLE001
                file_snap = None
            if file_snap is not None:
                state.file_snaps[active_id] = file_snap.snap_id

    # Any new turn invalidates the redo path. Discard the file snaps
    # bound to the now-orphaned redo entries so storage doesn't grow
    # without bound when the user branches the conversation often.
    if state.redo_stack and state.file_store is not None:
        for entry in state.redo_stack:
            stale = state.file_snaps.pop(entry.id, None)
            if stale is not None:
                try:
                    state.file_store.discard(stale)
                except Exception:  # noqa: BLE001
                    pass
    state.redo_stack.clear()
    return info


def _parse_steps(raw: str, *, default: int = 1) -> int:
    """Parse the ``--steps N`` (or bare ``N``) argument for /undo and /redo.

    Accepts:
      * ``""``                 -> ``default``
      * ``"3"``                -> ``3``
      * ``"--steps 3"``        -> ``3``
      * ``"--steps=3"``        -> ``3``
      * anything else / N <= 0 -> ``default`` (clamped at 1)

    The handlers print a diagnostic but never raise on bad input — a
    fat-fingered ``/undo --step 3`` should still produce a 1-step undo
    rather than crashing the REPL.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return max(default, 1)
    tokens = cleaned.split()
    candidate: str | None = None
    if len(tokens) == 1:
        token = tokens[0]
        if token.startswith("--steps="):
            candidate = token.split("=", 1)[1]
        elif token.lstrip("-").isdigit():
            candidate = token.lstrip("-")
    elif tokens[0] in {"--steps", "-n"} and len(tokens) >= 2:
        candidate = tokens[1]
    if candidate is None:
        return max(default, 1)
    try:
        n = int(candidate)
    except ValueError:
        return max(default, 1)
    return max(n, 1)


def _restore_files_for(state: _UndoState, target_id: str | None) -> int:
    """Restore the file snap bound to *target_id* (best-effort).

    Returns the number of paths actually mutated. ``0`` when there's no
    file store, no snap recorded for the target, or the target is the
    pre-turn-1 baseline (which has no associated file snap — see
    :func:`snapshot_after_turn`).
    """
    if state.file_store is None or target_id is None:
        return 0
    snap_id = state.file_snaps.get(target_id)
    if snap_id is None:
        return 0
    try:
        restored = state.file_store.restore(snap_id)
    except Exception:  # noqa: BLE001
        return 0
    return len(restored)


def cmd_undo(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Roll the session back one or more turns.

    Pops up to ``--steps N`` entries off the undo stack (each pushed
    onto the redo stack), restores the env + message snapshot for the
    resulting top-of-stack (or the pre-turn-1 baseline if the stack
    drains), and rewinds any per-turn file shadow so files modified in
    the rewound turns return to their snapshotted contents.

    Args:
        args: ``""`` for a single-step rewind, ``"--steps 3"`` /
            ``"-n 3"`` / bare ``"3"`` for a multi-step rewind. Invalid
            arguments degrade to a 1-step undo rather than raising.
    """
    state = get_undo_state(session)
    if not state.undo_stack:
        out("/undo: nothing to undo")
        return

    steps = _parse_steps(args)
    rewound = 0
    last_popped: CheckpointInfo | None = None
    while rewound < steps and state.undo_stack:
        last_popped = state.undo_stack.pop()
        state.redo_stack.append(last_popped)
        rewound += 1

    # Determine the target checkpoint to restore. Three cases:
    #   1. Stack still has entries → restore the new top.
    #   2. Stack drained but at least one entry was popped → restore the
    #      *earliest* popped entry (i.e. the bottom-of-stack baseline).
    #      Without this, a multi-step ``--steps 99`` overshoot would
    #      leave env state stranded at the post-turn-N value while the
    #      conversation correctly resets to the pre-turn-1 baseline.
    #   3. Nothing popped (defensive) → fall back to initial_messages.
    if state.undo_stack:
        target = state.undo_stack[-1]
        target_messages = state.message_snapshots.get(target.id, [])
    elif last_popped is not None:
        target = last_popped
        target_messages = state.message_snapshots.get(
            last_popped.id, state.initial_messages or [],
        )
    else:
        target = None
        target_messages = state.initial_messages or []

    if target is not None and state.manager is not None:
        try:
            state.manager.restore_by_id(target.id)
        except (KeyError, Exception):  # noqa: BLE001 -- never crash REPL
            pass

    files_restored = _restore_files_for(state, target.id if target else None)

    _restore_messages(session, target_messages)
    suffix = f" ({len(state.undo_stack)} remaining)"
    word = "turn" if rewound == 1 else "turns"
    file_note = f", {files_restored} files restored" if files_restored else ""
    out(f"/undo: rewound {rewound} {word}{file_note}{suffix}")


def cmd_redo(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Re-apply one or more turns previously rewound by :func:`cmd_undo`.

    Symmetric to :func:`cmd_undo` — accepts ``--steps N`` so a single
    ``/redo --steps 3`` reapplies three turns at once. Each redone turn
    is pushed back onto the undo stack so it can be undone again.
    """
    state = get_undo_state(session)
    if not state.redo_stack:
        out("/redo: nothing to redo")
        return

    steps = _parse_steps(args)
    replayed = 0
    target: CheckpointInfo | None = None
    while replayed < steps and state.redo_stack:
        target = state.redo_stack.pop()
        state.undo_stack.append(target)
        replayed += 1

    if target is not None and state.manager is not None:
        try:
            state.manager.restore_by_id(target.id)
        except (KeyError, Exception):  # noqa: BLE001
            pass

    files_restored = _restore_files_for(state, target.id if target else None)

    if target is not None:
        target_messages = state.message_snapshots.get(target.id, [])
        _restore_messages(session, target_messages)

    suffix = f" ({len(state.redo_stack)} remaining)"
    word = "turn" if replayed == 1 else "turns"
    file_note = f", {files_restored} files restored" if files_restored else ""
    out(f"/redo: replayed {replayed} {word}{file_note}{suffix}")


def cmd_edit(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Open an external ``$EDITOR`` for the next prompt (placeholder)."""
    out("not yet wired: /edit will open $EDITOR for the next prompt (owner: O2)")


def cmd_themes(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Switch the REPL theme (placeholder; owner: O2 / docs).

    The shared REPL is currently theme-less; this stub keeps the
    command discoverable so users coming from the upstream agent see a
    consistent palette.
    """
    out("not yet wired: /themes will be available once the REPL grows a theme switcher (owner: O2)")


def cmd_connect(_session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Connect a provider via the providers helper (O12).

    The upstream agent uses ``/connect`` to launch its provider-list
    dialog. We late-bind :mod:`chimera.otter.providers` so this hook
    lights up automatically once O12 lands, and prints a hint
    otherwise.
    """
    try:
        from chimera.otter import providers as _providers  # type: ignore[attr-defined]
    except ImportError:
        out("not yet wired: /connect will be available once provider wiring lands (owner: O12)")
        return

    handler = (
        getattr(_providers, "slash_connect", None)
        or getattr(_providers, "connect", None)
    )
    if handler is None:
        out("not yet wired: /connect handler missing (owner: O12)")
        return
    try:
        handler(args=args, out=out)
    except Exception as exc:  # noqa: BLE001
        out(f"connect failed: {exc}")


def cmd_mcps(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List configured MCP servers (alias for the shared ``/mcp``).

    The upstream agent surfaces this under ``/mcps`` (plural). We mirror
    that by aliasing onto the shared ``cmd_mcp`` so existing config
    discovery (project ``.mcp.json`` -> user ``~/.chimera/mcp.json``)
    still works through either spelling.
    """
    _cmd_mcp(session, env, args, out)


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
# W15-2 P2 (OPENCODE G24): /plan + /exec mode toggles
# ---------------------------------------------------------------------------
#
# Plan-mode is a soft toggle: ``/plan`` flips ``session.plan_mode`` to
# ``True`` and prints a one-line confirmation; ``/exec`` flips it back.
# The REPL surfaces this state in the prompt, and a one-shot ``/plan
# <task>`` form is also accepted — it sets the flag *and* echoes the task
# back so the operator's next message is read in plan-mode context.


def cmd_plan(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Enter plan-mode. Optional argument is echoed as the planning seed.

    Plan-mode discourages destructive tool use. The actual restriction
    lives in the loop's permission policy (the REPL reads
    ``session.plan_mode`` to compose a stricter policy when the flag is
    on); this slash command only flips the flag and echoes a confirmation.
    Already-on plan-mode is idempotent: bare ``/plan`` simply prints the
    current state.
    """
    seed = (args or "").strip()
    already = bool(getattr(session, "plan_mode", False))
    try:
        setattr(session, "plan_mode", True)
    except (AttributeError, TypeError):
        out("/plan: session does not accept a plan_mode flag")
        return
    if seed:
        out(f"/plan: entered plan-mode (seed: {seed!r})")
    elif already:
        out("/plan: already in plan-mode")
    else:
        out("/plan: entered plan-mode (read-only tool surface)")


def cmd_exec(session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Leave plan-mode and resume the standard execution surface."""
    was_on = bool(getattr(session, "plan_mode", False))
    try:
        setattr(session, "plan_mode", False)
    except (AttributeError, TypeError):
        out("/exec: session does not accept a plan_mode flag")
        return
    if was_on:
        out("/exec: left plan-mode")
    else:
        out("/exec: not in plan-mode")


# ---------------------------------------------------------------------------
# /permissions: declarative permission-rule management (W13 G6)
# ---------------------------------------------------------------------------
#
# Late-bound import so the slash module stays loadable when the
# permission-rules extra is missing (the loader itself uses only stdlib,
# but defensive). The slash command exposes the three operations the
# upstream agent's settings dialog ships:
#
#     /permissions list
#     /permissions add <tool> <action> [arg_key=PAT] [-- description...]
#     /permissions remove <index>
#
# Bare ``/permissions`` is a friendly alias for ``/permissions list``.


_PERMISSIONS_HELP = (
    "usage: /permissions [list | add <tool> <action> [arg_key=PAT] [desc...] | remove <index>]"
)


def cmd_permissions(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Manage declarative permission rules persisted in ``~/.chimera/permissions.json``.

    Sub-actions:

    * ``list`` (default) — print the current rules with their index.
    * ``add <tool> <action> [arg_key=PAT] [-- description]`` — append a
      rule. ``<action>`` is one of ``allow``, ``deny``, ``ask`` (or
      friendly aliases — ``permit`` / ``block`` / ``prompt``). The
      optional ``arg_key=PAT`` shorthand attaches a per-argument glob
      so e.g. ``arg_key=command "rm -rf*"`` only fires on dangerous
      bash invocations. Anything after a literal ``--`` token becomes
      the rule's description.
    * ``remove <index>`` — drop the 0-based rule at *index*.

    The command never crashes the REPL — bad input prints a one-line
    diagnostic and returns.
    """
    try:
        from chimera.otter import permission_rules as _pr
    except Exception as exc:  # noqa: BLE001 -- module optional in stripped CI
        out(f"/permissions: subsystem unavailable: {exc}")
        return

    tokens = shlex.split((args or "").strip()) if args else []
    sub = tokens[0].lower() if tokens else "list"

    if sub == "list":
        rules = _pr.list_rules()
        if not rules:
            path = _pr.default_permissions_path()
            out(f"/permissions: no rules in {path}")
            return
        out("/permissions:")
        for idx, rule in enumerate(rules):
            arg_note = ""
            if rule.arg_key and rule.arg_pattern:
                arg_note = f"  ({rule.arg_key} ~= {rule.arg_pattern!r})"
            desc = f"  -- {rule.description}" if rule.description else ""
            out(f"  [{idx}] {rule.tool} -> {rule.action}{arg_note}{desc}")
        return

    if sub == "add":
        rest = tokens[1:]
        if len(rest) < 2:
            out(_PERMISSIONS_HELP)
            return
        tool, action = rest[0], rest[1]
        extras = rest[2:]
        arg_key: str | None = None
        arg_pattern: str | None = None
        description_parts: list[str] = []
        # Walk extras: ``key=value`` becomes arg_key + arg_pattern; ``--``
        # toggles description-collection mode for any remaining tokens.
        in_description = False
        for tok in extras:
            if in_description:
                description_parts.append(tok)
                continue
            if tok == "--":
                in_description = True
                continue
            if "=" in tok and not tok.startswith("="):
                key, _, val = tok.partition("=")
                if not arg_key:
                    arg_key = key.strip()
                    arg_pattern = val
                continue
            # Bare token: treat as the arg_pattern when arg_key was set
            # without a value, otherwise fold into description.
            if arg_key and not arg_pattern:
                arg_pattern = tok
            else:
                description_parts.append(tok)
        try:
            new_rule = _pr.OtterPermissionRule(
                tool=tool,
                action=action.lower(),
                arg_key=arg_key or None,
                arg_pattern=arg_pattern or None,
                description=" ".join(description_parts).strip(),
            )
            # Validate eagerly so a bad action doesn't land on disk.
            _pr.parse_action(new_rule.action)
            _pr.add_rule(new_rule)
        except _pr.PermissionRulesError as exc:
            out(f"/permissions add: {exc}")
            return
        except OSError as exc:
            out(f"/permissions add: cannot write file: {exc}")
            return
        path = _pr.default_permissions_path()
        out(f"/permissions: added rule {new_rule.tool} -> {new_rule.action} (saved to {path})")
        return

    if sub == "remove":
        if len(tokens) < 2:
            out(_PERMISSIONS_HELP)
            return
        try:
            index = int(tokens[1])
        except ValueError:
            out(f"/permissions remove: index must be an integer, got {tokens[1]!r}")
            return
        try:
            removed = _pr.remove_rule(index)
        except OSError as exc:
            out(f"/permissions remove: cannot write file: {exc}")
            return
        if removed is None:
            out(f"/permissions remove: no rule at index {index}")
            return
        out(f"/permissions: removed rule [{index}] {removed.tool} -> {removed.action}")
        return

    out(_PERMISSIONS_HELP)


# ---------------------------------------------------------------------------
# Command origin tracking + grouped /help
# ---------------------------------------------------------------------------
#
# Wave-3 (F8) split the otter ``/help`` output into "Built-in", "Custom", and
# "Plugin" sections so users can tell at a glance which commands ship with
# the binary versus which are sourced from ``.opencode/command/*.md`` or
# ``.opencode/plugin/<name>/command/*.md``. To do that we keep a per-command
# origin map alongside the registry and replace the shared ``cmd_help`` for
# the otter palette only.

# Origin tags used by :func:`mark_origin` / :func:`cmd_help`. Kept as plain
# strings (not an enum) so plugins / tests can introduce new tags without
# touching this module — unrecognised tags simply land in their own section.
ORIGIN_BUILTIN = "builtin"
ORIGIN_CUSTOM = "custom"
ORIGIN_PLUGIN = "plugin"

# Section labels (in render order). When a tag is present in the origin map
# but missing from this list (e.g. an exotic plugin tag), it is rendered last
# under a fallback "Other commands" heading so it remains discoverable.
_ORIGIN_SECTIONS: list[tuple[str, str]] = [
    (ORIGIN_BUILTIN, "Built-in commands"),
    (ORIGIN_CUSTOM, "Custom commands"),
    (ORIGIN_PLUGIN, "Plugin commands"),
]

# Per-command origin tag. Keyed by slash-command name (no leading slash).
# Mutated by :func:`mark_origin`, :func:`register_otter_slash`,
# :func:`register_custom_commands`, and :func:`register_plugin_commands`.
_COMMAND_ORIGINS: dict[str, str] = {}

# Help-text cache populated alongside the origin map. We can't always read
# back the help text from whichever REPL state the caller installed onto
# (a dict-style fake exposes ``commands`` but no descriptions), so we cache
# it here at registration time. ``cmd_help`` consults this map before
# falling through to :data:`OTTER_SLASH_HELP` and the shared registry.
_COMMAND_HELP: dict[str, str] = {}


def mark_origin(name: str, origin: str, help_text: str | None = None) -> None:
    """Tag a slash-command name with its origin (and optional help text).

    Args:
        name: Command name without leading slash (matches the registry key).
        origin: Origin tag — typically one of :data:`ORIGIN_BUILTIN`,
            :data:`ORIGIN_CUSTOM`, or :data:`ORIGIN_PLUGIN`. Other values
            are accepted and rendered under a generic "Other commands"
            section by :func:`cmd_help`.
        help_text: Optional one-line description. When provided, cached
            alongside the origin so :func:`cmd_help` can render it even
            against REPL states that don't expose the help text back to
            us (e.g. a plain ``commands`` dict fake).

    Last-write-wins so re-registering a name (e.g. a custom command
    overriding a built-in) updates both the section it appears under
    and any cached help text.
    """
    _COMMAND_ORIGINS[name] = origin
    if help_text is not None:
        _COMMAND_HELP[name] = help_text


def get_command_origin(name: str) -> str | None:
    """Return the origin tag for ``name``, or ``None`` if unknown."""
    return _COMMAND_ORIGINS.get(name)


def _list_help_entries(origin: str) -> list[tuple[str, str]]:
    """Return ``(name, help_text)`` pairs registered under ``origin``, sorted.

    Help-text resolution walks four sources in priority order:

    1. The :data:`_COMMAND_HELP` cache populated at registration time
       (covers customs + plugins where the source description is the
       only authoritative copy).
    2. The shared :mod:`chimera.cli.slash_commands` registry (catches
       built-ins installed against the live registry).
    3. :data:`OTTER_SLASH_HELP` (the canonical built-in fallback).
    4. Empty string (last resort).
    """
    try:
        from chimera.cli import slash_commands as _shared
        live: dict[str, str] = {
            name: ht for name, ht in _shared.list_commands()
        }
    except Exception:  # noqa: BLE001 -- shared registry optional in tests
        live = {}

    rows: list[tuple[str, str]] = []
    for name, tag in _COMMAND_ORIGINS.items():
        if tag != origin:
            continue
        help_text = (
            _COMMAND_HELP.get(name)
            or live.get(name)
            or OTTER_SLASH_HELP.get(name, "")
        )
        rows.append((name, help_text))
    rows.sort(key=lambda row: row[0])
    return rows


def cmd_help(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Render the otter ``/help`` output, grouped by command origin.

    Built-in commands appear first, then user-defined customs from
    ``.opencode/command/*.md`` (W2-eligible), then plugin-contributed
    commands from ``.opencode/plugin/<name>/command/*.md``. Within each
    section commands are sorted alphabetically. Sections with no
    commands are skipped so the output stays compact for users who
    haven't authored any extensions yet.

    Falls back gracefully when the origin map is empty (e.g. a bare
    test fixture that constructs the palette without calling
    :func:`register_otter_slash`): in that case we emit the legacy flat
    listing via :func:`chimera.cli.slash_commands.cmd_help` so the
    behaviour matches ``chimera code``.
    """
    if not _COMMAND_ORIGINS:
        # No origins were ever registered — fall through to the shared
        # flat listing so the user still sees something sensible.
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

    # Catch-all for non-standard tags so they remain visible.
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
#
# Maps ``name -> handler``; help text lives in :data:`OTTER_SLASH_HELP` so
# :func:`register_otter_slash` can register both pieces against the shared
# registry. Order matches the upstream command dialog's grouping (Session ->
# Agent -> Provider -> System -> Prompt) for review-friendliness.

OTTER_SLASH_COMMANDS: dict[str, SlashHandler] = {
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
    # Provider
    "connect": cmd_connect,
    "mcp": _cmd_mcp,
    "mcps": cmd_mcps,
    # System
    "help": cmd_help,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "config": _cmd_config,
    "cost": _cmd_cost,
    "compact": _cmd_compact,
    "init": _cmd_init,
    "themes": cmd_themes,
    "permissions": cmd_permissions,
    # W15-2 P2 (OPENCODE G24): plan-mode toggles
    "plan": cmd_plan,
    "exec": cmd_exec,
    "exit": _cmd_exit,
    "quit": cmd_quit,
    # Prompt
    "edit": cmd_edit,
}

# Alias used by :mod:`chimera.otter.repl._resolve_slash_registry`. Keep the
# two names in lockstep — exposing both lets the REPL pick up overrides via
# its existing contract while still satisfying callers that ask for the
# explicit ``OTTER_SLASH_COMMANDS`` symbol.
COMMANDS: dict[str, SlashHandler] = OTTER_SLASH_COMMANDS


OTTER_SLASH_HELP: dict[str, str] = {
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
    # Provider
    "connect": "connect a provider",
    "mcp": "list MCP servers and tools",
    "mcps": "list MCP servers and tools",
    # System
    "help": "show this list",
    "status": "one-screen status summary",
    "doctor": "environment health checks",
    "config": "print effective merged settings",
    "cost": "show cumulative cost",
    "compact": "force a HARD threshold compaction now",
    "init": "summarise the project",
    "themes": "switch the REPL theme (coming soon)",
    "permissions": "list/add/remove declarative permission rules",
    # W15-2 P2: plan-mode toggles
    "plan": "enter plan-mode (read-only tool surface; arg = seed task)",
    "exec": "leave plan-mode and resume the standard execution surface",
    "exit": "leave the REPL",
    "quit": "leave the REPL",
    # Prompt
    "edit": "open $EDITOR for the next prompt (coming soon)",
}


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

def _install_one(
    repl_state: Any, name: str, handler: SlashHandler, help_text: str,
) -> bool:
    """Install a single ``(name, handler, help_text)`` triple onto *repl_state*.

    Centralises the three-flavor compatibility shim used by both
    :func:`register_otter_slash` and :func:`register_custom_commands` so
    they stay in lockstep with the shared registry contract.

    Args:
        repl_state: Target REPL state. May expose ``register(...)``, a
            ``commands``/``slash_commands`` mapping, or neither.
        name: Slash-command name (without leading slash).
        handler: Callable with the ``(session, env, args, out)`` shape.
        help_text: One-line description for ``/help`` rendering.

    Returns:
        ``True`` if the command landed on the state, ``False`` otherwise.
    """
    register = getattr(repl_state, "register", None)
    if callable(register):
        try:
            register(name, handler, help_text)
            return True
        except TypeError:
            try:
                register(name, handler)
                return True
            except Exception:  # noqa: BLE001 -- best-effort install
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


def register_otter_slash(
    repl_state: Any,
    *,
    custom_commands: list["CustomCommand"] | None = None,
) -> int:
    """Install every otter slash command onto ``repl_state``.

    This composes with three flavors of REPL state, in priority order:

    1. The shared :mod:`chimera.cli.slash_commands` module itself, or
       any object exposing ``register(name, handler, help_text)``.
    2. A state object exposing a ``commands`` or ``slash_commands``
       mapping (for ad-hoc REPL fakes used in tests).
    3. Anything else: we ``setattr(repl_state, name, handler)`` so the
       commands at least become discoverable as attributes.

    The function never raises on a missing handler; missing surfaces
    are silently skipped so a partially-built REPL state still works.

    Args:
        repl_state: Target onto which the otter palette is installed.
        custom_commands: Optional list of user-defined
            :class:`~chimera.otter.commands.CustomCommand` instances loaded
            from ``.opencode/command/*.md``. Each is converted into a
            slash handler that renders the body template and pushes the
            result to the active session as a follow-up user message.
            Customs land **after** the built-in palette so a same-named
            user command wins (matching the upstream's last-wins
            precedence on conflicts).

    Returns:
        The count of commands successfully installed (built-ins +
        customs).
    """
    installed = 0
    for name, handler in OTTER_SLASH_COMMANDS.items():
        help_text = OTTER_SLASH_HELP.get(name, "")
        if _install_one(repl_state, name, handler, help_text):
            mark_origin(name, ORIGIN_BUILTIN, help_text)
            installed += 1

    if custom_commands:
        installed += register_custom_commands(repl_state, custom_commands)
    return installed


# ---------------------------------------------------------------------------
# Custom-command bridge (.opencode/command/*.md -> slash handler)
# ---------------------------------------------------------------------------


def _split_custom_args(raw: str) -> tuple[list[str], dict[str, str]]:
    """Split a slash-command argument line into positional + named pieces.

    Supported forms (matching the upstream's permissive parser):

    * ``foo bar baz`` — three positional arguments.
    * ``foo target=src/main.py`` — one positional plus a named ``target``.
    * ``"quoted phrase" key="value with space"`` — shell-style quoting
      via :mod:`shlex`.

    Returns:
        ``(positional, named)`` — both empty when *raw* is empty.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return [], {}
    try:
        tokens = shlex.split(cleaned, posix=True)
    except ValueError:
        # Unbalanced quotes — fall back to whitespace split so the user
        # still sees their intent reflected (the upstream degrades the
        # same way rather than refusing to dispatch).
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
    """Wrap a :class:`CustomCommand` as a slash-registry handler.

    The returned callable matches the canonical
    ``(session, env, args, out)`` signature. On invocation it:

    1. Parses the raw argument string into positional + ``key=value``
       named pieces via :func:`_split_custom_args`.
    2. Renders the template via :meth:`CustomCommand.render`.
    3. Sends the rendered prompt to the active turn:

       * ``session.queue(rendered)`` when available — queues a
         follow-up user message for the next turn.
       * ``session.steer(rendered)`` when ``queue`` is missing but
         ``steer`` exists — interrupts the running turn.
       * Otherwise, prints the rendered text via *out* so the user at
         least sees what would have been sent.

    Errors raised by ``render`` or by the session never propagate — the
    handler prints a one-line diagnostic and returns. Crashing the REPL
    over a bad template would be hostile.

    Args:
        cmd: The user-defined command to wrap.

    Returns:
        A :data:`SlashHandler` ready to install on the slash registry.
    """

    def _handler(session: Any, _env: Any, args: str, out: PrintFn) -> None:
        positional, named = _split_custom_args(args)
        try:
            rendered = cmd.render(*positional, **named)
        except Exception as exc:  # noqa: BLE001 -- never crash REPL
            out(f"/{cmd.name} render failed: {exc}")
            return

        # Prefer queue() so the rendered prompt is treated as a normal
        # follow-up user turn. Steer is the next-best (interrupts the
        # current turn). Final fallback is just printing.
        queue = getattr(session, "queue", None)
        if callable(queue):
            try:
                queue(rendered)
                out(f"/{cmd.name} queued ({len(rendered)} chars)")
                return
            except Exception as exc:  # noqa: BLE001
                out(f"/{cmd.name} queue failed: {exc}")
                # Fall through to steer/print.

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
    """Install user-defined commands onto a slash registry.

    Each :class:`~chimera.otter.commands.CustomCommand` becomes a
    runnable slash handler via :func:`build_custom_command_handler`. The
    same three-flavor compatibility shim used by
    :func:`register_otter_slash` is reused so this composes with the
    shared registry, dict-style fakes, and bare attribute objects.

    Same-named entries clobber prior ones — the caller's ordering
    decides precedence. The standard otter wiring registers built-ins
    first, then customs, so user files override built-ins on conflict
    (matching the upstream's last-wins ladder).

    After installation, the readline tab-completion view is refreshed
    via :func:`_refresh_completion` so the new ``/<custom>`` names
    appear in ``<TAB>`` cycling on the very next prompt — without this,
    the REPL completer would show a stale snapshot from before the
    customs landed (W4 follow-up, F7).

    Args:
        repl_state: Target slash registry / REPL state.
        commands: Custom commands to install. Empty list is a no-op.

    Returns:
        Count of commands successfully installed.
    """
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
    """Install plugin-contributed slash commands onto a slash registry.

    W2 ships plugin slash commands as
    :class:`chimera.otter.plugins.OtterCommand` records that already
    carry a ``name``, ``description``, and (after
    :func:`chimera.otter.cli._make_plugin_command_handler`) a
    materialized handler. This helper is the F8 origin-aware mirror of
    :func:`register_custom_commands`: it installs each command via
    :func:`_install_one` and tags it with :data:`ORIGIN_PLUGIN` so
    :func:`cmd_help` renders it under the "Plugin commands" section.

    Args:
        repl_state: Target slash registry / REPL state. Same three-flavor
            compatibility shim as :func:`register_otter_slash`.
        commands: Plugin command records. Each must expose ``name`` and
            (optionally) ``description``. The dispatch target comes from
            ``handler_factory`` — by default we pull a callable
            ``handler`` attribute off the record.
        handler_factory: Optional callable that turns a plugin command
            record into a :data:`SlashHandler`. Defaults to looking up
            ``cmd.handler`` on the record so callers that already
            materialised the handler don't need to pass anything.

    Returns:
        Count of plugin commands successfully installed.
    """
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
        except Exception:  # noqa: BLE001 -- never crash the REPL over a bad plugin
            continue
        help_text = getattr(cmd, "description", "") or f"plugin command: /{name}"
        if _install_one(repl_state, name, handler, help_text):
            mark_origin(name, ORIGIN_PLUGIN, help_text)
            installed += 1
    if installed:
        _refresh_completion(repl_state)
    return installed


def _refresh_completion(repl_state: Any) -> None:
    """Resync tab-completion after custom commands land on *repl_state*.

    Two refresh paths fire (best-effort; never raise so REPL stays
    alive):

    1. **Shared-registry view.** When *repl_state* exposes
       ``refresh_command_names`` (the :mod:`chimera.cli.slash_commands`
       module does), call it so the :data:`COMMAND_NAMES` list seen by
       :func:`chimera.cli.code._complete_command` is rebuilt from the
       live registry.
    2. **Active readline completer.** When :mod:`readline` is
       importable and a completer is currently bound, re-bind it. The
       default completer in :mod:`chimera.cli.code` already reads
       names dynamically, but rebinding forces readline to discard any
       cached internal state (display columns, last-match list).

    Args:
        repl_state: The REPL state the customs were installed onto.
    """
    refresh = getattr(repl_state, "refresh_command_names", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # noqa: BLE001 -- never crash REPL on refresh
            pass

    try:
        import readline
    except ImportError:
        return
    try:
        completer = readline.get_completer()
    except Exception:  # noqa: BLE001 -- some readline shims lack getter
        return
    if completer is None:
        return
    try:
        readline.set_completer(completer)
    except Exception:  # noqa: BLE001
        return
