"""Event-sourced session that records all interactions to an EventLog."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.events.base import Event
from chimera.sessions.base import SessionID, Storage
from chimera.sessions.eventlog.log import EventLog
from chimera.sessions.session import Session
from chimera.sessions.storage.memory import InMemoryStorage
from chimera.types import AgentResult, Message

if TYPE_CHECKING:
    from chimera.compaction.base import CompactionStrategy
    from chimera.core.agent import Agent
    from chimera.env.base import Environment

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
        agent: Agent,
        storage: Storage | None = None,
        **kwargs: object,
    ) -> EventSourcedSession:
        """Replay all events from disk to reconstruct session state.

        Args:
            log_dir: Root directory containing event logs.
            session_id: The session to resume.
            agent: Agent instance for the session.
            storage: Optional storage backend.
            **kwargs: Additional keyword arguments forwarded to the
                constructor.

        Returns:
            A fully reconstructed :class:`EventSourcedSession`.

        Raises:
            ValueError: If the event log directory does not exist.
        """
        log_path = Path(log_dir) / session_id
        if not log_path.exists():
            raise ValueError(f"No event log found for session {session_id}")

        session = cls(
            agent=agent,
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
        agent: Agent,
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
        log_path = Path(log_dir) / session_id
        if not log_path.exists():
            raise ValueError(f"No event log found for session {session_id}")

        session = cls(
            agent=agent,
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

        Only ``user_message`` and ``agent_result`` event types are
        replayed.  Other event types are silently skipped.

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
