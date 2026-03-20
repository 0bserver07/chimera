# tests/test_ai_comment_watcher.py
"""Tests for AICommentWatcher in chimera.env.watcher."""
import tempfile
from pathlib import Path

from chimera.env.watcher import AIComment, AICommentWatcher


class TestAICommentWatcher:
    def test_scan_python_comment(self, tmp_path):
        pyfile = tmp_path / "main.py"
        pyfile.write_text("x = 1\n# AI: fix the bug here\ny = 2\n")
        watcher = AICommentWatcher(str(tmp_path))
        comments = watcher.scan_file("main.py")
        assert len(comments) == 1
        assert comments[0].directive == "fix the bug here"
        assert comments[0].line_number == 2
        assert comments[0].path == "main.py"

    def test_scan_js_comment(self, tmp_path):
        jsfile = tmp_path / "app.js"
        jsfile.write_text('const x = 1; // AI: add error handling\n')
        watcher = AICommentWatcher(str(tmp_path), patterns=["*.js"])
        comments = watcher.scan_file("app.js")
        assert len(comments) == 1
        assert comments[0].directive == "add error handling"

    def test_scan_no_ai_comments(self, tmp_path):
        pyfile = tmp_path / "clean.py"
        pyfile.write_text("# This is a normal comment\nx = 42\n")
        watcher = AICommentWatcher(str(tmp_path))
        comments = watcher.scan_file("clean.py")
        assert len(comments) == 0

    def test_scan_multiple_comments(self, tmp_path):
        pyfile = tmp_path / "multi.py"
        pyfile.write_text(
            "# AI: first task\n"
            "x = 1\n"
            "# AI: second task\n"
            "y = 2\n"
        )
        watcher = AICommentWatcher(str(tmp_path))
        comments = watcher.scan_file("multi.py")
        assert len(comments) == 2
        assert comments[0].directive == "first task"
        assert comments[1].directive == "second task"

    def test_scan_nonexistent_file(self, tmp_path):
        watcher = AICommentWatcher(str(tmp_path))
        comments = watcher.scan_file("does_not_exist.py")
        assert comments == []
