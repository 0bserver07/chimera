"""Tests for ToolResultPersister."""

import tempfile
from pathlib import Path

import pytest

from chimera.core.tool_result_persister import ToolResultPersister


@pytest.mark.asyncio
async def test_persist_and_read():
    """persist writes to disk, read retrieves the content."""
    with tempfile.TemporaryDirectory() as tmp:
        persister = ToolResultPersister(session_dir=Path(tmp))
        content = "A" * 5000
        path, preview = await persister.persist("tool-42", content)

        # File should exist on disk
        assert Path(path).exists()
        assert Path(path).read_text() == content

        # Preview should be a truncated version
        assert len(preview) < len(content)
        assert "truncated" in preview.lower() or len(preview) <= persister._preview_size

        # read should return the same content
        result = await persister.read("tool-42")
        assert result == content


@pytest.mark.asyncio
async def test_read_nonexistent():
    """read returns None for an id that was never persisted."""
    with tempfile.TemporaryDirectory() as tmp:
        persister = ToolResultPersister(session_dir=Path(tmp))
        result = await persister.read("does-not-exist")
        assert result is None
