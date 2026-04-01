"""Tests for TranscriptStorage."""

import json
import tempfile
from pathlib import Path

import pytest

from chimera.sessions.transcript import TranscriptStorage
from chimera.types import Message


@pytest.mark.asyncio
async def test_record_and_load():
    """record appends JSONL lines; load reads them back as Message objects."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = TranscriptStorage(session_dir=Path(tmp), session_id="sess-1")
        msg1 = Message.user("hello")
        msg2 = Message.assistant("hi there")

        await storage.record(msg1, parent_uuid=None)
        await storage.record(msg2, parent_uuid="uuid-1")

        entries = await storage.load()
        assert len(entries) == 2
        assert entries[0].role == "user"
        assert entries[0].content == "hello"
        assert entries[1].role == "assistant"
        assert entries[1].content == "hi there"


@pytest.mark.asyncio
async def test_subagent_sidechain():
    """record_subagent writes to a separate JSONL file per agent."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = TranscriptStorage(session_dir=Path(tmp), session_id="sess-2")
        msg = Message.user("sub-task")

        await storage.record_subagent("agent-a", msg, parent_uuid="parent-1")

        entries = await storage.load_subagent("agent-a")
        assert len(entries) == 1
        assert entries[0].content == "sub-task"

        # The subagent should appear in the list
        ids = storage.list_subagent_ids()
        assert "agent-a" in ids


@pytest.mark.asyncio
async def test_basic_structure():
    """Each JSONL line is valid JSON with expected keys."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = TranscriptStorage(session_dir=Path(tmp), session_id="sess-3")
        msg = Message.user("test message")
        await storage.record(msg)

        # Read the raw file and verify structure
        main_path = Path(tmp) / "sess-3.jsonl"
        text = main_path.read_text().strip()
        data = json.loads(text)
        assert "role" in data
        assert "content" in data
        assert "timestamp" in data
