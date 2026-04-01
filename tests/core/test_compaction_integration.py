import pytest
from chimera.core.compaction_integration import CompactionIntegration
from chimera.types import Message

@pytest.mark.asyncio
async def test_no_compaction_when_under_threshold():
    ci = CompactionIntegration()
    msgs = [Message.user("hello")]
    result, fired = await ci.auto_compact_if_needed(msgs, token_budget=100000)
    assert result == msgs
    assert fired is False

@pytest.mark.asyncio
async def test_reactive_compact_returns_none_without_compressor():
    ci = CompactionIntegration()
    result = await ci.reactive_compact([Message.user("test")])
    assert result is None
