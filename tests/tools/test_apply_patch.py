"""Tests for chimera.tools.apply_patch — Phase 9."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.tools.apply_patch import ApplyPatchTool
from chimera.types import ToolResult


class TestApplyPatchTool:
    """ApplyPatchTool applies structured patches to files on disk."""

    def test_apply_patch_updates_file(self, tmp_path: Path, monkeypatch):
        """Patching an existing file should update its content."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "hello.py"
        target.write_text("def hello():\n    return 'old'\n")

        patch_text = """\
*** Begin Patch
*** Update File: hello.py
 def hello():
-    return 'old'
+    return 'new'
*** End Patch"""

        tool = ApplyPatchTool()
        result = tool.execute({"patch": patch_text}, env=None)
        assert result.error is None
        assert "Updated hello.py" in result.output

        updated = target.read_text()
        assert "return 'new'" in updated
        assert "return 'old'" not in updated

    def test_apply_patch_creates_file(self, tmp_path: Path, monkeypatch):
        """Adding a new file via patch should create it."""
        monkeypatch.chdir(tmp_path)

        patch_text = """\
*** Begin Patch
*** Add File: new_module.py
+print("created")
+x = 42
*** End Patch"""

        tool = ApplyPatchTool()
        result = tool.execute({"patch": patch_text}, env=None)
        assert result.error is None
        assert "Created new_module.py" in result.output

        created = (tmp_path / "new_module.py").read_text()
        assert 'print("created")' in created
        assert "x = 42" in created
