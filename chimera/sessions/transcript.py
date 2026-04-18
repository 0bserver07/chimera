"""JSONL-based transcript storage for main and subagent conversations."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from chimera.types import Message

try:
    import aiofiles

    _HAS_AIOFILES = True
except ImportError:  # pragma: no cover
    _HAS_AIOFILES = False


def _dict_to_message(entry: dict) -> Message:
    """Convert a raw JSONL dict to a :class:`~chimera.types.Message`."""
    role = entry.get("role", "user")
    content = entry.get("content", "")
    if role == "assistant":
        return Message.assistant(content)
    elif role == "tool":
        return Message.tool(entry.get("call_id", ""), content)
    else:
        return Message.user(content)


class TranscriptStorage:
    """Append-only JSONL transcript for a session.

    Main transcript is stored at ``session_dir / "<session_id>.jsonl"``.
    Sub-agent transcripts live under ``session_dir / "subagents/"``.
    """

    def __init__(self, session_dir: Path, session_id: str) -> None:
        self._session_dir = session_dir
        self._session_id = session_id
        self._main_path = session_dir / f"{session_id}.jsonl"
        self._subagents_dir = session_dir / "subagents"

        # Ensure directories exist
        session_dir.mkdir(parents=True, exist_ok=True)

    @property
    def main_path(self) -> Path:
        return self._main_path

    @property
    def subagents_dir(self) -> Path:
        return self._subagents_dir

    def _message_to_dict(self, message: Any, parent_uuid: str | None = None) -> dict:
        """Serialize a message to a JSON-compatible dict."""
        # Handle progress messages — skip them
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict) and metadata.get("type") == "progress":
            return {}

        entry: dict[str, Any] = {
            "role": getattr(message, "role", "unknown"),
            "content": getattr(message, "content", ""),
            "timestamp": time.time(),
        }

        if parent_uuid is not None:
            entry["parent_uuid"] = parent_uuid

        # Preserve call_id for tool messages
        call_id = getattr(message, "call_id", None)
        if call_id is not None:
            entry["call_id"] = call_id

        # Preserve tool_calls
        tool_calls = getattr(message, "tool_calls", [])
        if tool_calls:
            entry["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in tool_calls
            ]

        return entry

    async def _append_line(self, path: Path, line: str) -> None:
        """Append a single line to a file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if _HAS_AIOFILES:
            async with aiofiles.open(path, "a") as f:
                await f.write(line + "\n")
        else:
            await asyncio.to_thread(
                lambda: path.open("a").write(line + "\n"),
            )

    async def record(self, message: Any, parent_uuid: str | None = None) -> None:
        """Append a message to the main transcript, skipping progress messages."""
        entry = self._message_to_dict(message, parent_uuid)
        if not entry:
            return
        line = json.dumps(entry, default=str)
        await self._append_line(self._main_path, line)

    async def record_subagent(
        self,
        agent_id: str,
        message: Any,
        parent_uuid: str | None = None,
        subdir: str | None = None,
    ) -> None:
        """Append a message to a subagent's sidechain transcript."""
        base = Path(subdir) if subdir else self._subagents_dir
        path = base / f"{agent_id}.jsonl"
        entry = self._message_to_dict(message, parent_uuid)
        if not entry:
            return
        line = json.dumps(entry, default=str)
        await self._append_line(path, line)

    async def _read_jsonl(self, path: Path) -> list[dict]:
        """Read all lines from a JSONL file."""
        if not path.exists():
            return []
        if _HAS_AIOFILES:
            async with aiofiles.open(path, "r") as f:
                text = await f.read()
        else:
            text = await asyncio.to_thread(path.read_text)
        lines = text.strip().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    async def load_raw(self) -> list[dict]:
        """Read all raw JSONL dicts from the main transcript (includes metadata entries)."""
        return await self._read_jsonl(self._main_path)

    async def load(self) -> list[Message]:
        """Read all entries from the main transcript as :class:`~chimera.types.Message` objects."""
        raw = await self._read_jsonl(self._main_path)
        return [_dict_to_message(entry) for entry in raw]

    async def load_subagent(
        self,
        agent_id: str,
        subdir: str | None = None,
    ) -> list[Message]:
        """Read all entries from a subagent's transcript as :class:`~chimera.types.Message` objects."""
        base = Path(subdir) if subdir else self._subagents_dir
        path = base / f"{agent_id}.jsonl"
        raw = await self._read_jsonl(path)
        return [_dict_to_message(entry) for entry in raw]

    def list_subagent_ids(self) -> list[str]:
        """Return the agent ids that have sidechain transcripts."""
        if not self._subagents_dir.exists():
            return []
        return [
            p.stem
            for p in sorted(self._subagents_dir.iterdir())
            if p.suffix == ".jsonl"
        ]
