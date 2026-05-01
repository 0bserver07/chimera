"""Ferret ACP server with IDE-friendly notification schema.

This module is the *default* transport behind ``chimera ferret serve``.
HTTP is opt-in via ``--http`` and is wired by sibling agent FF1 in
:mod:`chimera.ferret.cli`. Ferret follows the IDE-first / sandbox-first
posture mirrored from the upstream IDE-first OpenAI-flagship coding
agent: external IDEs (Zed, VS Code extensions, JetBrains plugins) drive
ferret over newline-delimited JSON-RPC 2.0 on stdio.

What this adds over otter
-------------------------

:class:`chimera.otter.acp.OtterACPServer` already speaks plain ACP:
``initialize`` / ``session/new`` / ``session/message`` /
``session/cancel`` / ``tool/approve`` and emits ``session/update``
notifications around the turn boundary. That's correct for headless
clients but loses information IDEs want to render natively.

:class:`FerretACPServer` extends that surface with four IDE-shaped
notification kinds (still over the same ``session/update`` envelope so
plain ACP clients can simply ignore them):

* ``code/diff`` — a unified diff for a single file write. Lets the IDE
  render an inline diff gutter rather than the bare
  ``tool_call_finished`` envelope plain otter sends. Mirrors upstream's
  ``item/fileChange/outputDelta`` + ``FileChange`` shape.
* ``editor/open_file`` — a one-shot "open this file at this line"
  request, used when the agent wants to draw the user's eye to a
  specific location (e.g. after creating a new test file). Mirrors
  upstream's editor-side IPC.
* ``terminal/output`` — a streamed bash output chunk with an explicit
  stream tag (``stdout`` / ``stderr``) and a sequence id. Mirrors
  upstream's ``command/exec/outputDelta`` shape; lets the IDE render a
  live terminal pane instead of one fat blob at the end.
* ``progress/step`` — per-step thinking/action markers (``thinking``,
  ``tool_call``, ``response``, ``done``) for the IDE progress UI.
  Lighter-weight than ``item/started`` / ``item/completed`` envelopes.

Schema toggle
-------------

The ``ide_schema`` flag (default ``True``) controls whether the IDE
kinds are emitted at all. When ``False`` the helpers degrade to plain
otter ``session/update`` notifications so an HTTP-only relay or test
that doesn't understand the new kinds still works. ``--ide-schema
false`` on the CLI flips it.

Trademark hygiene: this module never names the upstream IDE-first
OpenAI-flagship coding agent. The ACP wire shape itself is an open spec.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import os
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from chimera.otter.acp import (
    ACPSessionState,
    AgentFactory,
    JsonValue,
    OtterACPServer,
)

if TYPE_CHECKING:
    from chimera.events.base import EventBus
    from chimera.otter.server import OtterSessionState

__all__ = [
    "FERRET_ACP_AGENT_NAME",
    "FERRET_ACP_PROTOCOL_VERSION",
    "FerretACPServer",
    "IDENotificationEmitter",
    "build_ide_serve_parser",
    "ide_emit_for_state",
    "maybe_serve_ide_acp",
    "serve_stdio_ide",
    "unified_diff",
]


#: Wire-level protocol version reported by ``initialize``. Bumped when the
#: ferret-specific notification shape changes in a way clients must notice.
#: Distinct from :data:`chimera.otter.acp.OTTER_ACP_PROTOCOL_VERSION` because
#: the IDE notification kinds expand the protocol surface.
FERRET_ACP_PROTOCOL_VERSION = 1

#: Name reported in ``initialize.agentInfo.name``. Trademark-clean.
FERRET_ACP_AGENT_NAME = "ferret"


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------


def unified_diff(
    *,
    path: str,
    before: str,
    after: str,
    context_lines: int = 3,
) -> str:
    """Return a unified-diff string for a file write.

    Args:
        path: File path the diff applies to. Used in the diff header.
        before: File contents *before* the write. Empty string for
            newly-created files.
        after: File contents *after* the write. Empty string for deletes.
        context_lines: Number of context lines around each hunk.

    Returns:
        A unified-diff string with ``--- <path>`` / ``+++ <path>``
        headers. Empty string when the two inputs are identical (caller
        decides whether to skip emission in that case).
    """
    if before == after:
        return ""
    before_lines = before.splitlines(keepends=True) if before else []
    after_lines = after.splitlines(keepends=True) if after else []
    # Mirror unix ``diff -u`` headers; the path appears in both header
    # lines so IDE clients can render side-by-side without extra parsing.
    diff_iter = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=path,
        tofile=path,
        n=context_lines,
    )
    return "".join(diff_iter)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class FerretACPServer(OtterACPServer):
    """ACP server with IDE-friendly notification kinds.

    Composition over rebuild: this subclass reuses every otter handler
    (initialize/session/tool-approve) and only customizes the
    ``initialize`` capability bag plus four ``notify_*`` helper methods
    that emit the new kinds. Tool/file/bash hooks call those helpers
    rather than raw ``session/update`` envelopes so the schema toggle
    can flip them off in one place.

    Args:
        agent_factory: Same shape as
            :class:`chimera.otter.acp.OtterACPServer` — a callable that
            builds an agent for a session.
        ide_schema: When ``True`` (default), emit IDE-shaped
            notifications (``code/diff``, ``editor/open_file``,
            ``terminal/output``, ``progress/step``). When ``False``,
            degrade to plain otter ``session/update`` notifications so
            schema-naive clients still see the activity.
        reader, writer, protocol_version, agent_name, agent_version:
            Forwarded to the otter base class. ``agent_name`` defaults
            to ``"ferret"`` and ``protocol_version`` to
            :data:`FERRET_ACP_PROTOCOL_VERSION`.
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        ide_schema: bool = True,
        reader: Any = None,
        writer: Any = None,
        protocol_version: int = FERRET_ACP_PROTOCOL_VERSION,
        agent_name: str = FERRET_ACP_AGENT_NAME,
        agent_version: str = "0.0.0",
    ) -> None:
        super().__init__(
            agent_factory,
            reader=reader,
            writer=writer,
            protocol_version=protocol_version,
            agent_name=agent_name,
            agent_version=agent_version,
        )
        self.ide_schema = ide_schema

    # -- handlers -----------------------------------------------------------

    async def _handle_initialize(self, params: JsonValue) -> JsonValue:
        """Override otter's capability bag to advertise IDE kinds."""
        result = await super()._handle_initialize(params)
        # Defensive: super() returns a dict but mypy sees ``JsonValue``.
        if not isinstance(result, dict):
            return result
        caps = result.setdefault("agentCapabilities", {})
        if isinstance(caps, dict):
            caps["ideSchema"] = self.ide_schema
            if self.ide_schema:
                # Capability advertisement so clients can feature-detect
                # without sending a probe message.
                caps["ideNotifications"] = {
                    "codeDiff": True,
                    "editorOpenFile": True,
                    "terminalOutput": True,
                    "progressStep": True,
                }
        result["agentInfo"] = {
            "name": self._agent_name,
            "version": self._agent_version,
        }
        return result

    # -- IDE-friendly emitters ---------------------------------------------

    async def notify_code_diff(
        self,
        state: ACPSessionState,
        *,
        path: str,
        before: str,
        after: str,
        change_kind: str | None = None,
    ) -> None:
        """Emit a unified-diff payload for a single file write.

        When :attr:`ide_schema` is ``False``, this falls through to a
        plain ``tool_call_finished``-shaped notification so otter-only
        clients still see the write happened.

        Args:
            state: The session whose turn produced the write.
            path: File path the diff applies to.
            before: File contents before the write (``""`` for create).
            after: File contents after the write (``""`` for delete).
            change_kind: Optional override for the change kind
                (``"add"`` / ``"delete"`` / ``"update"``). When omitted,
                inferred from ``before``/``after``.
        """
        kind = change_kind or self._infer_change_kind(before, after)
        diff_text = unified_diff(path=path, before=before, after=after)
        if not self.ide_schema:
            # Degrade to plain otter shape — opaque tool result envelope.
            await self._notify(
                "session/update",
                {
                    "sessionId": state.session_id,
                    "update": {
                        "sessionUpdate": "tool_call_finished",
                        "tool": {"name": "write_file", "input": {"path": path}},
                    },
                },
            )
            return
        await self._notify(
            "session/update",
            {
                "sessionId": state.session_id,
                "update": {
                    "sessionUpdate": "code/diff",
                    "path": path,
                    "changeKind": kind,
                    "unifiedDiff": diff_text,
                },
            },
        )

    async def notify_editor_open_file(
        self,
        state: ACPSessionState,
        *,
        path: str,
        line: int | None = None,
        column: int | None = None,
        preview: str | None = None,
    ) -> None:
        """Ask the IDE to open ``path`` at the given location.

        Args:
            state: The active session.
            path: File the IDE should open.
            line: 1-based line number to focus. ``None`` leaves the
                cursor wherever the IDE prefers (typically file start).
            column: 1-based column number to focus. Optional.
            preview: Optional short snippet the IDE can show as a
                tooltip if the user hasn't accepted the open yet.
        """
        if not self.ide_schema:
            # No reasonable otter fallback for this one — emit a
            # human-readable agent_message_chunk so clients see *some*
            # signal that the file is interesting.
            text = f"[ferret] open file: {path}"
            if line is not None:
                text += f":{line}"
            await self._notify(
                "session/update",
                {
                    "sessionId": state.session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                    },
                },
            )
            return
        update: dict[str, JsonValue] = {
            "sessionUpdate": "editor/open_file",
            "path": path,
        }
        if line is not None:
            update["line"] = int(line)
        if column is not None:
            update["column"] = int(column)
        if preview is not None:
            update["preview"] = preview
        await self._notify(
            "session/update",
            {"sessionId": state.session_id, "update": update},
        )

    async def notify_terminal_output(
        self,
        state: ACPSessionState,
        *,
        process_id: str,
        stream: str,
        chunk: str,
        sequence: int = 0,
        cap_reached: bool = False,
    ) -> None:
        """Stream a bash output chunk to the IDE's terminal pane.

        Args:
            state: The active session.
            process_id: Stable id for the running process; the IDE uses
                this to bucket chunks into the right terminal tab.
            stream: ``"stdout"`` or ``"stderr"``.
            chunk: The output bytes (already decoded — IDEs prefer
                strings over base64 for the common UTF-8 case). Long
                chunks should be split by the caller, not here.
            sequence: Monotonically-increasing chunk sequence number;
                lets the IDE detect drops.
            cap_reached: ``True`` when the agent's output cap clipped
                later bytes from this stream.
        """
        if stream not in ("stdout", "stderr"):
            raise ValueError(f"stream must be 'stdout' or 'stderr', got {stream!r}")
        if not self.ide_schema:
            # Otter fallback: surface the chunk as an opaque text frame.
            await self._notify(
                "session/update",
                {
                    "sessionId": state.session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": chunk},
                    },
                },
            )
            return
        await self._notify(
            "session/update",
            {
                "sessionId": state.session_id,
                "update": {
                    "sessionUpdate": "terminal/output",
                    "processId": process_id,
                    "stream": stream,
                    "chunk": chunk,
                    "sequence": int(sequence),
                    "capReached": bool(cap_reached),
                },
            },
        )

    async def notify_progress_step(
        self,
        state: ACPSessionState,
        *,
        phase: str,
        step: int,
        detail: str | None = None,
    ) -> None:
        """Emit a per-step progress marker for the IDE progress UI.

        Args:
            state: The active session.
            phase: One of ``"thinking"``, ``"tool_call"``, ``"response"``,
                ``"done"``. Other values are accepted (for forward
                compatibility) but the four above are what stock IDE
                renderers know how to draw.
            step: Monotonically-increasing step counter for the turn.
            detail: Optional human-readable detail string the IDE can
                show next to the progress indicator.
        """
        if not self.ide_schema:
            return  # Plain otter has no progress concept; drop silently.
        update: dict[str, JsonValue] = {
            "sessionUpdate": "progress/step",
            "phase": phase,
            "step": int(step),
        }
        if detail is not None:
            update["detail"] = detail
        await self._notify(
            "session/update",
            {"sessionId": state.session_id, "update": update},
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _infer_change_kind(before: str, after: str) -> str:
        if not before and after:
            return "add"
        if before and not after:
            return "delete"
        return "update"


# ---------------------------------------------------------------------------
# HTTP+SSE bridge — fan IDE-shaped notifications into the otter SSE channel
# ---------------------------------------------------------------------------


#: Type alias for the emit callable used by :class:`IDENotificationEmitter`.
#: Mirrors :meth:`chimera.otter.server.OtterServer.emit_event` minus the
#: state argument: ``(event_name, data) -> None``.
EmitCallable = Callable[[str, dict[str, Any]], None]


def ide_emit_for_state(state: "OtterSessionState") -> EmitCallable:
    """Return an emit callable bound to *state* that mirrors otter's SSE shape.

    The HTTP server's :meth:`OtterServer.emit_event` is the production path
    for fan-out, but the per-session :func:`agent_factory` only receives a
    :class:`~chimera.otter.server.OtterSessionState` — not the server
    instance. We mirror its append-and-fan-out logic here so an
    :class:`IDENotificationEmitter` wired in the factory can drop frames
    onto the same SSE stream every other otter event uses.

    The returned callable is thread-safe: it acquires ``state.lock`` while
    appending to the events list and snapshotting subscribers, then
    releases the lock before fanning out (matching :class:`OtterServer`).

    Args:
        state: The HTTP session whose ``events`` and ``subscribers`` lists
            should receive the IDE-shaped frames.

    Returns:
        A callable ``(event_name, data) -> None`` that writes one SSE
        envelope per call. Frames look the same as any other otter SSE
        frame: ``{"id", "event", "data", "timestamp"}``.
    """

    def _emit(event_name: str, data: dict[str, Any]) -> None:
        envelope: dict[str, Any] = {
            "id": str(len(state.events) + 1),
            "event": event_name,
            "data": data,
            "timestamp": time.time(),
        }
        with state.lock:
            state.events.append(envelope)
            subscribers = list(state.subscribers)
        for q in subscribers:
            try:
                q.put_nowait(envelope)
            except queue.Full:  # pragma: no cover - unbounded queues today
                pass

    return _emit


class IDENotificationEmitter:
    """Translate Chimera EventBus frames into IDE-shaped SSE notifications.

    The four IDE-friendly notification kinds — ``code/diff``,
    ``editor/open_file``, ``terminal/output``, ``progress/step`` — are
    already shipped over the ACP transport by :class:`FerretACPServer`.
    Wave-9 lifts them onto the HTTP+SSE transport so the same JSON
    payloads (one frame per event, identical field names) reach
    HTTP-bound IDE plugins through ``GET /session/<id>/events``.

    Wiring lives entirely on a :class:`~chimera.events.base.EventBus`:
    the agent's :class:`LoopConfig` carries the bus, the loop publishes
    :class:`ToolCallEvent` / :class:`ToolResultEvent` as it runs, and
    this class's :meth:`attach` registers handlers that translate the
    write-file / bash hits into IDE-shaped frames. The same instance can
    also be driven directly via :meth:`emit_code_diff` /
    :meth:`emit_editor_open_file` / :meth:`emit_terminal_output` /
    :meth:`emit_progress_step` for callers that want explicit control
    (e.g. emitting an ``editor/open_file`` after a successful test run).

    Trademark hygiene: the wire shape is the same open ``session/update``
    schema documented in :mod:`chimera.ferret.ide`; nothing IDE-vendor
    specific leaks into the names.

    Args:
        emit: Callable ``(event_name, data) -> None`` that drops a single
            SSE frame onto the session stream. Production callers obtain
            this from :func:`ide_emit_for_state`; tests inject a list-
            backed spy.
        ide_schema: When ``True`` (default) emit the rich IDE kinds;
            when ``False`` skip translation entirely so HTTP-only relays
            that don't speak the IDE schema see only the otter base
            shape (``loop_event`` / ``result``). Mirrors the
            :class:`FerretACPServer.ide_schema` toggle.
    """

    #: Tools whose ``ToolCallEvent`` / ``ToolResultEvent`` pair should fan
    #: out as a ``code/diff`` IDE frame. The set matches
    #: :data:`chimera.core.tool_executor._FILE_MODIFYING_TOOLS` so the
    #: HTTP transport tracks the same tools the loop already treats as
    #: file-modifying for permission purposes.
    _DIFF_TOOLS = frozenset({"write_file", "edit_file", "replace_in_file"})

    #: Tool name whose ``ToolResultEvent`` fan-out becomes a
    #: ``terminal/output`` IDE frame. ``bash`` is the only stock tool
    #: whose output is naturally terminal-shaped; other tools degrade to
    #: opaque ``loop_event`` frames.
    _TERMINAL_TOOLS = frozenset({"bash"})

    def __init__(
        self,
        emit: EmitCallable,
        *,
        ide_schema: bool = True,
    ) -> None:
        self._emit = emit
        self.ide_schema = bool(ide_schema)
        # WHY: ToolCallEvent fires before the tool runs, ToolResultEvent
        # after. We need both to materialize a unified diff (the "before"
        # snapshot is captured at call time, the "after" comes from the
        # filesystem at result time). Keep a per-call_id pending-call map
        # so the ``ToolResultEvent`` handler can pair up.
        self._pending_calls: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Monotonic step counter for ``progress/step``. Bumped on every
        # tool_call and tool_result the bus delivers.
        self._step = 0
        # Monotonic terminal-chunk sequence counter, partitioned by the
        # synthesized process_id (the bash call_id). Lets the IDE detect
        # drops when multiple bash calls are interleaved.
        self._term_seq: dict[str, int] = {}

    # -- bus wiring ---------------------------------------------------------

    def attach(self, bus: "EventBus") -> Callable[[], None]:
        """Subscribe the translator to *bus* and return an unsubscribe handle.

        Called by :func:`chimera.ferret.cli._dispatch_serve_http`'s
        per-session agent factory once the :class:`LoopConfig` has been
        constructed. The returned callable detaches every handler this
        emitter registered — useful in tests that reuse a bus.

        Args:
            bus: The :class:`EventBus` carried on
                :attr:`LoopConfig.event_bus`. Subscriptions are exact-
                type (no wildcards) so unrelated events don't pay the
                translation cost.

        Returns:
            A zero-arg callable that removes every handler this emitter
            registered, idempotent.
        """
        unsubscribers: list[Callable[[], None]] = []
        unsubscribers.append(
            bus.subscribe("tool_call", self._on_tool_call)
        )
        unsubscribers.append(
            bus.subscribe("tool_result", self._on_tool_result)
        )

        def _detach() -> None:
            for u in unsubscribers:
                try:
                    u()
                except Exception:  # noqa: BLE001 - best-effort
                    pass

        return _detach

    # -- bus handlers -------------------------------------------------------

    def _on_tool_call(self, event: Any) -> None:
        """Stash the call snapshot and emit a ``progress/step`` marker."""
        if not self.ide_schema:
            return
        tool_name = getattr(event, "tool_name", "") or ""
        call_id = getattr(event, "call_id", "") or ""
        arguments = getattr(event, "arguments", None) or {}

        with self._lock:
            self._step += 1
            step_no = self._step

        # Always emit a progress marker for the IDE progress UI.
        self.emit_progress_step(
            phase="tool_call",
            step=step_no,
            detail=f"{tool_name}",
        )

        if not call_id:
            return
        if tool_name in self._DIFF_TOOLS:
            # Snapshot the file's current contents BEFORE the tool runs
            # so :meth:`_on_tool_result` can produce a unified diff. We
            # tolerate any IO error (file may not exist yet) by
            # recording an empty ``before``.
            path = self._extract_path(arguments)
            before = ""
            if path:
                try:
                    with open(path, encoding="utf-8") as fh:
                        before = fh.read()
                except (OSError, UnicodeDecodeError):
                    before = ""
            with self._lock:
                self._pending_calls[call_id] = {
                    "tool": tool_name,
                    "path": path,
                    "before": before,
                    "arguments": arguments,
                }
        elif tool_name in self._TERMINAL_TOOLS:
            # Bash calls don't need a "before" snapshot — we stash the
            # tool name only so :meth:`_on_tool_result` knows to fan out
            # a ``terminal/output`` frame instead of skipping.
            with self._lock:
                self._pending_calls[call_id] = {
                    "tool": tool_name,
                    "arguments": arguments,
                }

    def _on_tool_result(self, event: Any) -> None:
        """Translate file writes / bash output into IDE frames."""
        if not self.ide_schema:
            return
        call_id = getattr(event, "call_id", "") or ""
        success = bool(getattr(event, "success", True))
        output = getattr(event, "output", "") or ""

        with self._lock:
            self._step += 1
            step_no = self._step
            pending = self._pending_calls.pop(call_id, None)

        # Per-step progress marker for the IDE so it can advance its
        # spinner without waiting for the next tool call.
        self.emit_progress_step(
            phase="response",
            step=step_no,
            detail=("ok" if success else "error"),
        )

        if pending is not None and success:
            tool_name = pending.get("tool", "")
            if tool_name in self._DIFF_TOOLS:
                path = pending.get("path") or ""
                before = pending.get("before") or ""
                after = ""
                if path:
                    try:
                        with open(path, encoding="utf-8") as fh:
                            after = fh.read()
                    except (OSError, UnicodeDecodeError):
                        after = ""
                self.emit_code_diff(
                    path=path,
                    before=before,
                    after=after,
                )
                return

        # ``bash`` results carry interleaved stdout/stderr in ``output``.
        # We emit the whole blob as a single stdout chunk; richer
        # streaming would require splitting on the wire (left for a
        # follow-up wave) but this still lets the IDE render a live
        # terminal pane instead of one giant final blob.
        tool_name = ""
        if pending is not None:
            tool_name = pending.get("tool", "") or ""
        if not tool_name:
            # ToolResultEvent doesn't carry the tool name directly; we
            # rely on the pending-call snapshot. When it's absent (e.g.
            # a non-DIFF, non-BASH tool we never stashed) skip terminal
            # emission — the otter base ``loop_event`` frame already
            # carries the result.
            return
        if tool_name in self._TERMINAL_TOOLS and output:
            with self._lock:
                self._term_seq[call_id] = self._term_seq.get(call_id, 0) + 1
                seq = self._term_seq[call_id]
            self.emit_terminal_output(
                process_id=call_id,
                stream=("stdout" if success else "stderr"),
                chunk=str(output),
                sequence=seq,
            )

    # -- explicit emitters --------------------------------------------------

    def emit_code_diff(
        self,
        *,
        path: str,
        before: str,
        after: str,
        change_kind: str | None = None,
    ) -> None:
        """Drop a ``code/diff`` SSE frame onto the session stream.

        Mirrors :meth:`FerretACPServer.notify_code_diff` shape exactly so
        IDE plugins can consume frames from either transport without
        branching.
        """
        if not self.ide_schema:
            return
        kind = change_kind or self._infer_change_kind(before, after)
        diff_text = unified_diff(path=path, before=before, after=after)
        self._emit(
            "code/diff",
            {
                "sessionUpdate": "code/diff",
                "path": path,
                "changeKind": kind,
                "unifiedDiff": diff_text,
            },
        )

    def emit_editor_open_file(
        self,
        *,
        path: str,
        line: int | None = None,
        column: int | None = None,
        preview: str | None = None,
    ) -> None:
        """Drop an ``editor/open_file`` SSE frame onto the session stream."""
        if not self.ide_schema:
            return
        data: dict[str, Any] = {
            "sessionUpdate": "editor/open_file",
            "path": path,
        }
        if line is not None:
            data["line"] = int(line)
        if column is not None:
            data["column"] = int(column)
        if preview is not None:
            data["preview"] = preview
        self._emit("editor/open_file", data)

    def emit_terminal_output(
        self,
        *,
        process_id: str,
        stream: str,
        chunk: str,
        sequence: int = 0,
        cap_reached: bool = False,
    ) -> None:
        """Drop a ``terminal/output`` SSE frame onto the session stream."""
        if not self.ide_schema:
            return
        if stream not in ("stdout", "stderr"):
            raise ValueError(
                f"stream must be 'stdout' or 'stderr', got {stream!r}"
            )
        self._emit(
            "terminal/output",
            {
                "sessionUpdate": "terminal/output",
                "processId": process_id,
                "stream": stream,
                "chunk": chunk,
                "sequence": int(sequence),
                "capReached": bool(cap_reached),
            },
        )

    def emit_progress_step(
        self,
        *,
        phase: str,
        step: int,
        detail: str | None = None,
    ) -> None:
        """Drop a ``progress/step`` SSE frame onto the session stream."""
        if not self.ide_schema:
            return
        data: dict[str, Any] = {
            "sessionUpdate": "progress/step",
            "phase": phase,
            "step": int(step),
        }
        if detail is not None:
            data["detail"] = detail
        self._emit("progress/step", data)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _infer_change_kind(before: str, after: str) -> str:
        if not before and after:
            return "add"
        if before and not after:
            return "delete"
        return "update"

    @staticmethod
    def _extract_path(arguments: Any) -> str:
        """Pull a filesystem path out of ``arguments`` for write-file tools.

        The stock tools accept ``path`` (write_file, replace_in_file) or
        ``file_path`` (edit_file). We try both keys and degrade to ``""``
        when neither is present so the caller can decide whether to skip.
        """
        if not isinstance(arguments, dict):
            return ""
        for key in ("path", "file_path", "filename"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return value
        return ""


# ---------------------------------------------------------------------------
# CLI plumbing — late-binding hook for ``chimera ferret serve``
# ---------------------------------------------------------------------------


def build_ide_serve_parser(parser: argparse.ArgumentParser) -> None:
    """Register the IDE-relevant ``serve`` flags on ``parser``.

    FF1 owns the top-level ``chimera ferret`` argparse tree; this helper
    lets that tree pick up the ferret-specific ``--http`` and
    ``--ide-schema`` flags by passing in its existing ``serve`` parser.

    Args:
        parser: The argparse subparser owning ``chimera ferret serve``.
    """
    parser.add_argument(
        "--http",
        action="store_true",
        help=(
            "Run the HTTP server instead of the default ACP (IDE)"
            " server. ACP is the default; pass --http to opt in."
        ),
    )
    # WHY: an IDE plugin can disable the ferret-specific notification
    # kinds for compat with a strict otter-shaped relay. Default ON so
    # local IDEs get the rich schema for free.
    parser.add_argument(
        "--ide-schema",
        dest="ide_schema",
        type=_parse_bool,
        default=True,
        help=(
            "Emit IDE-friendly notification kinds (code/diff,"
            " editor/open_file, terminal/output, progress/step)."
            " Default: true. Pass 'false' to fall back to plain ACP."
        ),
    )


def _parse_bool(value: str) -> bool:
    """Argparse-friendly bool parser accepting 'true'/'false'/'1'/'0'."""
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    raise argparse.ArgumentTypeError(
        f"expected boolean (true/false), got {value!r}"
    )


def maybe_serve_ide_acp(args: argparse.Namespace) -> int | None:
    """Return an exit code if ``args`` selects the ACP transport, else None.

    FF1 will call this from ``chimera/ferret/cli.py:_dispatch_serve``::

        rc = maybe_serve_ide_acp(args)
        if rc is not None:
            return rc
        return _dispatch_serve_http(args)

    The late-binding shape lets this module land before ``cli.py`` does
    without forcing a tight import order.

    Args:
        args: Parsed argparse namespace from ``chimera ferret serve``.
            Inspected for ``http`` (truthy => HTTP) and ``ide_schema``.

    Returns:
        Exit code from running the ACP server, or ``None`` when the
        caller asked for the HTTP transport (``--http``).
    """
    if getattr(args, "http", False):
        return None
    ide_schema = bool(getattr(args, "ide_schema", True))
    factory = _build_default_agent_factory(args)
    return serve_stdio_ide(factory, ide_schema=ide_schema)


def _build_default_agent_factory(args: argparse.Namespace) -> AgentFactory:
    """Build the per-session agent factory used by the default ``serve``.

    Imports stay inside the function so ``chimera ferret serve --help``
    is cheap and doesn't drag in the provider stack.

    Args:
        args: Parsed argparse namespace; we read ``model``, ``cwd``,
            and ``max_steps`` if present.

    Returns:
        A factory matching :data:`chimera.otter.acp.AgentFactory`.
    """

    def _factory(state: ACPSessionState) -> Any:
        # Late binding: keep heavy imports out of module load. The real
        # provider chain lives in :mod:`chimera.ferret.providers` (FF6);
        # we route through the generic factory so this module loads even
        # before FF6 lands.
        from chimera.core.agent import Agent
        from chimera.core.loop import ReAct
        from chimera.core.prompt import Prompt
        from chimera.core.tool_group import AGENT_TOOLS
        from chimera.env.local import LocalEnvironment
        from chimera.providers.factory import create_provider

        model = getattr(args, "model", None)
        cwd = getattr(args, "cwd", None) or state.working_dir or os.getcwd()
        max_steps = int(getattr(args, "max_steps", 30) or 30)

        provider = create_provider(model=model) if model else create_provider()
        env = LocalEnvironment(workdir=cwd)
        env.setup()
        prompt = Prompt.from_string(
            "You are Ferret, a Chimera coding agent driven over IDE-ACP."
        )
        loop = ReAct(max_steps=max_steps)
        return Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=loop,
            prompt=prompt,
        )

    return _factory


def serve_stdio_ide(
    agent_factory: AgentFactory,
    *,
    ide_schema: bool = True,
) -> int:
    """Run :class:`FerretACPServer` on stdio until EOF.

    Args:
        agent_factory: Per-session agent factory, same shape as
            :data:`chimera.otter.acp.AgentFactory`.
        ide_schema: Toggle IDE-shaped notifications (default ``True``).

    Returns:
        Process exit code (``0`` on clean shutdown, ``130`` on Ctrl-C,
        ``1`` if asyncio raised).
    """
    server = FerretACPServer(agent_factory, ide_schema=ide_schema)
    try:
        asyncio.run(server.serve_forever())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        return 1
