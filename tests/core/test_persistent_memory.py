"""Tests for chimera.context.persistent_memory — persistent memory module."""
from __future__ import annotations

import os
import tempfile

from chimera.context.persistent_memory import PersistentMemory
from chimera.types import Message


class TestFactExtraction:
    """Test automatic fact extraction from conversation messages."""

    def test_extract_facts_from_messages(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            memory = PersistentMemory(path=path, auto_save_interval=1)

            messages = [
                Message.assistant("The project uses pytest for testing."),
                Message.assistant("Python 3.12 is required."),
                Message.user("Thanks"),
            ]

            # record_turn should auto-extract after 1 turn
            result = memory.record_turn(messages)

            # Should have extracted facts (the assistant messages contain
            # indicator words like "uses" and "is")
            assert memory.fact_count > 0
            assert result is not None
            assert "facts" in result.lower()
        finally:
            os.unlink(path)


class TestPersistenceAcrossSessions:
    """Test that facts survive session recreation."""

    def test_facts_persist_across_instances(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            # Session 1: store a fact
            mem1 = PersistentMemory(path=path)
            mem1.store_fact("lang", "Python 3.12", category="project")
            assert mem1.fact_count == 1

            # Session 2: reload from same path
            mem2 = PersistentMemory(path=path)
            assert mem2.fact_count == 1
            assert mem2.recall("lang") == "Python 3.12"
        finally:
            os.unlink(path)


class TestContextInjection:
    """Test prompt section generation for session startup."""

    def test_context_injection_renders_markdown(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            memory = PersistentMemory(path=path)
            memory.store_fact("build_tool", "uv", category="project")
            memory.store_fact("test_framework", "pytest", category="testing")

            injection = memory.get_context_injection()

            assert "## Agent Memory" in injection
            assert "uv" in injection
            assert "pytest" in injection
            assert "build_tool" in injection
        finally:
            os.unlink(path)
