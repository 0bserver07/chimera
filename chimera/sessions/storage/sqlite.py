from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from chimera.sessions.base import SessionData, SessionID, Storage
from chimera.types import Message, ToolCall

__all__ = ["SQLiteStorage"]

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    system       TEXT,
    parent_id    TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    metadata     TEXT NOT NULL DEFAULT '{}',
    messages     TEXT NOT NULL DEFAULT '[]'
);
"""


class SQLiteStorage(Storage):
    """SQLite-backed session storage using the stdlib *sqlite3* module.

    Messages are stored as a JSON blob in a single ``messages`` column.
    """

    def __init__(self, db_path: str = "~/.chimera/sessions.db") -> None:
        resolved = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        self._conn = sqlite3.connect(resolved)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Storage interface
    # ------------------------------------------------------------------

    def save(self, session_id: SessionID, data: SessionData) -> None:
        messages_json = json.dumps(self._serialise_messages(data.messages))
        metadata_json = json.dumps(data.metadata)
        self._conn.execute(
            """
            INSERT INTO sessions
                (session_id, system, parent_id, created_at, updated_at,
                 metadata, messages)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                system     = excluded.system,
                parent_id  = excluded.parent_id,
                updated_at = excluded.updated_at,
                metadata   = excluded.metadata,
                messages   = excluded.messages
            """,
            (
                data.session_id,
                data.system,
                data.parent_id,
                data.created_at,
                data.updated_at,
                metadata_json,
                messages_json,
            ),
        )
        self._conn.commit()

    def load(self, session_id: SessionID) -> SessionData | None:
        cursor = self._conn.execute(
            "SELECT session_id, system, parent_id, created_at, updated_at, "
            "metadata, messages FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_session_data(row)

    def list_sessions(self) -> list[SessionID]:
        cursor = self._conn.execute(
            "SELECT session_id FROM sessions ORDER BY created_at"
        )
        return [row[0] for row in cursor.fetchall()]

    def delete(self, session_id: SessionID) -> None:
        self._conn.execute(
            "DELETE FROM sessions WHERE session_id = ?", (session_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise_messages(messages: list[Message]) -> list[dict[str, Any]]:
        return [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in m.tool_calls
                ],
                "call_id": m.call_id,
            }
            for m in messages
        ]

    @staticmethod
    def _deserialise_messages(raw: list[dict[str, Any]]) -> list[Message]:
        messages: list[Message] = []
        for m in raw:
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
        return messages

    @staticmethod
    def _row_to_session_data(row: tuple[Any, ...]) -> SessionData:
        (
            session_id,
            system,
            parent_id,
            created_at,
            updated_at,
            metadata_json,
            messages_json,
        ) = row
        return SessionData(
            session_id=session_id,
            messages=SQLiteStorage._deserialise_messages(
                json.loads(messages_json)
            ),
            system=system,
            parent_id=parent_id,
            created_at=created_at,
            updated_at=updated_at,
            metadata=json.loads(metadata_json),
        )
