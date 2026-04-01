"""Tests for hook emission in CompactionIntegration (PRE_COMPACT / POST_COMPACT)."""
from __future__ import annotations

import pytest

from chimera.core.compaction_integration import CompactionIntegration
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.types import HookOutput
from chimera.types import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recording_emitter() -> tuple[HookEmitter, list[tuple[HookEvent, dict]]]:
    """Return an emitter that records every (event, kwargs) it receives."""
    recordings: list[tuple[HookEvent, dict]] = []

    async def recording_emit(self, event, **kwargs):
        recordings.append((event, kwargs))
        return HookOutput()

    emitter = HookEmitter()
    emitter.emit = recording_emit.__get__(emitter, HookEmitter)
    return emitter, recordings


class MockCompressor:
    """A compressor that simply returns half the messages."""

    async def compress(self, messages, aggressive=False):
        return messages[: len(messages) // 2] or messages[:1]


class MockEstimator:
    """Returns a fixed token count."""

    def __init__(self, count: int):
        self._count = count

    async def count_messages(self, messages):
        return self._count


# ---------------------------------------------------------------------------
# Tests: PRE_COMPACT and POST_COMPACT
# ---------------------------------------------------------------------------


class TestCompactionHooks:
    @pytest.mark.asyncio
    async def test_pre_and_post_compact_fire(self):
        """When compaction triggers, both PRE_COMPACT and POST_COMPACT fire."""
        emitter, recordings = _make_recording_emitter()
        ci = CompactionIntegration(
            compressor=MockCompressor(),
            estimator=MockEstimator(90_000),  # above 80% of 100k
            emitter=emitter,
        )

        msgs = [Message.user("hello")] * 10
        result, fired = await ci.auto_compact_if_needed(msgs, token_budget=100_000)

        assert fired is True
        events = [r[0] for r in recordings]
        assert HookEvent.PRE_COMPACT in events
        assert HookEvent.POST_COMPACT in events
        # PRE should come before POST
        assert events.index(HookEvent.PRE_COMPACT) < events.index(HookEvent.POST_COMPACT)

    @pytest.mark.asyncio
    async def test_no_hooks_when_under_threshold(self):
        """When under threshold, no hooks fire."""
        emitter, recordings = _make_recording_emitter()
        ci = CompactionIntegration(
            compressor=MockCompressor(),
            estimator=MockEstimator(1000),  # well under threshold
            emitter=emitter,
        )

        msgs = [Message.user("hello")]
        result, fired = await ci.auto_compact_if_needed(msgs, token_budget=100_000)

        assert fired is False
        assert len(recordings) == 0

    @pytest.mark.asyncio
    async def test_no_emitter_still_works(self):
        """CompactionIntegration without emitter should work as before."""
        ci = CompactionIntegration(
            compressor=MockCompressor(),
            estimator=MockEstimator(90_000),
        )

        msgs = [Message.user("hello")] * 10
        result, fired = await ci.auto_compact_if_needed(msgs, token_budget=100_000)
        assert fired is True

    @pytest.mark.asyncio
    async def test_no_compressor_no_hooks(self):
        """Without compressor, nothing fires."""
        emitter, recordings = _make_recording_emitter()
        ci = CompactionIntegration(emitter=emitter)

        msgs = [Message.user("hello")]
        result, fired = await ci.auto_compact_if_needed(msgs, token_budget=100_000)
        assert fired is False
        assert len(recordings) == 0
