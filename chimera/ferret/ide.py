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
from typing import Any

from chimera.otter.acp import (
    ACPSessionState,
    AgentFactory,
    JsonValue,
    OtterACPServer,
)

__all__ = [
    "FERRET_ACP_AGENT_NAME",
    "FERRET_ACP_PROTOCOL_VERSION",
    "FerretACPServer",
    "build_ide_serve_parser",
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
