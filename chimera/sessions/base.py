from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from chimera.types import Message

__all__ = ["SessionID", "SessionData", "Storage"]

SessionID = str  # UUID-based


@dataclass
class SessionData:
    """Serialisable snapshot of a single session's state."""

    session_id: SessionID
    messages: list[Message]
    system: str | None = None
    parent_id: SessionID | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class Storage(ABC):
    """Abstract persistence backend for session data."""

    @abstractmethod
    def save(self, session_id: SessionID, data: SessionData) -> None:
        """Persist *data* under *session_id*."""

    @abstractmethod
    def load(self, session_id: SessionID) -> SessionData | None:
        """Load a previously saved session, or ``None`` if not found."""

    @abstractmethod
    def list_sessions(self) -> list[SessionID]:
        """Return all stored session IDs."""

    @abstractmethod
    def delete(self, session_id: SessionID) -> None:
        """Remove a session from storage.  No-op if it does not exist."""
