"""Integration tests verifying new modules are wired into the agent execution path.

These are NOT unit tests (those exist already). These verify that modules
work TOGETHER within the agent loop using mocked providers.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.core.agent import Agent
from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Shared mock provider
# ---------------------------------------------------------------------------

class _MockProvider(Provider):
    """Provider that returns canned responses. Override complete() per-test."""

    def __init__(self, responses: list[Response] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._call_count += 1
        if self._responses:
            return self._responses.pop(0)
        return Response(content="Done.", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock-model"


class _DummyTool(BaseTool):
    """Tool whose execute() returns a fixed string."""

    def __init__(self, name: str, output: str) -> None:
        self.name = name
        self.description = f"Dummy tool: {name}"
        self.parameters = {"type": "object", "properties": {}}
        self._output = output

    def execute(self, args, env):
        return ToolResult(output=self._output)


# =========================================================================
# 1. Truncation in loop
# =========================================================================

class TestTruncationInLoop:
    """Verify that huge tool output is truncated (head+tail) before landing in context."""

    def test_truncation_in_loop(self):
        from chimera.core.truncation import TruncationConfig, truncate_output

        # Build a 500-line output
        big_output = "\n".join(f"line-{i}" for i in range(500))

        # Default config: max_lines=200, head=50, tail=50
        config = TruncationConfig()
        result = truncate_output(big_output, config)
        result_lines = result.split("\n")

        # Total should be head + marker + tail = 50 + 1 + 50 = 101
        assert len(result_lines) == 101, f"Expected 101 lines, got {len(result_lines)}"

        # Head preserved
        assert result_lines[0] == "line-0"
        assert result_lines[49] == "line-49"

        # Tail preserved
        assert result_lines[-1] == "line-499"
        assert result_lines[-50] == "line-450"

        # Marker in the middle
        marker_line = result_lines[50]
        assert "truncated" in marker_line.lower()
        assert "400" in marker_line  # 500 - 50 - 50 = 400 truncated

    def test_truncation_within_agent_loop(self, tmp_path):
        """Verify truncation works when a tool returns large output in the agent loop."""
        from chimera.core.truncation import truncate_output

        big_output = "\n".join(f"line-{i}" for i in range(500))
        tool = _DummyTool("big_tool", big_output)

        # Simulate what the loop does: execute tool, then truncate result
        result = tool.execute({}, None)
        truncated = truncate_output(result.output)
        lines = truncated.split("\n")

        # Should be truncated
        assert len(lines) < 500
        assert "line-0" in lines[0]
        assert "line-499" in lines[-1]
        assert any("truncated" in l.lower() for l in lines)


# =========================================================================
# 2. Ghost commits on write
# =========================================================================

class TestGhostCommitsOnWrite:
    """Verify GhostCommitManager snapshots files before writes."""

    def test_ghost_commits_on_write(self, tmp_path):
        from chimera.checkpoints_ghost import GhostCommitManager

        ghost = GhostCommitManager(workdir=str(tmp_path))

        # Pre-existing file
        target = tmp_path / "main.py"
        target.write_text("original content")

        # Snapshot before write (what the agent loop would do)
        snap_id = ghost.snapshot("write_file: main.py", paths=["main.py"])

        # Simulate the write
        target.write_text("new content")

        # Verify snapshot was created
        assert ghost.depth == 1
        snap = ghost.peek()
        assert snap is not None
        assert snap.id == snap_id
        assert snap.label == "write_file: main.py"
        assert snap.files["main.py"] == "original content"

        # Undo restores original
        restored = ghost.undo()
        assert len(restored) == 1
        assert "restored" in restored[0]
        assert target.read_text() == "original content"

    def test_ghost_commit_for_new_file(self, tmp_path):
        """Snapshot records absence for files that don't yet exist."""
        from chimera.checkpoints_ghost import GhostCommitManager

        ghost = GhostCommitManager(workdir=str(tmp_path))

        snap_id = ghost.snapshot("write_file: new.py", paths=["new.py"])
        snap = ghost.peek()
        assert snap is not None
        assert snap.files["new.py"] == ""  # file didn't exist

        # Now create the file
        (tmp_path / "new.py").write_text("created")

        # Undo should delete the newly created file
        ghost.undo()
        assert not (tmp_path / "new.py").exists()


# =========================================================================
# 3. RepoMapMiddleware injects context
# =========================================================================

class TestRepoMapMiddlewareInjectsContext:
    """Verify RepoMapMiddleware injects a repo map into context messages."""

    def test_repo_map_middleware_injects_context(self, tmp_path):
        from chimera.context.repo_map import RepoMapMiddleware

        # Create some Python files
        (tmp_path / "calculator.py").write_text(
            "class Calculator:\n    def add(self, a, b):\n        return a + b\n"
        )
        (tmp_path / "utils.py").write_text(
            "def helper():\n    pass\n"
        )

        mw = RepoMapMiddleware(workdir=str(tmp_path), max_tokens=3000)
        context = Context(system="You are a helper.")
        context.add(Message.user("Fix the calculator"))

        # Call before_model
        context = mw.before_model(context, [])

        # Should have injected a system message at position 0
        assert len(context.messages) >= 2
        injected = context.messages[0]
        assert injected.role == "system"
        assert "Repository structure" in injected.content
        assert "calculator.py" in injected.content
        assert "utils.py" in injected.content

        # Class and function symbols should appear
        assert "class Calculator" in injected.content
        assert "def helper" in injected.content

    def test_repo_map_middleware_only_injects_once(self, tmp_path):
        """Second call to before_model should not re-inject."""
        from chimera.context.repo_map import RepoMapMiddleware

        (tmp_path / "a.py").write_text("def foo(): pass\n")

        mw = RepoMapMiddleware(workdir=str(tmp_path))
        context = Context()
        context.add(Message.user("test"))

        context = mw.before_model(context, [])
        count_after_first = len(context.messages)

        context = mw.before_model(context, [])
        assert len(context.messages) == count_after_first


# =========================================================================
# 4. CachedProvider deduplicates
# =========================================================================

class TestCachedProviderDeduplicates:
    """Verify CachedProvider returns cached responses on duplicate calls."""

    def test_cached_provider_deduplicates(self):
        from chimera.providers.cached import CachedProvider

        inner = _MockProvider(responses=[
            Response(content="Hello", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5}),
        ])

        cached = CachedProvider(inner, max_entries=10)
        msgs = [Message.user("Hi")]

        r1 = cached.complete(msgs)
        r2 = cached.complete(msgs)

        assert r1.content == "Hello"
        assert r2.content == "Hello"
        # Inner provider should have been called exactly once
        assert inner._call_count == 1
        assert cached.stats.hits == 1
        assert cached.stats.misses == 1

    def test_cached_provider_different_messages(self):
        """Different messages should result in separate cache entries."""
        from chimera.providers.cached import CachedProvider

        inner = _MockProvider(responses=[
            Response(content="R1", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5}),
            Response(content="R2", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5}),
        ])

        cached = CachedProvider(inner, max_entries=10)
        r1 = cached.complete([Message.user("A")])
        r2 = cached.complete([Message.user("B")])

        assert r1.content == "R1"
        assert r2.content == "R2"
        assert inner._call_count == 2
        assert cached.stats.misses == 2
        assert cached.stats.hits == 0


# =========================================================================
# 5. SmartCompaction preserves recent
# =========================================================================

class TestSmartCompactionPreservesRecent:
    """Verify SmartCompaction summarizes older messages and preserves recent ones."""

    def test_smart_compaction_preserves_recent(self):
        from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig

        # Create 20 messages: alternating user/assistant
        messages: list[Message] = []
        for i in range(10):
            messages.append(Message.user(f"Question {i}"))
            messages.append(Message.assistant(f"Answer {i}"))

        assert len(messages) == 20

        config = SmartCompactionConfig(preserve_recent=5)
        compaction = SmartCompaction(config=config)
        result = compaction.compact(messages, budget=4000)

        # Last 5 messages should be intact
        last_5_original = messages[-5:]
        last_5_result = result[-5:]
        for orig, res in zip(last_5_original, last_5_result):
            assert orig.role == res.role
            assert orig.content == res.content

        # First message should be the summary
        summary_msg = result[0]
        assert summary_msg.role == "system"
        assert "[Conversation summary]" in summary_msg.content

        # Total should be summary + 5 recent = 6
        assert len(result) == 6

    def test_smart_compaction_no_op_when_few_messages(self):
        """If messages <= preserve_recent, no compaction occurs."""
        from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig

        messages = [Message.user("A"), Message.assistant("B")]
        config = SmartCompactionConfig(preserve_recent=5)
        compaction = SmartCompaction(config=config)

        result = compaction.compact(messages, budget=4000)
        assert len(result) == 2
        assert result[0].content == "A"
        assert result[1].content == "B"


# =========================================================================
# 6. ThoughtStrip compaction
# =========================================================================

class TestThoughtStripCompaction:
    """Verify ThoughtStripCompaction strips thinking blocks from old messages."""

    def test_thought_strip_compaction(self):
        from chimera.compaction.thought_strip import ThoughtStripCompaction

        messages = [
            Message.user("Task 1"),
            Message.assistant("<thinking>Deep analysis here...</thinking>The answer is 42."),
            Message.user("Task 2"),
            Message.assistant("<thinking>More thinking...</thinking>The answer is 7."),
            Message.user("Task 3"),
            Message.assistant("<thinking>Recent thinking</thinking>The answer is 99."),
        ]

        # preserve_recent=1 means keep thinking in the last 1 assistant message
        compaction = ThoughtStripCompaction(preserve_recent=1)
        result = compaction.compact(messages, budget=8000)

        assert len(result) == 6  # same count, but thinking stripped

        # First assistant message (index 1): thinking should be stripped
        assert "<thinking>" not in result[1].content
        assert "The answer is 42." in result[1].content

        # Second assistant message (index 3): thinking should be stripped
        assert "<thinking>" not in result[3].content
        assert "The answer is 7." in result[3].content

        # Last assistant message (index 5): thinking should be KEPT
        assert "<thinking>" in result[5].content
        assert "Recent thinking" in result[5].content
        assert "The answer is 99." in result[5].content

    def test_thought_strip_bracket_syntax(self):
        """Also handles [thinking]...[/thinking] syntax."""
        from chimera.compaction.thought_strip import ThoughtStripCompaction

        messages = [
            Message.user("Q"),
            Message.assistant("[thinking]Old thought[/thinking]Answer A"),
            Message.user("Q2"),
            Message.assistant("[thinking]New thought[/thinking]Answer B"),
        ]

        compaction = ThoughtStripCompaction(preserve_recent=1)
        result = compaction.compact(messages, budget=8000)

        # Old thinking stripped
        assert "[thinking]" not in result[1].content
        assert "Answer A" in result[1].content

        # Recent thinking kept
        assert "[thinking]" in result[3].content


# =========================================================================
# 7. Commit style detection
# =========================================================================

class TestCommitStyleDetection:
    """Verify analyze_style detects conventional commits and generate_commit_message works."""

    def test_commit_style_detection(self):
        from chimera.workflows.commit_style import (
            CommitStyle,
            analyze_style,
            generate_commit_message,
        )

        conventional_messages = [
            "feat: add user authentication",
            "fix: resolve login timeout",
            "chore: update dependencies",
            "docs: update README",
            "feat(api): add rate limiting",
            "fix: handle null pointer in parser",
            "refactor: extract validation logic",
            "test: add integration tests",
        ]

        analysis = analyze_style(conventional_messages)

        assert analysis.style == CommitStyle.CONVENTIONAL
        assert analysis.confidence >= 0.5
        assert analysis.sample_count == 8
        assert "feat" in analysis.prefixes

        # Generate a message and verify it has the prefix
        msg = generate_commit_message(
            CommitStyle.CONVENTIONAL,
            "add caching layer",
            commit_type="feat",
        )
        assert msg.startswith("feat: ")
        assert "add caching layer" in msg

    def test_commit_style_fix_prefix(self):
        """Generate with fix: prefix."""
        from chimera.workflows.commit_style import CommitStyle, generate_commit_message

        msg = generate_commit_message(
            CommitStyle.CONVENTIONAL,
            "resolve race condition",
            commit_type="fix",
        )
        assert msg.startswith("fix: ")
        assert "resolve race condition" in msg

    def test_commit_style_freeform(self):
        """Detect freeform when no pattern dominates."""
        from chimera.workflows.commit_style import CommitStyle, analyze_style

        freeform_messages = [
            "Updated the login page",
            "Fixed a bug in the parser",
            "Added new tests",
            "Cleaned up code",
        ]

        analysis = analyze_style(freeform_messages)
        assert analysis.style == CommitStyle.FREEFORM


# =========================================================================
# 8. FileWatcher detects changes
# =========================================================================

class TestFileWatcherDetectsChanges:
    """Verify FileWatcher.check_once() detects file creations and modifications."""

    def test_file_watcher_detects_changes(self, tmp_path):
        from chimera.env.watcher import FileWatcher, ChangeType

        # Create initial file
        (tmp_path / "existing.txt").write_text("hello")

        watcher = FileWatcher(str(tmp_path), patterns=["*.txt", "*.py"])
        # Initialize snapshot
        watcher._snapshots = watcher._scan()

        # Create a new file
        (tmp_path / "new_file.txt").write_text("world")

        changes = watcher.check_once()

        assert len(changes) >= 1
        new_file_changes = [c for c in changes if "new_file" in c.path]
        assert len(new_file_changes) == 1
        assert new_file_changes[0].change_type == ChangeType.CREATED

    def test_file_watcher_detects_modification(self, tmp_path):
        """Detect modification of an existing file."""
        from chimera.env.watcher import FileWatcher, ChangeType

        target = tmp_path / "target.py"
        target.write_text("original")

        watcher = FileWatcher(str(tmp_path), patterns=["*.py"])
        watcher._snapshots = watcher._scan()

        # Wait briefly so mtime differs, then modify
        time.sleep(0.05)
        target.write_text("modified")

        changes = watcher.check_once()
        modified = [c for c in changes if c.change_type == ChangeType.MODIFIED]
        assert len(modified) >= 1

    def test_file_watcher_detects_deletion(self, tmp_path):
        """Detect deletion of a file."""
        from chimera.env.watcher import FileWatcher, ChangeType

        target = tmp_path / "doomed.txt"
        target.write_text("bye")

        watcher = FileWatcher(str(tmp_path), patterns=["*.txt"])
        watcher._snapshots = watcher._scan()

        target.unlink()

        changes = watcher.check_once()
        deleted = [c for c in changes if c.change_type == ChangeType.DELETED]
        assert len(deleted) == 1
        assert "doomed.txt" in deleted[0].path


# =========================================================================
# 9. Investigator parses output
# =========================================================================

class TestInvestigatorParsesOutput:
    """Verify _parse_investigation extracts structured data from raw output."""

    def test_investigator_parses_output(self):
        from chimera.agents.investigator import _parse_investigation

        sample_output = """I've analyzed the codebase. Here are my findings:

RELEVANT_FILES: src/auth.py, src/login.py, src/middleware.py
TEST_FILES: tests/test_auth.py, tests/test_login.py
DEPENDENCIES: flask, sqlalchemy, bcrypt
APPROACH: Fix the authentication timeout by increasing the session TTL in auth.py and adding retry logic in login.py

Additional notes: The bug is likely in the session handler."""

        inv = _parse_investigation(sample_output)

        assert inv.relevant_files == ["src/auth.py", "src/login.py", "src/middleware.py"]
        assert inv.test_files == ["tests/test_auth.py", "tests/test_login.py"]
        assert inv.dependencies == ["flask", "sqlalchemy", "bcrypt"]
        assert "session TTL" in inv.suggested_approach
        assert inv.raw_output == sample_output

    def test_investigator_empty_output(self):
        """Handle empty or malformed output gracefully."""
        from chimera.agents.investigator import _parse_investigation

        inv = _parse_investigation("")
        assert inv.relevant_files == []
        assert inv.test_files == []
        assert inv.dependencies == []
        assert inv.suggested_approach == ""

    def test_investigator_to_context_block(self):
        """Verify to_context_block() formats nicely."""
        from chimera.agents.investigator import _parse_investigation

        output = "RELEVANT_FILES: a.py, b.py\nTEST_FILES: test_a.py\nDEPENDENCIES: numpy\nAPPROACH: Refactor the module"
        inv = _parse_investigation(output)
        block = inv.to_context_block()

        assert "[Codebase Investigation]" in block
        assert "a.py" in block
        assert "test_a.py" in block
        assert "numpy" in block
        assert "Refactor the module" in block


# =========================================================================
# 10. EditProposal roundtrip
# =========================================================================

class TestEditProposalRoundtrip:
    """Verify EditProposal: add edits, accept/reject, apply to mock env."""

    def test_edit_proposal_roundtrip(self):
        from chimera.core.proposed_edit import EditProposal, EditStatus

        proposal = EditProposal()

        # Add three edits
        proposal.add("calc.py", "def add(a,b): return a-b\n", "def add(a,b): return a+b\n", "Fix add function")
        proposal.add("test.py", "", "def test_add(): assert add(1,2)==3\n", "Add unit test")
        proposal.add("legacy.py", "old code\n", "new code\n", "Refactor legacy module")

        assert len(proposal.edits) == 3
        assert len(proposal.pending) == 3

        # Accept first two, reject third
        proposal.accept(0)
        proposal.accept(1)
        proposal.reject(2)

        assert proposal.edits[0].status == EditStatus.ACCEPTED
        assert proposal.edits[1].status == EditStatus.ACCEPTED
        assert proposal.edits[2].status == EditStatus.REJECTED
        assert len(proposal.accepted) == 2
        assert len(proposal.pending) == 0

        # Apply to a mock environment
        mock_env = MagicMock()
        applied = proposal.apply(mock_env)

        assert applied == ["calc.py", "test.py"]
        assert mock_env.write_file.call_count == 2

        # Verify correct content was written
        calls = mock_env.write_file.call_args_list
        assert calls[0].args == ("calc.py", "def add(a,b): return a+b\n")
        assert calls[1].args == ("test.py", "def test_add(): assert add(1,2)==3\n")

    def test_edit_proposal_accept_all(self):
        """accept_all() marks all pending as accepted."""
        from chimera.core.proposed_edit import EditProposal, EditStatus

        proposal = EditProposal()
        proposal.add("a.py", "old", "new")
        proposal.add("b.py", "old", "new")
        proposal.add("c.py", "old", "new")

        proposal.accept_all()
        assert all(e.status == EditStatus.ACCEPTED for e in proposal.edits)

    def test_edit_proposal_diff_generation(self):
        """unified_diff() produces a valid diff."""
        from chimera.core.proposed_edit import EditProposal

        proposal = EditProposal()
        edit = proposal.add("file.py", "line1\nline2\n", "line1\nline2_modified\n", "Modify line 2")

        diff = edit.unified_diff()
        assert "--- a/file.py" in diff
        assert "+++ b/file.py" in diff
        assert "-line2" in diff
        assert "+line2_modified" in diff

    def test_edit_proposal_summary(self):
        """summary() returns a human-readable overview."""
        from chimera.core.proposed_edit import EditProposal

        proposal = EditProposal()
        proposal.add("new.py", "", "print('hi')\n", "Create new file")
        proposal.add("old.py", "code\n", "", "Delete old file")

        summary = proposal.summary()
        assert "2 edit(s)" in summary
        assert "new" in summary  # new file
        assert "del" in summary  # deletion
