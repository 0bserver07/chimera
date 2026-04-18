"""Tests for chimera.core.patch_parser — Phase 9."""
from __future__ import annotations

import pytest

from chimera.core.patch_parser import PatchParser, PatchHunk, FilePatch


class TestParsing:
    """Patch text parsing."""

    def test_parse_simple_update(self):
        patch_text = """\
*** Begin Patch
*** Update File: src/main.py
 def hello():
-    return "old"
+    return "new"
*** End Patch"""
        parser = PatchParser()
        patches = parser.parse(patch_text)
        assert len(patches) == 1
        fp = patches[0]
        assert fp.path == "src/main.py"
        assert fp.operation == "update"
        assert len(fp.hunks) == 1
        assert fp.hunks[0].context_before == ["def hello():"]
        assert fp.hunks[0].removals == ['    return "old"']
        assert fp.hunks[0].additions == ['    return "new"']

    def test_parse_add_file(self):
        patch_text = """\
*** Begin Patch
*** Add File: new_file.py
+print("hello")
+print("world")
*** End Patch"""
        parser = PatchParser()
        patches = parser.parse(patch_text)
        assert len(patches) == 1
        fp = patches[0]
        assert fp.path == "new_file.py"
        assert fp.operation == "add"
        assert len(fp.hunks) == 1
        assert fp.hunks[0].additions == ['print("hello")', 'print("world")']

    def test_parse_delete_file(self):
        patch_text = """\
*** Begin Patch
*** Delete File: old_file.py
*** End Patch"""
        parser = PatchParser()
        patches = parser.parse(patch_text)
        assert len(patches) == 1
        fp = patches[0]
        assert fp.path == "old_file.py"
        assert fp.operation == "delete"

    def test_multi_file_patch(self):
        patch_text = """\
*** Begin Patch
*** Update File: a.py
 x = 1
-y = 2
+y = 3
*** Add File: b.py
+new content
*** Delete File: c.py
*** End Patch"""
        parser = PatchParser()
        patches = parser.parse(patch_text)
        assert len(patches) == 3
        assert patches[0].path == "a.py"
        assert patches[0].operation == "update"
        assert patches[1].path == "b.py"
        assert patches[1].operation == "add"
        assert patches[2].path == "c.py"
        assert patches[2].operation == "delete"


class TestApply:
    """Hunk application with different match strategies."""

    def test_apply_hunk_exact_match(self):
        parser = PatchParser()
        content = "def hello():\n    return 'old'\n"
        fp = FilePatch(
            path="test.py",
            operation="update",
            hunks=[PatchHunk(
                context_before=["def hello():"],
                removals=["    return 'old'"],
                additions=["    return 'new'"],
                context_after=[],
            )],
        )
        result = parser.apply(fp, content)
        assert "return 'new'" in result
        assert "return 'old'" not in result

    def test_apply_hunk_rstrip_match(self):
        """Trailing whitespace should still match via RSTRIP pass."""
        parser = PatchParser()
        # Content has trailing spaces on the context line
        content = "def hello():   \n    return 'old'\n"
        fp = FilePatch(
            path="test.py",
            operation="update",
            hunks=[PatchHunk(
                context_before=["def hello():"],
                removals=["    return 'old'"],
                additions=["    return 'new'"],
                context_after=[],
            )],
        )
        result = parser.apply(fp, content)
        assert "return 'new'" in result

    def test_apply_hunk_trim_match(self):
        """Leading+trailing whitespace should match via TRIM pass."""
        parser = PatchParser()
        content = "  def hello():  \n    return 'old'\n"
        fp = FilePatch(
            path="test.py",
            operation="update",
            hunks=[PatchHunk(
                context_before=["def hello():"],
                removals=["    return 'old'"],
                additions=["    return 'new'"],
                context_after=[],
            )],
        )
        result = parser.apply(fp, content)
        assert "return 'new'" in result

    def test_no_match_raises(self):
        """If no match is found, ValueError should be raised."""
        parser = PatchParser()
        content = "completely different content\n"
        fp = FilePatch(
            path="test.py",
            operation="update",
            hunks=[PatchHunk(
                context_before=["def nonexistent():"],
                removals=["    x = 1"],
                additions=["    x = 2"],
                context_after=[],
            )],
        )
        with pytest.raises(ValueError, match="Could not find match"):
            parser.apply(fp, content)
