from __future__ import annotations

import copy

from chimera.sessions.base import SessionData, SessionID, Storage

__all__ = ["InMemoryStorage"]


class InMemoryStorage(Storage):
    """Dictionary-backed storage with no persistence.

    Useful for tests and single-process sessions that do not need to survive
    a restart.
    """

    def __init__(self) -> None:
        self._store: dict[SessionID, SessionData] = {}

    def save(self, session_id: SessionID, data: SessionData) -> None:
        self._store[session_id] = copy.deepcopy(data)

    def load(self, session_id: SessionID) -> SessionData | None:
        data = self._store.get(session_id)
        if data is None:
            return None
        return copy.deepcopy(data)

    def list_sessions(self) -> list[SessionID]:
        return list(self._store.keys())

    def delete(self, session_id: SessionID) -> None:
        self._store.pop(session_id, None)
