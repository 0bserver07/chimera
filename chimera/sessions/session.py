from __future__ import annotations

import copy
import uuid
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.sessions.base import SessionData, SessionID, Storage
from chimera.sessions.storage.memory import InMemoryStorage
from collections.abc import Generator

from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.compaction.base import CompactionStrategy
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.sessions.tree import SessionTree

__all__ = ["Session"]


class Session:
    """Multi-turn conversation wrapper around an :class:`Agent`.

    A *Session* owns a :class:`Context` and a :class:`Storage` backend.
    Each call to :meth:`chat` appends the user message to the running
    context, delegates to the agent's loop, and (optionally) persists the
    result.

    Parameters
    ----------
    agent:
        The agent that powers this session.
    env:
        Optional execution environment forwarded to the agent loop.
    storage:
        Persistence backend.  Defaults to :class:`InMemoryStorage`.
    session_id:
        Explicit session identifier.  A random UUID is generated when
        ``None``.
    auto_compact:
        When ``True``, apply *compaction* after every ``chat`` turn.
    compaction:
        Strategy used to compact the context when *auto_compact* is
        enabled.
    """

    def __init__(
        self,
        agent: Agent,
        env: Environment | None = None,
        storage: Storage | None = None,
        session_id: SessionID | None = None,
        auto_compact: bool = False,
        compaction: CompactionStrategy | None = None,
        tree: SessionTree | None = None,
    ) -> None:
        self._agent = agent
        self._env = env
        self._storage: Storage = storage or InMemoryStorage()
        self._session_id: SessionID = session_id or str(uuid.uuid4())
        self._auto_compact = auto_compact
        self._compaction = compaction
        self._parent_id: SessionID | None = None
        self._tree = tree

        # Build initial context from the agent's prompt.
        system = agent.prompt.render(tools=[t.name for t in agent.tools])
        self._context = Context(system=system)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, message: str) -> AgentResult:
        """Send a user message and run the agent loop.

        The message is appended to the existing context so that the
        agent sees the full multi-turn history.
        """
        self._context.add(Message.user(message))
        if self._tree:
            self._tree.add_message(Message.user(message))
        result = self._agent.loop.run(
            self._agent.provider, self._agent.tools, self._context, self._env,
        )
        if self._tree:
            self._tree.add_message(Message.assistant(result.output))
        return result

    def iter_chat(self, message: str) -> Generator[StepResult, None, AgentResult]:
        """Send a user message and iterate through agent steps.

        Like :meth:`chat` but yields :class:`StepResult` per LLM turn,
        allowing the caller to inspect intermediate state and handle
        :attr:`~StepResult.pending_approval` interactively.
        """
        self._context.add(Message.user(message))
        if self._tree:
            self._tree.add_message(Message.user(message))
        return (
            yield from self._agent.loop.iter_steps(
                self._agent.provider,
                self._agent.tools,
                self._context,
                self._env,
            )
        )

    def fork(self) -> Session:
        """Create an independent branch from the current session state.

        The forked session receives a deep copy of the context, a new
        session ID, and records this session as its parent.
        """
        forked = Session(
            agent=self._agent,
            env=self._env,
            storage=self._storage,
            session_id=str(uuid.uuid4()),
            auto_compact=self._auto_compact,
            compaction=self._compaction,
        )
        forked._context = Context(system=self._context.system)
        for msg in copy.deepcopy(self._context.messages):
            forked._context.add(msg)
        forked._parent_id = self._session_id
        return forked

    def switch_branch(self, leaf_id: str) -> None:
        """Switch to a different branch and rebuild context from tree.

        Args:
            leaf_id: The entry ID of the leaf to switch to.

        Raises:
            ValueError: If *leaf_id* is not found in the tree.
        """
        if self._tree is None:
            return
        self._tree.switch_branch(leaf_id)
        messages = self._tree.get_messages(leaf_id)
        self._context = Context(system=self._context.system)
        for msg in messages:
            self._context.add(msg)

    def save(self) -> None:
        """Persist the current session state to storage."""
        import time

        data = SessionData(
            session_id=self._session_id,
            messages=list(self._context.messages),
            system=self._context.system,
            parent_id=self._parent_id,
            updated_at=time.time(),
        )
        self._storage.save(self._session_id, data)

    @classmethod
    def resume(
        cls,
        session_id: SessionID,
        agent: Agent,
        storage: Storage,
        **kwargs: object,
    ) -> Session:
        """Resume a previously saved session.

        Raises :class:`ValueError` if the session is not found in
        *storage*.
        """
        data = storage.load(session_id)
        if data is None:
            raise ValueError(f"Session {session_id} not found")

        session = cls(
            agent=agent,
            storage=storage,
            session_id=session_id,
            **kwargs,  # type: ignore[arg-type]
        )
        # Overwrite the freshly built context with saved state.
        session._context = Context(system=data.system)
        for msg in data.messages:
            session._context.add(msg)
        session._parent_id = data.parent_id
        return session

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> SessionID:
        """The unique identifier of this session."""
        return self._session_id

    @property
    def messages(self) -> list[Message]:
        """The current conversation messages (excludes system)."""
        return self._context.messages

    @property
    def context(self) -> Context:
        """Direct access to the underlying :class:`Context`."""
        return self._context
