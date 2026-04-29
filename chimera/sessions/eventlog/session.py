"""Event-sourced session that records all interactions to a durable journal.

Two journal backends are supported:

* :class:`~chimera.sessions.eventlog.log.EventLog` (default, used when
  ``log_dir=`` is supplied) — JSONL files on disk, one per event.  This
  is the original M0 implementation and remains the default for backwards
  compatibility.
* :class:`~chimera.events.sourcing.sqlite_store.SqliteEventStore` — a
  single SQLite file with monotonic per-aggregate sequences (M-L4).
  When this backend is in use AND a snapshot exists for the session,
  replay fast-resumes from ``snapshot.seq + 1`` instead of seq=1.

Pick a backend by passing exactly one of ``log_dir=`` or ``store=`` to
``__init__``; the :meth:`with_sqlite` classmethod is the recommended
convenience for the SQLite path.

A separate ``event_store=`` keyword (distinct from ``store=``) hooks an
*auxiliary* snapshot-capable store onto the JSONL backend.  When set
together with ``snapshot_every_n_events``, the session triggers a
snapshot on the auxiliary store every N events and uses it on resume to
fast-forward replay.  This is the M-17 "side-car snapshot" mode and is
preserved unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.events.base import Event, EventBus
from chimera.events.sourcing.sqlite_store import SqliteEventStore
from chimera.events.sourcing.types import AgentResultEvent, UserMessageEvent
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


def _store_supports_snapshots(store: Any) -> bool:
    """Late-bound capability check.

    L4 may wire a SqliteEventStore (or any future store) onto the
    session.  Rather than hard-import the SQLite store class, we
    duck-type the two methods we need: ``latest_snapshot`` and
    ``snapshot``.  Returns ``False`` for ``None``.
    """
    if store is None:
        return False
    return callable(getattr(store, "latest_snapshot", None)) and callable(
        getattr(store, "snapshot", None),
    )


def _serialize_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Encode a list of Messages as JSON-friendly dicts.

    Snapshots only need to round-trip role + content for resume
    (tool_calls / content_blocks are reconstructed from later events
    if needed).  Keeping the snapshot payload minimal avoids
    coupling to provider-specific shapes that may evolve.
    """
    return [{"role": m.role, "content": m.content} for m in messages]


def _deserialize_messages(rows: list[dict[str, Any]]) -> list[Message]:
    """Inverse of :func:`_serialize_messages`."""
    return [Message(role=r["role"], content=r.get("content", "")) for r in rows]


class EventSourcedSession(Session):
    """A :class:`Session` that journals every interaction to a durable log.

    On top of the normal session behaviour, every user message and agent
    result is recorded — either as an :class:`Event` in an append-only
    JSONL log (default) or as a typed payload in a
    :class:`SqliteEventStore`.  This enables full replay and partial
    recovery via :meth:`resume` and :meth:`resume_from`.

    Args:
        agent: The agent that powers this session.
        log_dir: Root directory for the JSONL event log.  A subdirectory
            named after the session ID is created automatically.
            Mutually exclusive with ``store``.
        env: Optional execution environment forwarded to the agent loop.
        storage: Persistence backend.  Defaults to
            :class:`InMemoryStorage`.
        session_id: Explicit session identifier.  A random UUID is
            generated when ``None``.
        auto_compact: When ``True``, apply compaction after every turn.
        compaction: Strategy used to compact the context.
        event_store: Optional auxiliary snapshot store (M-17 side-car
            mode).  See module docstring.
        snapshot_every_n_events: Trigger a snapshot every N recorded
            events.  Targets the SQLite primary journal when in use,
            otherwise the auxiliary ``event_store``.
        store: Pre-built primary journal backend.  Pass an
            :class:`EventLog` to use a custom JSONL location, or a
            :class:`SqliteEventStore` to use the SQLite backend as the
            primary journal (M-L4).  Mutually exclusive with ``log_dir``.

    Raises:
        ValueError: When neither ``log_dir`` nor ``store`` is supplied,
            or both are supplied.
    """

    def __init__(
        self,
        agent: Agent,
        log_dir: str | Path | None = None,
        env: Environment | None = None,
        storage: Storage | None = None,
        session_id: SessionID | None = None,
        auto_compact: bool = False,
        compaction: CompactionStrategy | None = None,
        event_store: Any | None = None,
        snapshot_every_n_events: int | None = None,
        *,
        store: EventLog | SqliteEventStore | None = None,
    ) -> None:
        if log_dir is None and store is None:
            raise ValueError(
                "EventSourcedSession requires either `log_dir` or `store`",
            )
        if log_dir is not None and store is not None:
            raise ValueError(
                "Pass either `log_dir` or `store`, not both",
            )

        sid = session_id or str(uuid.uuid4())
        super().__init__(
            agent=agent,
            env=env,
            storage=storage,
            session_id=sid,
            auto_compact=auto_compact,
            compaction=compaction,
        )

        # Initialise the primary journal — either an EventLog (JSONL) or
        # a SqliteEventStore.  Exactly one is non-None at any time.
        self._log_dir: Path | None
        self._event_log: EventLog | None
        self._sqlite_store: SqliteEventStore | None

        if isinstance(store, SqliteEventStore):
            self._log_dir = None
            self._event_log = None
            self._sqlite_store = store
        elif isinstance(store, EventLog):
            self._log_dir = None
            self._event_log = store
            self._sqlite_store = None
        else:
            assert log_dir is not None  # narrowed above
            self._log_dir = Path(log_dir)
            self._event_log = EventLog(self._log_dir / self._session_id)
            self._sqlite_store = None

        # Auxiliary snapshot store (M-17 side-car).  Distinct from the
        # primary journal.  Late-bound: any object exposing the snapshot
        # API (``latest_snapshot`` / ``snapshot``) works.
        self._event_store: Any | None = event_store
        self._snapshot_every_n_events: int | None = snapshot_every_n_events
        # Track total events ever recorded so we can fire a snapshot
        # every N appends.  Reset only on instance construction; resume
        # rebuilds from disk so the counter starts fresh there too.
        self._events_recorded: int = 0
        # Wire any TodoTool the agent owns to a bus that forwards
        # TodoWriteEvent into our durable log. Keeping the bus
        # session-local avoids coupling to LoopConfig.event_bus, which may
        # not be configured.
        self._todo_bus: EventBus = EventBus()
        self._todo_bus.subscribe("todo_write", self._on_todo_write)
        self._todo_tools: list[TodoTool] = self._discover_and_attach_todo_tools()

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def with_sqlite(
        cls,
        path: str | Path,
        *,
        agent: Agent,
        env: Environment | None = None,
        storage: Storage | None = None,
        session_id: SessionID | None = None,
        auto_compact: bool = False,
        compaction: CompactionStrategy | None = None,
        snapshot_every_n_events: int | None = None,
    ) -> EventSourcedSession:
        """Build a session backed by a :class:`SqliteEventStore` at *path*.

        The store is owned by the returned session — typed events are
        appended to it as the session runs, and :meth:`snapshot_now`
        / :meth:`resume` use it directly.

        Args:
            path: Filesystem path for the SQLite database.  Created if
                missing.  Use ``":memory:"`` for unit tests.
            agent: The agent that powers this session.
            env: Optional execution environment.
            storage: Persistence backend.  Defaults to
                :class:`InMemoryStorage`.
            session_id: Explicit session identifier.  A random UUID is
                generated when ``None``.
            auto_compact: When ``True``, apply compaction after every
                turn.
            compaction: Strategy used to compact the context.
            snapshot_every_n_events: Trigger an auto-snapshot every N
                events.  ``None`` disables auto-snapshot.

        Returns:
            A new :class:`EventSourcedSession` writing to a SQLite store.
        """
        sqlite_store = SqliteEventStore(path)
        return cls(
            agent=agent,
            store=sqlite_store,
            env=env,
            storage=storage,
            session_id=session_id,
            auto_compact=auto_compact,
            compaction=compaction,
            snapshot_every_n_events=snapshot_every_n_events,
        )

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def chat(self, message: str) -> AgentResult:
        """Send a user message, record events, and run the agent loop."""
        self._record_user_message(message)
        self._after_event_recorded()
        result = super().chat(message)
        self._record_agent_result(result)
        self._after_event_recorded()
        return result

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _after_event_recorded(self) -> None:
        """Bump the event counter and fire an auto-snapshot if due.

        Auto-snapshots fire when ``snapshot_every_n_events`` is set AND
        a snapshot-capable store is reachable.  The threshold check uses
        modular arithmetic so a snapshot is taken every N events
        (10, 20, 30, ...) starting from the first boundary.

        Priority order for the snapshot target:
          1. The SQLite primary journal, when in use.
          2. The auxiliary ``event_store`` side-car (M-17 mode).
        """
        self._events_recorded += 1
        n = self._snapshot_every_n_events
        if n is None or n <= 0:
            return
        if self._events_recorded % n != 0:
            return
        if self._sqlite_store is not None:
            self._take_primary_snapshot()
        elif _store_supports_snapshots(self._event_store):
            self._take_auxiliary_snapshot()

    def _take_auxiliary_snapshot(self) -> None:
        """Capture state on the side-car snapshot store (M-17 mode).

        Snapshot state is derived by replaying the JSONL event log onto
        a fresh message list (rather than copying the live ``_context``),
        because :meth:`Session.chat` only appends user messages to
        context — assistant outputs come from the loop and are not
        echoed into context outside the resume path.  Running the same
        replay logic here keeps the snapshot ↔ replay round-trip
        symmetric.
        """
        store = self._event_store
        if not _store_supports_snapshots(store):
            return
        assert store is not None  # narrowed by _store_supports_snapshots
        assert self._event_log is not None  # auxiliary mode pairs with JSONL
        events = self._event_log.get_range()
        msgs: list[Message] = []
        for event in events:
            if event.type == "user_message":
                msgs.append(Message.user(event.metadata.get("content", "")))
            elif event.type == "agent_result":
                msgs.append(Message.assistant(event.metadata.get("output", "")))
        state = {
            "messages": _serialize_messages(msgs),
            "system": self._context.system,
            "events_recorded": self._events_recorded,
        }
        store.snapshot(self._session_id, self._events_recorded, state)

    def _take_primary_snapshot(self) -> None:
        """Capture state directly into the primary SQLite journal.

        Uses the journal's current ``last_seq`` as the snapshot point —
        which is what :meth:`SqliteEventStore.replay` will resume from
        when we call ``read_since(seq)`` later.
        """
        assert self._sqlite_store is not None
        seq = self._sqlite_store.last_seq(self._session_id)
        # Re-derive messages from the journal so the snapshot mirrors
        # what a clean replay would build.
        msgs: list[Message] = []
        for stored in self._sqlite_store.read_since(self._session_id, from_seq=0):
            payload = stored.payload
            if isinstance(payload, UserMessageEvent):
                msgs.append(Message.user(payload.content))
            elif isinstance(payload, AgentResultEvent):
                msgs.append(Message.assistant(payload.output))
        state = {
            "messages": _serialize_messages(msgs),
            "system": self._context.system,
            "events_recorded": self._events_recorded,
        }
        self._sqlite_store.snapshot(self._session_id, seq=seq, state=state)

    def _take_snapshot(self) -> None:
        """Back-compat shim for the original side-car snapshot path.

        Older tests (and external callers) invoked ``_take_snapshot``
        directly; route them through the priority resolver so behaviour
        mirrors :meth:`_after_event_recorded`.
        """
        if self._sqlite_store is not None:
            self._take_primary_snapshot()
        else:
            self._take_auxiliary_snapshot()

    def snapshot_now(self) -> bool:
        """Force a snapshot at the current state.

        Returns:
            ``True`` if a snapshot was written, ``False`` when no
            compatible event store is configured.
        """
        if self._sqlite_store is not None:
            self._take_primary_snapshot()
            return True
        if _store_supports_snapshots(self._event_store):
            self._take_auxiliary_snapshot()
            return True
        return False

    # ------------------------------------------------------------------
    # Replay / recovery
    # ------------------------------------------------------------------

    @classmethod
    def resume(  # type: ignore[override]
        cls,
        log_dir: str | Path | None = None,
        session_id: SessionID = "",
        agent: SessionResumeAgent | None = None,
        storage: Storage | None = None,
        *,
        store: EventLog | SqliteEventStore | None = None,
        **kwargs: object,
    ) -> EventSourcedSession:
        """Replay all events to reconstruct session state.

        Behaviour by backend:

        * **JSONL (default).** The on-disk EventLog is replayed in full.
          When the caller passes ``event_store=`` (an auxiliary store
          exposing the snapshot API), the newest snapshot — if any — is
          applied first and only events recorded after that snapshot's
          seq are replayed.
        * **SQLite (``store=SqliteEventStore``).** When a snapshot exists
          for *session_id*, replay fast-resumes from ``snapshot.seq + 1``
          and the snapshot's serialized state seeds the context.
          Otherwise the full event stream is replayed.

        Args:
            log_dir: Root directory containing JSONL event logs.
                Mutually exclusive with ``store``.
            session_id: The session to resume.  Required.
            agent: Anything that satisfies :class:`SessionResumeAgent` —
                a real :class:`Agent` or a lightweight shim. Resume only
                touches ``agent.prompt`` / ``agent.tools`` to seed Context.
            storage: Optional storage backend.
            store: A pre-built :class:`EventLog` or
                :class:`SqliteEventStore`.  Mutually exclusive with
                ``log_dir``.
            **kwargs: Additional keyword arguments forwarded to the
                constructor (notably ``event_store=`` and
                ``snapshot_every_n_events=``).

        Returns:
            A fully reconstructed :class:`EventSourcedSession`.

        Raises:
            ValueError: If neither ``log_dir`` nor ``store`` is supplied,
                or both are, or *session_id* is empty / *agent* is
                missing, or the JSONL log directory is missing.
        """
        # WHY (audit M-17): mirror Session.resume's Protocol acceptance so
        # CLI front-ends can stage history with a one-class shim instead of
        # the previous four-class stub-cast pattern.
        from typing import cast as _cast

        if not session_id:
            raise ValueError("resume() requires a non-empty session_id")
        if agent is None:
            raise ValueError("resume() requires an agent")
        if log_dir is None and store is None:
            raise ValueError(
                "resume() requires either `log_dir` or `store`",
            )
        if log_dir is not None and store is not None:
            raise ValueError(
                "Pass either `log_dir` or `store`, not both",
            )

        # SQLite primary journal path.
        if isinstance(store, SqliteEventStore):
            session = cls(
                agent=_cast("Agent", agent),
                store=store,
                storage=storage,
                session_id=session_id,
                **kwargs,  # type: ignore[arg-type]
            )
            session._replay_sqlite_with_snapshot()
            return session

        # JSONL path — either via `log_dir=` or via an explicit EventLog
        # passed as `store=`.
        if isinstance(store, EventLog):
            session = cls(
                agent=_cast("Agent", agent),
                store=store,
                storage=storage,
                session_id=session_id,
                **kwargs,  # type: ignore[arg-type]
            )
        else:
            assert log_dir is not None
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

        assert session._event_log is not None
        events = session._event_log.get_range()

        # Fast-resume path: when an event_store with snapshot support
        # was passed via kwargs, consult it for the newest snapshot and
        # skip ahead.
        snap_seq = session._maybe_apply_snapshot()
        if snap_seq is not None:
            tail = events[snap_seq:]
            session._replay_events(tail)
            session._events_recorded = len(events)
            return session

        # Default: full replay (preserves M-17 behaviour).
        session._replay_events(events)
        session._events_recorded = len(events)
        return session

    def _maybe_apply_snapshot(self) -> int | None:
        """Hydrate context from the auxiliary store's newest snapshot.

        Returns the snapshot's ``seq`` (so resume can slice the JSONL
        event list) or ``None`` when fast-resume is not possible.

        ``None`` is returned when:
            * no ``event_store`` was passed to ``__init__``;
            * the store doesn't implement the snapshot API; or
            * the store has no snapshot for this session_id.
        """
        store = self._event_store
        if not _store_supports_snapshots(store):
            return None
        assert store is not None  # narrowed by _store_supports_snapshots
        snap = store.latest_snapshot(self._session_id)
        if snap is None:
            return None
        seq, state = snap
        if not isinstance(state, dict):
            return None
        msgs_raw = state.get("messages")
        if not isinstance(msgs_raw, list):
            return None
        # Hydrate context from the snapshot.
        from chimera.core.context import Context

        system = state.get("system", self._context.system)
        self._context = Context(system=system)
        for msg in _deserialize_messages(msgs_raw):
            self._context.add(msg)
        return int(seq)

    def _replay_sqlite_with_snapshot(self) -> None:
        """Rebuild context from the SQLite journal, fast-forwarding via snapshot.

        When :meth:`SqliteEventStore.latest_snapshot` returns a stored
        state, that snapshot's ``messages`` (written by the auto- or
        manual-snapshot path) seed the context, and replay continues
        from ``snap.seq + 1``.  Otherwise the full event stream is
        replayed from seq=0.
        """
        assert self._sqlite_store is not None
        from chimera.core.context import Context

        from_seq = 0
        snap = self._sqlite_store.latest_snapshot(self._session_id)
        if snap is not None:
            seq, state = snap
            from_seq = seq
            if isinstance(state, dict):
                msgs_raw = state.get("messages")
                if isinstance(msgs_raw, list):
                    system = state.get("system", self._context.system)
                    self._context = Context(system=system)
                    for msg in _deserialize_messages(msgs_raw):
                        self._context.add(msg)
                    self._events_recorded = int(state.get("events_recorded", seq))

        for stored in self._sqlite_store.read_since(self._session_id, from_seq=from_seq):
            payload = stored.payload
            if isinstance(payload, UserMessageEvent):
                self._context.add(Message.user(payload.content))
            elif isinstance(payload, AgentResultEvent):
                self._context.add(Message.assistant(payload.output))
            self._events_recorded = stored.seq

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
        """Partial recovery: replay JSONL events up to *up_to_index* (inclusive).

        Only the JSONL backend supports partial recovery by index — the
        SQLite backend exposes its own ``replay(since_seq=...)`` API on
        the store directly.

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
        assert session._event_log is not None
        events = session._event_log.get_range(start=0, end=up_to_index + 1)
        session._replay_events(events)
        return session

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def event_count(self) -> int:
        """Total number of events recorded for this session."""
        if self._event_log is not None:
            return self._event_log.length
        assert self._sqlite_store is not None
        return self._sqlite_store.last_seq(self._session_id)

    @property
    def event_log(self) -> EventLog:
        """Direct access to the underlying JSONL :class:`EventLog`.

        Raises:
            AttributeError: When the session is using the SQLite
                backend.  Use :attr:`sqlite_store` instead.
        """
        if self._event_log is None:
            raise AttributeError(
                "event_log is unavailable on SQLite-backed sessions; "
                "use `sqlite_store` instead",
            )
        return self._event_log

    @property
    def sqlite_store(self) -> SqliteEventStore:
        """Direct access to the underlying :class:`SqliteEventStore`.

        Raises:
            AttributeError: When the session is using the JSONL backend.
        """
        if self._sqlite_store is None:
            raise AttributeError(
                "sqlite_store is unavailable on JSONL-backed sessions; "
                "use `event_log` instead",
            )
        return self._sqlite_store

    @property
    def uses_sqlite(self) -> bool:
        """``True`` when the session's primary journal is a SQLite store."""
        return self._sqlite_store is not None

    # ------------------------------------------------------------------
    # Internal: write helpers
    # ------------------------------------------------------------------

    def _record_user_message(self, message: str) -> None:
        """Append a user_message event to whichever backend is active."""
        if self._event_log is not None:
            self._event_log.append(
                Event(
                    type="user_message",
                    metadata={"content": message},
                ),
            )
            return
        assert self._sqlite_store is not None
        self._sqlite_store.append(
            self._session_id,
            UserMessageEvent(session_id=self._session_id, content=message),
        )

    def _record_agent_result(self, result: AgentResult) -> None:
        """Append an agent_result event to whichever backend is active."""
        if self._event_log is not None:
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
                ),
            )
            return
        assert self._sqlite_store is not None
        self._sqlite_store.append(
            self._session_id,
            AgentResultEvent(
                session_id=self._session_id,
                output=result.output,
                steps=result.steps,
                tool_calls_total=result.tool_calls_total,
                cost=result.cost,
                success=result.success,
                error=result.error,
            ),
        )

    # ------------------------------------------------------------------
    # Internal: replay helpers
    # ------------------------------------------------------------------

    def _replay_events(self, events: list[Event]) -> None:
        """Replay a sequence of JSONL events into the session context.

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
        """Forward an in-process ``TodoWriteEvent`` into the active backend.

        Subscribed once during __init__; called synchronously by the
        TodoTool whenever its state mutates.  For the JSONL backend we
        re-publish as a base :class:`Event` so the on-disk JSON form is
        forward-compatible with future replay code that has no dataclass
        to deserialize into.  For the SQLite backend we currently skip
        these events — the typed registry has no ``todo_write`` payload
        and TodoTool persistence already mirrors to its own file.

        Args:
            event: The ``TodoWriteEvent`` raised by a TodoTool.
        """
        if self._event_log is None:
            return
        # Normalize to a base Event so EventLog.serialize/deserialize
        # round-trips cleanly without a custom registry.
        metadata = dict(event.metadata)
        metadata.setdefault("todos", getattr(event, "todos", []))
        metadata.setdefault("op", getattr(event, "op", "set"))
        metadata.setdefault("session_id", getattr(event, "session_id", self._session_id))
        self._event_log.append(Event(type="todo_write", metadata=metadata))
