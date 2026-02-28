from __future__ import annotations

import json
import os
from pathlib import Path

from chimera.sessions.base import SessionData, SessionID, Storage
from chimera.types import Message, ToolCall

__all__ = ["FileStorage"]


class FileStorage(Storage):
    """One-JSON-file-per-session storage backed by the local filesystem.

    Each session is written as ``<session_id>.json`` under the configured
    *directory*.
    """

    def __init__(self, directory: str = "~/.chimera/sessions/") -> None:
        self._directory = Path(os.path.expanduser(directory))

    # ------------------------------------------------------------------
    # Storage interface
    # ------------------------------------------------------------------

    def save(self, session_id: SessionID, data: SessionData) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(session_id)
        payload = self._serialise(data)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self, session_id: SessionID) -> SessionData | None:
        path = self._path_for(session_id)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return self._deserialise(raw)

    def list_sessions(self) -> list[SessionID]:
        if not self._directory.exists():
            return []
        return [
            p.stem for p in sorted(self._directory.glob("*.json"))
        ]

    def delete(self, session_id: SessionID) -> None:
        path = self._path_for(session_id)
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path_for(self, session_id: SessionID) -> Path:
        return self._directory / f"{session_id}.json"

    @staticmethod
    def _serialise(data: SessionData) -> dict:
        """Convert *SessionData* to a JSON-safe dictionary."""
        return {
            "session_id": data.session_id,
            "system": data.system,
            "parent_id": data.parent_id,
            "created_at": data.created_at,
            "updated_at": data.updated_at,
            "metadata": data.metadata,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in m.tool_calls
                    ],
                    "call_id": m.call_id,
                }
                for m in data.messages
            ],
        }

    @staticmethod
    def _deserialise(raw: dict) -> SessionData:
        """Reconstruct a *SessionData* from a parsed JSON dictionary."""
        messages: list[Message] = []
        for m in raw["messages"]:
            tool_calls = [
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                for tc in m.get("tool_calls", [])
            ]
            messages.append(
                Message(
                    role=m["role"],
                    content=m["content"],
                    tool_calls=tool_calls,
                    call_id=m.get("call_id"),
                )
            )
        return SessionData(
            session_id=raw["session_id"],
            messages=messages,
            system=raw.get("system"),
            parent_id=raw.get("parent_id"),
            created_at=raw.get("created_at", 0.0),
            updated_at=raw.get("updated_at", 0.0),
            metadata=raw.get("metadata", {}),
        )
