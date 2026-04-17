"""Tests for Phase 38: Close remaining coding agent gaps."""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest

from chimera.types import Message, ToolCall


# === Task 77: Head+tail truncation ===

class TestTruncation:
    def test_no_truncation_under_limit(self):
        from chimera.core.truncation import truncate_output
        text = "line1\nline2\nline3"
        assert truncate_output(text) == text

    def test_truncates_long_output(self):
        from chimera.core.truncation import truncate_output, TruncationConfig
        lines = [f"line {i}" for i in range(500)]
        text = "\n".join(lines)
        config = TruncationConfig(max_lines=100, head_lines=20, tail_lines=20)
        result = truncate_output(text, config)
        assert "line 0" in result
        assert "line 499" in result
        assert "460 lines truncated" in result

    def test_convenience_function(self):
        from chimera.core.truncation import truncate_result_output
        text = "\n".join(f"L{i}" for i in range(300))
        truncated, removed = truncate_result_output(text, max_lines=100)
        assert removed > 0
        assert "L0" in truncated
        assert "L299" in truncated

    def test_no_removal_returns_zero(self):
        from chimera.core.truncation import truncate_result_output
        text = "short"
        _, removed = truncate_result_output(text, max_lines=100)
        assert removed == 0


# === Task 78: Ghost commits ===

class TestGhostCommits:
    def test_snapshot_and_undo(self, tmp_path):
        from chimera.checkpoints_ghost import GhostCommitManager
        (tmp_path / "a.py").write_text("original")
        ghost = GhostCommitManager(str(tmp_path))
        ghost.snapshot("before edit", ["a.py"])
        (tmp_path / "a.py").write_text("modified")
        assert (tmp_path / "a.py").read_text() == "modified"
        ghost.undo()
        assert (tmp_path / "a.py").read_text() == "original"

    def test_undo_new_file(self, tmp_path):
        from chimera.checkpoints_ghost import GhostCommitManager
        ghost = GhostCommitManager(str(tmp_path))
        ghost.snapshot("before create", ["new.py"])
        (tmp_path / "new.py").write_text("new content")
        ghost.undo()
        assert not (tmp_path / "new.py").exists()

    def test_multi_undo(self, tmp_path):
        from chimera.checkpoints_ghost import GhostCommitManager
        (tmp_path / "a.py").write_text("v1")
        ghost = GhostCommitManager(str(tmp_path))
        ghost.snapshot("s1", ["a.py"])
        (tmp_path / "a.py").write_text("v2")
        ghost.snapshot("s2", ["a.py"])
        (tmp_path / "a.py").write_text("v3")
        ghost.undo(2)
        assert (tmp_path / "a.py").read_text() == "v1"

    def test_depth(self, tmp_path):
        from chimera.checkpoints_ghost import GhostCommitManager
        ghost = GhostCommitManager(str(tmp_path))
        ghost.snapshot("s1", [])
        ghost.snapshot("s2", [])
        assert ghost.depth == 2

    def test_max_snapshots(self, tmp_path):
        from chimera.checkpoints_ghost import GhostCommitManager
        ghost = GhostCommitManager(str(tmp_path), max_snapshots=3)
        for i in range(5):
            ghost.snapshot(f"s{i}", [])
        assert ghost.depth == 3


# === Task 79: Repo map ===

class TestRepoMap:
    def test_generate_file_level(self, tmp_path):
        from chimera.context.repo_map import generate_repo_map
        (tmp_path / "main.py").write_text("def main(): pass")
        (tmp_path / "utils.py").write_text("def helper(): pass")
        result = generate_repo_map(tmp_path, depth="file")
        assert "main.py" in result
        assert "utils.py" in result

    def test_generate_function_level(self, tmp_path):
        from chimera.context.repo_map import generate_repo_map
        (tmp_path / "app.py").write_text("class App:\n    pass\n\ndef run():\n    pass\n")
        result = generate_repo_map(tmp_path, depth="function")
        assert "class App" in result
        assert "def run" in result

    def test_skips_hidden_dirs(self, tmp_path):
        from chimera.context.repo_map import generate_repo_map
        (tmp_path / "visible.py").write_text("x = 1")
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("x = 2")
        result = generate_repo_map(tmp_path)
        assert "visible.py" in result
        assert "secret.py" not in result

    def test_token_budget(self, tmp_path):
        from chimera.context.repo_map import generate_repo_map
        for i in range(50):
            (tmp_path / f"file_{i}.py").write_text(f"def func_{i}(): pass\n" * 20)
        result = generate_repo_map(tmp_path, max_tokens=500)
        assert "truncated" in result


# === Task 80: Commit message inference ===

class TestCommitStyle:
    def test_detect_conventional(self):
        from chimera.workflows.commit_style import analyze_style, CommitStyle
        msgs = ["feat: add login", "fix: resolve crash", "chore: update deps", "feat: new API"]
        analysis = analyze_style(msgs)
        assert analysis.style == CommitStyle.CONVENTIONAL

    def test_detect_gitmoji(self):
        from chimera.workflows.commit_style import analyze_style, CommitStyle
        msgs = [":sparkles: add feature", ":bug: fix crash", ":memo: update docs"]
        analysis = analyze_style(msgs)
        assert analysis.style == CommitStyle.GITMOJI

    def test_detect_freeform(self):
        from chimera.workflows.commit_style import analyze_style, CommitStyle
        msgs = ["Add the thing", "Fix the bug", "Update readme"]
        analysis = analyze_style(msgs)
        assert analysis.style == CommitStyle.FREEFORM

    def test_generate_conventional(self):
        from chimera.workflows.commit_style import generate_commit_message, CommitStyle
        msg = generate_commit_message(CommitStyle.CONVENTIONAL, "add user auth", commit_type="feat")
        assert msg.startswith("feat: ")

    def test_generate_gitmoji(self):
        from chimera.workflows.commit_style import generate_commit_message, CommitStyle
        msg = generate_commit_message(CommitStyle.GITMOJI, "fix login bug", commit_type="fix")
        assert msg.startswith(":bug:")

    def test_generate_with_files(self):
        from chimera.workflows.commit_style import generate_commit_message, CommitStyle
        msg = generate_commit_message(CommitStyle.CONVENTIONAL, "add tests", ["a.py", "b.py"], "test")
        assert "Changed:" in msg

    def test_infer_and_generate(self, tmp_path):
        from chimera.workflows.commit_style import infer_and_generate
        # No git repo — falls back to freeform
        msg = infer_and_generate(str(tmp_path), "add new feature")
        assert len(msg) > 0


# === Task 81: Investigator ===

class TestInvestigator:
    def test_parse_investigation(self):
        from chimera.agents.investigator import _parse_investigation
        output = """RELEVANT_FILES: auth.py, login.py
TEST_FILES: test_auth.py
DEPENDENCIES: bcrypt, jwt
APPROACH: Fix the password hashing in auth.py"""
        inv = _parse_investigation(output)
        assert "auth.py" in inv.relevant_files
        assert "test_auth.py" in inv.test_files
        assert "bcrypt" in inv.dependencies
        assert "password" in inv.suggested_approach

    def test_to_context_block(self):
        from chimera.agents.investigator import Investigation
        inv = Investigation(
            relevant_files=["a.py", "b.py"],
            suggested_approach="Fix the bug",
        )
        block = inv.to_context_block()
        assert "a.py" in block
        assert "Fix the bug" in block


# === Task 82: Thought stripping ===

class TestThoughtStripping:
    def test_strips_old_thinking(self):
        from chimera.compaction.thought_strip import ThoughtStripCompaction
        messages = [
            Message.user("Task 1"),
            Message.assistant("<thinking>Long reasoning here</thinking>Answer 1"),
            Message.user("Task 2"),
            Message.assistant("<thinking>More reasoning</thinking>Answer 2"),
            Message.user("Task 3"),
            Message.assistant("<thinking>Recent thinking</thinking>Answer 3"),
        ]
        compaction = ThoughtStripCompaction(preserve_recent=1)
        result = compaction.compact(messages, budget=8000)
        # First two assistant messages should have thinking stripped
        assert "<thinking>" not in result[1].content
        assert "<thinking>" not in result[3].content
        # Last assistant message should keep thinking
        assert "<thinking>" in result[5].content

    def test_no_strip_when_few_messages(self):
        from chimera.compaction.thought_strip import ThoughtStripCompaction
        messages = [
            Message.user("Hi"),
            Message.assistant("<thinking>Thinking</thinking>Hello"),
        ]
        compaction = ThoughtStripCompaction(preserve_recent=2)
        result = compaction.compact(messages, budget=8000)
        assert "<thinking>" in result[1].content

    def test_estimate_thinking_tokens(self):
        from chimera.compaction.thought_strip import estimate_thinking_tokens
        messages = [
            Message.assistant("<thinking>A long thought process here</thinking>Answer"),
            Message.assistant("No thinking here"),
        ]
        tokens = estimate_thinking_tokens(messages)
        assert tokens > 0


# === Task 83: Response caching ===

class TestResponseCaching:
    def test_cache_hit(self):
        from chimera.providers.cached import CachedProvider
        from chimera.providers.base import Response
        mock = MagicMock()
        mock.model_name = "test"
        mock.complete.return_value = Response(content="Hello", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})
        cached = CachedProvider(mock, max_entries=10)
        msgs = [Message.user("Hi")]
        r1 = cached.complete(msgs)
        r2 = cached.complete(msgs)
        assert r1.content == r2.content
        assert mock.complete.call_count == 1  # Only one actual API call
        assert cached.stats.hits == 1
        assert cached.stats.misses == 1

    def test_different_prompts_no_hit(self):
        from chimera.providers.cached import CachedProvider
        from chimera.providers.base import Response
        mock = MagicMock()
        mock.model_name = "test"
        mock.complete.return_value = Response(content="R", tool_calls=[], usage={})
        cached = CachedProvider(mock)
        cached.complete([Message.user("Hello")])
        cached.complete([Message.user("World")])
        assert mock.complete.call_count == 2

    def test_lru_eviction(self):
        from chimera.providers.cached import CachedProvider
        from chimera.providers.base import Response
        mock = MagicMock()
        mock.model_name = "test"
        mock.complete.return_value = Response(content="R", tool_calls=[], usage={})
        cached = CachedProvider(mock, max_entries=2)
        cached.complete([Message.user("A")])
        cached.complete([Message.user("B")])
        cached.complete([Message.user("C")])  # Evicts A
        assert cached.stats.evictions == 1

    def test_thread_safe(self):
        import threading
        from chimera.providers.cached import CachedProvider
        from chimera.providers.base import Response
        mock = MagicMock()
        mock.model_name = "test"
        mock.complete.return_value = Response(content="R", tool_calls=[], usage={})
        cached = CachedProvider(mock)
        threads = [threading.Thread(target=lambda: cached.complete([Message.user(f"msg{i}")])) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        # Should not crash

    def test_thinking_is_part_of_cache_key(self):
        """Regression: thinking=off vs thinking=high must not share cache slot."""
        from chimera.providers.cached import CachedProvider
        from chimera.providers.base import Response
        from chimera.providers.thinking import ThinkingLevel
        mock = MagicMock()
        mock.model_name = "test"
        mock.complete.return_value = Response(content="R", tool_calls=[], usage={})
        cached = CachedProvider(mock)
        msgs = [Message.user("hi")]
        cached.complete(msgs, thinking=ThinkingLevel.OFF)
        cached.complete(msgs, thinking=ThinkingLevel.HIGH)
        # Two calls with different thinking must hit the wrapped provider twice.
        assert mock.complete.call_count == 2
        # And thinking is forwarded
        assert mock.complete.call_args_list[-1].kwargs.get("thinking") == ThinkingLevel.HIGH


# === Task 84: LSP feedback ===

class TestLSPFeedback:
    def test_tracks_modified_files(self):
        from chimera.core.lsp_feedback import LSPFeedbackMiddleware
        lsp = MagicMock()
        mw = LSPFeedbackMiddleware(lsp)
        response = MagicMock()
        response.has_tool_calls = True
        response.tool_calls = [ToolCall(id="1", name="write_file", arguments={"path": "a.py"})]
        context = MagicMock()
        mw.after_model(response, context)
        assert "a.py" in mw._modified_files

    def test_ignores_read_tools(self):
        from chimera.core.lsp_feedback import LSPFeedbackMiddleware
        lsp = MagicMock()
        mw = LSPFeedbackMiddleware(lsp)
        response = MagicMock()
        response.has_tool_calls = True
        response.tool_calls = [ToolCall(id="1", name="read_file", arguments={"path": "a.py"})]
        context = MagicMock()
        mw.after_model(response, context)
        assert len(mw._modified_files) == 0


# === Task 85: File watcher ===

class TestFileWatcher:
    def test_detect_new_file(self, tmp_path):
        from chimera.env.watcher import FileWatcher
        watcher = FileWatcher(str(tmp_path), patterns=["*.py"])
        watcher._snapshots = watcher._scan()
        (tmp_path / "new.py").write_text("x = 1")
        changes = watcher.check_once()
        assert len(changes) == 1
        assert changes[0].path == "new.py"
        assert changes[0].change_type.value == "created"

    def test_detect_modified_file(self, tmp_path):
        from chimera.env.watcher import FileWatcher
        (tmp_path / "a.py").write_text("v1")
        watcher = FileWatcher(str(tmp_path), patterns=["*.py"])
        watcher._snapshots = watcher._scan()
        time.sleep(0.05)
        (tmp_path / "a.py").write_text("v2")
        changes = watcher.check_once()
        assert any(c.change_type.value == "modified" for c in changes)

    def test_detect_deleted_file(self, tmp_path):
        from chimera.env.watcher import FileWatcher
        (tmp_path / "a.py").write_text("x")
        watcher = FileWatcher(str(tmp_path), patterns=["*.py"])
        watcher._snapshots = watcher._scan()
        (tmp_path / "a.py").unlink()
        changes = watcher.check_once()
        assert any(c.change_type.value == "deleted" for c in changes)

    def test_ignores_pycache(self, tmp_path):
        from chimera.env.watcher import FileWatcher
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-312.pyc").write_bytes(b"x")
        watcher = FileWatcher(str(tmp_path), patterns=["*"])
        snap = watcher._scan()
        assert not any("__pycache__" in k for k in snap)

    def test_callback_fires(self, tmp_path):
        from chimera.env.watcher import FileWatcher
        watcher = FileWatcher(str(tmp_path), patterns=["*.txt"])
        watcher._snapshots = watcher._scan()
        received = []
        watcher.on_change(lambda changes: received.extend(changes))
        (tmp_path / "test.txt").write_text("hello")
        changes = watcher.check_once()
        # Manually trigger callbacks (check_once doesn't fire them)
        for cb in watcher._callbacks:
            cb(changes)
        assert len(received) == 1
