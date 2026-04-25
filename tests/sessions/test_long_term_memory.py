"""Tests for chimera.sessions.long_term_memory."""

from __future__ import annotations

import os
import tempfile

from chimera.sessions.long_term_memory import LongTermMemory, MemoryEntry


def test_store_and_recall():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("user_name", "Alice", category="preference")
        assert mem.recall("user_name") == "Alice"
    finally:
        os.unlink(path)


def test_persistence_across_instances():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem1 = LongTermMemory(path)
        mem1.store("lang", "Python")

        mem2 = LongTermMemory(path)  # new instance, same file
        assert mem2.recall("lang") == "Python"
    finally:
        os.unlink(path)


def test_recall_category():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("k1", "v1", category="project")
        mem.store("k2", "v2", category="project")
        mem.store("k3", "v3", category="personal")
        results = mem.recall_category("project")
        assert len(results) == 2
    finally:
        os.unlink(path)


def test_search():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("db", "PostgreSQL 15")
        mem.store("lang", "Python 3.12")
        results = mem.search("python")
        assert len(results) == 1
    finally:
        os.unlink(path)


def test_forget():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("temp", "data")
        assert mem.forget("temp")
        assert mem.recall("temp") is None
        assert not mem.forget("nonexistent")
    finally:
        os.unlink(path)


def test_to_prompt_section():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("name", "Alice", category="preference")
        mem.store("lang", "Python", category="project")
        section = mem.to_prompt_section()
        assert "Agent Memory" in section
        assert "Alice" in section
        assert "Python" in section
    finally:
        os.unlink(path)


def test_to_prompt_section_filtered():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("name", "Alice", category="preference")
        mem.store("lang", "Python", category="project")
        section = mem.to_prompt_section(categories=["preference"])
        assert "Alice" in section
        assert "Python" not in section
    finally:
        os.unlink(path)


def test_to_prompt_section_empty():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        assert mem.to_prompt_section() == ""
    finally:
        os.unlink(path)


def test_update_preserves_created_at():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("k", "v1")
        created = mem._entries["k"].created_at
        mem.store("k", "v2")
        assert mem._entries["k"].created_at == created
        assert mem.recall("k") == "v2"
    finally:
        os.unlink(path)


def test_count_and_clear():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("a", "1")
        mem.store("b", "2")
        assert mem.count == 2
        mem.clear()
        assert mem.count == 0
    finally:
        os.unlink(path)


def test_entries_property():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("x", "hello")
        mem.store("y", "world")
        entries = mem.entries
        assert len(entries) == 2
        assert all(isinstance(e, MemoryEntry) for e in entries)
    finally:
        os.unlink(path)


def test_metadata():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("k", "v", metadata={"source": "user", "confidence": 0.9})
        entry = mem._entries["k"]
        assert entry.metadata["source"] == "user"
        assert entry.metadata["confidence"] == 0.9

        # Verify metadata survives persistence
        mem2 = LongTermMemory(path)
        entry2 = mem2._entries["k"]
        assert entry2.metadata["source"] == "user"
    finally:
        os.unlink(path)


def test_recall_missing_key():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        assert mem.recall("nonexistent") is None
    finally:
        os.unlink(path)


def test_search_matches_key():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mem = LongTermMemory(path)
        mem.store("python_version", "3.12")
        results = mem.search("python")
        assert len(results) == 1
        assert results[0].key == "python_version"
    finally:
        os.unlink(path)


def test_nonexistent_file_starts_empty():
    path = tempfile.mktemp(suffix=".json")
    try:
        mem = LongTermMemory(path)
        assert mem.count == 0
        mem.store("k", "v")
        assert os.path.exists(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_corrupted_file_starts_fresh():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write("not valid json {{{")
        path = f.name
    try:
        mem = LongTermMemory(path)
        assert mem.count == 0
        mem.store("k", "v")
        assert mem.recall("k") == "v"
    finally:
        os.unlink(path)
