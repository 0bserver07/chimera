"""EventBus -> SqliteEventStore translation layer.

:class:`EventSourcingSink` subscribes to the existing
:class:`chimera.events.base.EventBus` and converts each emission into a
typed sourced event before appending to a :class:`SqliteEventStore`.

Why a sink instead of changing the tool executor / loop signatures:

* The classic :class:`EventBus` already publishes :class:`ToolCallEvent`,
  :class:`ToolResultEvent`, :class:`PermissionEvent`, :class:`ModelRequestEvent`,
  :class:`ModelResponseEvent`, :class:`CompactionEvent`, :class:`ErrorEvent`,
  :class:`SessionEvent`, and :class:`AgentEndEvent` from the loop and
  tool executor.  Subscribing once at startup wires *every* call site
  without touching them.
* Keeps the sourcing subsystem opt-in: callers attach a sink only when
  they want SQLite persistence.

The sink also exposes :meth:`record_user_message`,
:meth:`record_session_created`, and :meth:`record_session_ended` for
callers that have these moments in hand (Session wrappers don't go
through the EventBus today).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

from chimera.events.sourcing.sqlite_store import SqliteEventStore
from chimera.events.sourcing.types import (
    AgentResultEvent,
    CompactionPerformedEvent,
    ErrorOccurredEvent,
    FileMutatedEvent,
    ModelRequestedEvent,
    ModelRespondedEvent,
    PermissionDecidedEvent,
    SessionCreatedEvent,
    SessionEndedEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
    UserMessageEvent,
)

if TYPE_CHECKING:
    from chimera.events.base import Event, EventBus

__all__ = ["EventSourcingSink"]


# Tools that touch the filesystem — used to synthesize FileMutatedEvent
# from ToolCallEvent / ToolResultEvent pairs.
_FILE_MODIFYING_TOOLS = frozenset({"write_file", "edit_file", "replace_in_file"})


class EventSourcingSink:
    """Translate :class:`EventBus` events into a :class:`SqliteEventStore`.

    Args:
        store: The destination SQLite store.
        aggregate_id: The aggregate (typically session) id used for every
            event recorded by this sink.
        bus: Optional bus to subscribe immediately.  Callers that hand the
            bus in later can call :meth:`attach`.
    """

    def __init__(
        self,
        store: SqliteEventStore,
        aggregate_id: str,
        bus: EventBus | None = None,
    ) -> None:
        self._store = store
        self._aggregate_id = aggregate_id
        self._unsubs: list[Callable[[], None]] = []
        # Buffer of in-flight tool calls so we can correlate ToolResultEvent
        # back to the originating tool name + arguments (the result event
        # only carries call_id).
        self._pending_calls: dict[str, dict[str, object]] = {}
        if bus is not None:
            self.attach(bus)

    # ------------------------------------------------------------------
    # Subscription lifecycle
    # ------------------------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Subscribe to the relevant event types on *bus*."""
        self._unsubs.append(bus.subscribe("tool_call", self._on_tool_call))
        self._unsubs.append(bus.subscribe("tool_result", self._on_tool_result))
        self._unsubs.append(bus.subscribe("permission", self._on_permission))
        self._unsubs.append(bus.subscribe("model_request", self._on_model_request))
        self._unsubs.append(bus.subscribe("model_response", self._on_model_response))
        self._unsubs.append(bus.subscribe("compaction", self._on_compaction))
        self._unsubs.append(bus.subscribe("error", self._on_error))
        self._unsubs.append(bus.subscribe("session", self._on_session))
        self._unsubs.append(bus.subscribe("agent_end", self._on_agent_end))

    def detach(self) -> None:
        """Unsubscribe all listeners.  Safe to call multiple times."""
        for u in self._unsubs:
            u()
        self._unsubs.clear()

    # ------------------------------------------------------------------
    # Manual hooks (for events that don't flow through EventBus)
    # ------------------------------------------------------------------

    def record_session_created(
        self, agent_name: str = "", model: str = "", **metadata: object,
    ) -> None:
        self._store.append(
            self._aggregate_id,
            SessionCreatedEvent(
                session_id=self._aggregate_id,
                agent_name=agent_name,
                model=model,
                metadata={k: v for k, v in metadata.items()},
            ),
        )

    def record_session_ended(
        self,
        success: bool = True,
        error: str | None = None,
        total_steps: int = 0,
        total_cost: float = 0.0,
    ) -> None:
        self._store.append(
            self._aggregate_id,
            SessionEndedEvent(
                session_id=self._aggregate_id,
                success=success,
                error=error,
                total_steps=total_steps,
                total_cost=total_cost,
            ),
        )

    def record_user_message(self, content: str) -> None:
        self._store.append(
            self._aggregate_id,
            UserMessageEvent(session_id=self._aggregate_id, content=content),
        )

    def record_agent_result(
        self,
        output: str,
        steps: int = 0,
        tool_calls_total: int = 0,
        cost: float = 0.0,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        self._store.append(
            self._aggregate_id,
            AgentResultEvent(
                session_id=self._aggregate_id,
                output=output,
                steps=steps,
                tool_calls_total=tool_calls_total,
                cost=cost,
                success=success,
                error=error,
            ),
        )

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    def _on_tool_call(self, evt: Event) -> None:
        tool_name = getattr(evt, "tool_name", "")
        arguments = dict(getattr(evt, "arguments", {}) or {})
        call_id = getattr(evt, "call_id", "")
        self._pending_calls[call_id] = {"tool_name": tool_name, "arguments": arguments}
        self._store.append(
            self._aggregate_id,
            ToolCalledEvent(
                session_id=self._aggregate_id,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            ),
        )

    def _on_tool_result(self, evt: Event) -> None:
        call_id = getattr(evt, "call_id", "")
        pending = self._pending_calls.pop(call_id, {})
        tool_name = str(pending.get("tool_name", ""))
        arguments = pending.get("arguments", {}) or {}
        success = bool(getattr(evt, "success", True))
        output = str(getattr(evt, "output", ""))
        self._store.append(
            self._aggregate_id,
            ToolCompletedEvent(
                session_id=self._aggregate_id,
                call_id=call_id,
                tool_name=tool_name,
                success=success,
                output=output,
                error=None if success else output,
            ),
        )
        # Synthesize file.mutated if this was a file-modifying tool that succeeded.
        if (
            success
            and tool_name in _FILE_MODIFYING_TOOLS
            and isinstance(arguments, dict)
            and arguments.get("path")
        ):
            path = str(arguments["path"])
            operation = "modified"
            if tool_name == "write_file":
                operation = "created" if not os.path.lexists(path) else "modified"
            self._store.append(
                self._aggregate_id,
                FileMutatedEvent(
                    session_id=self._aggregate_id,
                    call_id=call_id,
                    path=path,
                    operation=operation,
                ),
            )

    def _on_permission(self, evt: Event) -> None:
        self._store.append(
            self._aggregate_id,
            PermissionDecidedEvent(
                session_id=self._aggregate_id,
                call_id=getattr(evt, "call_id", ""),
                tool_name=getattr(evt, "tool_name", ""),
                action=getattr(evt, "action", ""),
                granted=bool(getattr(evt, "granted", False)),
            ),
        )

    def _on_model_request(self, evt: Event) -> None:
        self._store.append(
            self._aggregate_id,
            ModelRequestedEvent(
                session_id=self._aggregate_id,
                model=getattr(evt, "model", ""),
                message_count=int(getattr(evt, "message_count", 0)),
                tool_count=int(getattr(evt, "tool_count", 0)),
            ),
        )

    def _on_model_response(self, evt: Event) -> None:
        self._store.append(
            self._aggregate_id,
            ModelRespondedEvent(
                session_id=self._aggregate_id,
                model=getattr(evt, "model", ""),
                content_length=int(getattr(evt, "content_length", 0)),
                tool_calls_count=int(getattr(evt, "tool_calls_count", 0)),
                input_tokens=int(getattr(evt, "input_tokens", 0)),
                output_tokens=int(getattr(evt, "output_tokens", 0)),
            ),
        )

    def _on_compaction(self, evt: Event) -> None:
        self._store.append(
            self._aggregate_id,
            CompactionPerformedEvent(
                session_id=self._aggregate_id,
                messages_before=int(getattr(evt, "messages_before", 0)),
                messages_after=int(getattr(evt, "messages_after", 0)),
                strategy=str(getattr(evt, "strategy", "")),
            ),
        )

    def _on_error(self, evt: Event) -> None:
        self._store.append(
            self._aggregate_id,
            ErrorOccurredEvent(
                session_id=self._aggregate_id,
                error=str(getattr(evt, "error", "")),
                recoverable=bool(getattr(evt, "recoverable", True)),
                where=str(getattr(evt, "where", "")),
            ),
        )

    def _on_session(self, evt: Event) -> None:
        action = str(getattr(evt, "action", ""))
        if action == "created":
            self.record_session_created()
        elif action in {"ended", "closed"}:
            self.record_session_ended()
        # Other actions (resumed, etc.) are not in the spec's 12 types yet.

    def _on_agent_end(self, evt: Event) -> None:
        # Treat agent_end as a session-scoped result marker. Callers who
        # want a richer SessionEndedEvent should use record_session_ended.
        self._store.append(
            self._aggregate_id,
            AgentResultEvent(
                session_id=self._aggregate_id,
                output="",
                steps=int(getattr(evt, "steps", 0)),
                tool_calls_total=0,
                cost=float(getattr(evt, "total_cost", 0.0)),
                success=bool(getattr(evt, "success", True)),
                error=None,
            ),
        )
