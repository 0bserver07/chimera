"""Tests for SessionResumer."""

import tempfile
from pathlib import Path

import pytest

from chimera.core.content_replacement import ContentReplacementEntry
from chimera.core.tool_result_persister import ToolResultPersister
from chimera.sessions.resume import SessionResumer
from chimera.sessions.transcript import TranscriptStorage
from chimera.types import Message


@pytest.mark.asyncio
async def test_resume_reconstructs_content_replacement_state():
    """resume loads transcript and rebuilds ContentReplacementState from persisted results."""
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp)
        session_id = "resume-test"

        # Set up transcript with messages
        storage = TranscriptStorage(session_dir=session_dir, session_id=session_id)
        await storage.record(Message.user("do something"))
        await storage.record(Message.assistant("ok, running tool"))

        # Set up a persisted tool result
        persister = ToolResultPersister(session_dir=session_dir)
        content = "X" * 5000
        path, preview = await persister.persist("tool-99", content)

        # Record the tool message with replacement metadata
        tool_msg = Message.tool(call_id="tool-99", content=preview)
        await storage.record(tool_msg)

        # Also store replacement info in the transcript as a metadata line
        import json, time

        replacement_entry = {
            "type": "content_replacement",
            "tool_use_id": "tool-99",
            "persisted_path": path,
            "preview": preview,
            "original_size": len(content),
            "timestamp": time.time(),
        }
        line = json.dumps(replacement_entry)
        # Append directly to the JSONL file
        (session_dir / f"{session_id}.jsonl").open("a").write(line + "\n")

        resumer = SessionResumer()
        messages, cr_state = await resumer.resume(session_id, storage, persister)

        # Should have loaded messages
        assert len(messages) >= 3

        # ContentReplacementState should have the persisted entry
        assert "tool-99" in cr_state.seen_ids
        assert "tool-99" in cr_state.replacements
        assert cr_state.replacements["tool-99"].persisted_path == path
