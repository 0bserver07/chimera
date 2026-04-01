"""Tests for ContentReplacementState and ContentReplacementEntry."""

from chimera.core.content_replacement import ContentReplacementEntry, ContentReplacementState


def test_should_persist_above_threshold():
    """should_persist returns True when result_size exceeds per_tool_max_chars."""
    state = ContentReplacementState(per_tool_max_chars=100)
    assert state.should_persist("tool-1", result_size=200) is True
    assert state.should_persist("tool-2", result_size=50) is False


def test_frozen_decisions_after_record():
    """Once a decision is recorded, should_persist returns consistent results."""
    state = ContentReplacementState(per_tool_max_chars=100)
    # Record tool-1 as persisted
    state.record_decision("tool-1", persisted_path="/tmp/tool-1.txt", preview="preview...", original_size=500)
    # Even though we call with a small size, it was already persisted
    assert state.should_persist("tool-1", result_size=10) is True

    # Record tool-2 as NOT persisted (no persisted_path)
    state.record_decision("tool-2")
    # Even though size is large, decision was already made to keep inline
    assert state.should_persist("tool-2", result_size=9999) is False


def test_inline_frozen_decision():
    """A tool recorded without persisted_path stays inline on re-check."""
    state = ContentReplacementState(per_tool_max_chars=100)
    state.record_decision("inline-tool")
    assert state.should_persist("inline-tool", result_size=9999) is False
    assert "inline-tool" in state.seen_ids
    assert "inline-tool" not in state.replacements


def test_clone_independence():
    """Cloned state is independent — mutations don't affect original."""
    state = ContentReplacementState(per_tool_max_chars=100)
    state.record_decision("tool-a", persisted_path="/tmp/a.txt", preview="prev", original_size=200)
    cloned = state.clone()

    # Mutate the clone
    cloned.record_decision("tool-b", persisted_path="/tmp/b.txt", preview="prev2", original_size=300)

    assert "tool-b" in cloned.seen_ids
    assert "tool-b" not in state.seen_ids
    assert "tool-b" in cloned.replacements
    assert "tool-b" not in state.replacements


def test_reconstruct_from_transcript():
    """reconstruct_from_transcript rebuilds state from a list of entries."""
    entries = [
        ContentReplacementEntry(
            tool_use_id="t1",
            persisted_path="/tmp/t1.txt",
            preview="first bytes...",
            original_size=5000,
            timestamp=1000.0,
        ),
        ContentReplacementEntry(
            tool_use_id="t2",
            persisted_path="/tmp/t2.txt",
            preview="other bytes...",
            original_size=8000,
            timestamp=1001.0,
        ),
    ]
    state = ContentReplacementState.reconstruct_from_transcript(entries)
    assert "t1" in state.seen_ids
    assert "t2" in state.seen_ids
    assert state.replacements["t1"].persisted_path == "/tmp/t1.txt"
    assert state.replacements["t2"].original_size == 8000
