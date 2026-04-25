"""Event-sourced session that records all interactions to an EventLog."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.events.base import Event, EventBus
from chimera.sessions.base import SessionID, Storage
from chimera.sessions.eventlog.log import EventLog
from chimera.sessions.session import Session, SessionResumeAgent
from chimera.types import AgentResult, Message

if TYPE_CHECKING:
    from chimera.compaction.base import CompactionStrategy
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.tools.todo import TodoTool

__all__ = ["EventSourcedSession"]


class EventSourcedSession(Session):
    """A :class:`Session` that journals every interaction to an :class:`EventLog`.

    On top of the normal session behaviour, every user message and agent
    result is recorded as an :class:`Event` in an append-only log stored
    under ``<log_dir>/<session_id>/``.  This enables full replay and
    partial recovery via :meth:`resume` and :meth:`resume_from`.

    Args:
        agent: The agent that powers this session.
        log_dir: Root directory for event logs.  A subdirectory named
            after the session ID is created automatically.
        env: Optional execution environment forwarded to the agent loop.
        storage: Persistence backend.  Defaults to
            :class:`InMemoryStorage`.
        session_id: Explicit session identifier.  A random UUID is
            generated when ``None``.
        auto_compact: When ``True``, apply compaction after every turn.
        compaction: Strategy used to compact the context.
    """

    def __init__(
        self,
        agent: Agent,
        log_dir: str | Path,
        env: Environment | None = None,
        storage: Storage | None = None,
        session_id: SessionID | None = None,
        auto_compact: bool = False,
        compaction: CompactionStrategy | None = None,
    ) -> None:
        sid = session_id or str(uuid.uuid4())
        super().__init__(
            agent=agent,
            env=env,
            storage=storage,
            session_id=sid,
            auto_compact=auto_compact,
            compaction=compaction,
        )
        self._log_dir = Path(log_dir)
        self._event_log = EventLog(self._log_dir / self._session_id)
        # Wire any TodoTool the agent owns to a bus that forwards
        # TodoWriteEvent into our durable log. Keeping the bus
        # session-local avoids coupling to LoopConfig.event_bus, which may
        # not be configured.
        self._todo_bus: EventBus = EventBus()
        self._todo_bus.subscribe("todo_write", self._on_todo_write)
        self._todo_tools: list[TodoTool] = self._discover_and_attach_todo_tools()

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def chat(self, message: str) -> AgentResult:
        """Send a user message, record events, and run the agent loop."""
        self._event_log.append(
            Event(
                type="user_message",
                metadata={"content": message},
            )
        )
        result = super().chat(message)
        self._event_log.append(
            Event(
                type="agent_result",
                metadata={
                    "output": result.output,
                    "steps": result.steps,
                    "tool_calls_total": result.tool_calls_total,
                    "cost": result.cost,
                    "success": result.success,
                    "error": result.error,
                },
            )
        )
        return result

    # ------------------------------------------------------------------
    # Replay / recovery
    # ------------------------------------------------------------------

    @classmethod
    def resume(  # type: ignore[override]
        cls,
        log_dir: str | Path,
        session_id: SessionID,
        agent: SessionResumeAgent,
        storage: Storage | None = None,
        **kwargs: object,
    ) -> EventSourcedSession:
        """Replay all events from disk to reconstruct session state.

        Args:
            log_dir: Root directory containing event logs.
            session_id: The session to resume.
            agent: Anything that satisfies :class:`SessionResumeAgent` —
                a real :class:`Agent` or a lightweight shim. Resume only
                touches ``agent.prompt`` / ``agent.tools`` to seed Context.
            storage: Optional storage backend.
            **kwargs: Additional keyword arguments forwarded to the
                constructor.

        Returns:
            A fully reconstructed :class:`EventSourcedSession`.

        Raises:
            ValueError: If the event log directory does not exist.
        """
        # WHY (audit M-17): mirror Session.resume's Protocol acceptance so
        # CLI front-ends can stage history with a one-class shim instead of
        # the previous four-class stub-cast pattern.
        from typing import cast as _cast

        log_path = Path(log_dir) / session_id
        if not log_path.exists():
            raise ValueError(f"No event log found for session {session_id}")

        session = cls(
            agent=_cast("Agent", agent),
            log_dir=log_dir,
            storage=storage,
            session_id=session_id,
            **kwargs,  # type: ignore[arg-type]
        )
        # The EventLog is already loaded from disk; replay user messages
        # into the context so the conversation history is rebuilt.
        events = session._event_log.get_range()
        session._replay_events(events)
        return session

    @classmethod
    def resume_from(
        cls,
        log_dir: str | Path,
        session_id: SessionID,
        agent: SessionResumeAgent,
        up_to_index: int,
        storage: Storage | None = None,
        **kwargs: object,
    ) -> EventSourcedSession:
        """Partial recovery: replay events up to *up_to_index* (inclusive).

        Args:
            log_dir: Root directory containing event logs.
            session_id: The session to resume.
            agent: Agent instance for the session.
            up_to_index: Last event index to replay (inclusive).
            storage: Optional storage backend.
            **kwargs: Additional keyword arguments forwarded to the
                constructor.

        Returns:
            A partially reconstructed :class:`EventSourcedSession`.

        Raises:
            ValueError: If the event log directory does not exist.
        """
        # WHY (audit M-17): same Protocol-cast bridge as resume() — accept the
        # narrow SessionResumeAgent in the public surface, cast at the
        # constructor boundary so __init__ keeps its full Agent typing.
        from typing import cast as _cast

        log_path = Path(log_dir) / session_id
        if not log_path.exists():
            raise ValueError(f"No event log found for session {session_id}")

        session = cls(
            agent=_cast("Agent", agent),
            log_dir=log_dir,
            storage=storage,
            session_id=session_id,
            **kwargs,  # type: ignore[arg-type]
        )
        events = session._event_log.get_range(start=0, end=up_to_index + 1)
        session._replay_events(events)
        return session

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def event_count(self) -> int:
        """Total number of events in the log."""
        return self._event_log.length

    @property
    def event_log(self) -> EventLog:
        """Direct access to the underlying :class:`EventLog`."""
        return self._event_log

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _replay_events(self, events: list[Event]) -> None:
        """Replay a sequence of events into the session context.

        ``user_message`` and ``agent_result`` events rebuild the
        conversation history; ``todo_write`` events are reapplied to every
        TodoTool the agent owns so the task list survives /resume,
        /compact, and /undo.  Unknown event types are silently skipped.

        Args:
            events: Ordered list of events to replay.
        """
        for event in events:
            if event.type == "user_message":
                content = event.metadata.get("content", "")
                self._context.add(Message.user(content))
            elif event.type == "agent_result":
                output = event.metadata.get("output", "")
                self._context.add(Message.assistant(output))
            elif event.type == "todo_write":
                for tool in self._todo_tools:
                    tool.apply_event(event)

    # ------------------------------------------------------------------
    # TodoTool wiring
    # ------------------------------------------------------------------

    def _discover_and_attach_todo_tools(self) -> list[TodoTool]:
        """Find every TodoTool the agent owns and wire it to our bus.

        TodoTool instances are looked up by ``name == "todo"`` so this
        works for both subclassed tools and bare instances.  Persistence
        is left untouched — M3-C handles the file mirror.

        Returns:
            The list of attached TodoTool instances (possibly empty).
        """
        from chimera.tools.todo import TodoTool

        attached: list[TodoTool] = []
        for tool in getattr(self._agent, "tools", []) or []:
            if isinstance(tool, TodoTool):
                tool.attach_event_bus(self._todo_bus, session_id=self._session_id)
                attached.append(tool)
        return attached

    def _on_todo_write(self, event: Event) -> None:
        """Forward an in-process ``TodoWriteEvent`` into the EventLog.

        Subscribed once during __init__; called synchronously by the
        TodoTool whenever its state mutates.  We re-publish as a base
        :class:`Event` so the on-disk JSON form is forward-compatible
        with future replay code that has no dataclass to deserialize
        into.

        Args:
            event: The ``TodoWriteEvent`` raised by a TodoTool.
        """
        # Normalize to a base Event so EventLog.serialize/deserialize
        # round-trips cleanly without a custom registry.
        metadata = dict(event.metadata)
        metadata.setdefault("todos", getattr(event, "todos", []))
        metadata.setdefault("op", getattr(event, "op", "set"))
        metadata.setdefault("session_id", getattr(event, "session_id", self._session_id))
        self._event_log.append(Event(type="todo_write", metadata=metadata))
