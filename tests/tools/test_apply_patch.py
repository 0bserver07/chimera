"""Tests for :mod:`chimera.tools.apply_patch` — W13-G1.

Covers parsing, multi-file apply, atomic rollback semantics, and the
add / update / delete operations of the structured patch DSL. The DSL
itself is owned by :mod:`chimera.core.patch_parser`; these tests
exercise the ApplyPatchTool orchestration layer that sits on top.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.tools.apply_patch import ApplyPatchTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch(body: str) -> str:
    """Wrap *body* in the ``*** Begin Patch`` / ``*** End Patch`` envelope."""
    return f"*** Begin Patch\n{body}\n*** End Patch"


# ---------------------------------------------------------------------------
# Update File
# ---------------------------------------------------------------------------


class TestUpdateFile:
    def test_apply_patch_updates_file(self, tmp_path: Path, monkeypatch):
        """Patching an existing file replaces the targeted line."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "hello.py"
        target.write_text("def hello():\n    return 'old'\n")

        patch_text = _patch(
            "*** Update File: hello.py\n"
            " def hello():\n"
            "-    return 'old'\n"
            "+    return 'new'"
        )

        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        assert "Updated hello.py" in result.output

        updated = target.read_text()
        assert "return 'new'" in updated
        assert "return 'old'" not in updated

    def test_update_with_multiple_hunks_in_same_file(
        self, tmp_path: Path, monkeypatch,
    ):
        """Two hunks within one Update File apply both changes."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "multi.txt"
        target.write_text("foo\nbar\nbaz\nqux\n")

        patch_text = _patch(
            "*** Update File: multi.txt\n"
            " foo\n"
            "-bar\n"
            "+BAR\n"
            " baz\n"
            "-qux\n"
            "+QUX"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        contents = target.read_text()
        assert "BAR" in contents
        assert "QUX" in contents
        assert "bar" not in contents.replace("BAR", "")
        assert "qux" not in contents.replace("QUX", "")


# ---------------------------------------------------------------------------
# Add File
# ---------------------------------------------------------------------------


class TestAddFile:
    def test_apply_patch_creates_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        patch_text = _patch(
            "*** Add File: new_module.py\n"
            "+print(\"created\")\n"
            "+x = 42"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        assert "Created new_module.py" in result.output

        created = (tmp_path / "new_module.py").read_text()
        assert 'print("created")' in created
        assert "x = 42" in created

    def test_add_creates_nested_parent_directories(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        patch_text = _patch(
            "*** Add File: pkg/sub/__init__.py\n"
            "+\"\"\"sub package.\"\"\""
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        target = tmp_path / "pkg" / "sub" / "__init__.py"
        assert target.exists()
        assert target.read_text().strip() == '"""sub package."""'

    def test_add_existing_file_is_rejected(
        self, tmp_path: Path, monkeypatch,
    ):
        """Add File must not clobber an existing file."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "exists.py").write_text("# already here\n")
        patch_text = _patch(
            "*** Add File: exists.py\n"
            "+# overwrite attempt"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is not None
        assert "cannot add existing file" in result.error
        # Original is untouched.
        assert (tmp_path / "exists.py").read_text() == "# already here\n"


# ---------------------------------------------------------------------------
# Delete File
# ---------------------------------------------------------------------------


class TestDeleteFile:
    def test_apply_patch_deletes_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "doomed.txt"
        target.write_text("goodbye\n")

        patch_text = _patch("*** Delete File: doomed.txt")
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        assert "Deleted doomed.txt" in result.output
        assert not target.exists()

    def test_delete_missing_file_is_rejected(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        patch_text = _patch("*** Delete File: nope.txt")
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is not None
        assert "cannot delete missing file" in result.error


# ---------------------------------------------------------------------------
# Multi-file
# ---------------------------------------------------------------------------


class TestMultiFile:
    def test_multi_file_patch_applies_all_operations(
        self, tmp_path: Path, monkeypatch,
    ):
        """Add + Update + Delete in one envelope all land successfully."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "to_update.py").write_text("x = 1\n")
        (tmp_path / "to_delete.py").write_text("# stale\n")

        patch_text = _patch(
            "*** Add File: added.py\n"
            "+# brand new\n"
            "*** Update File: to_update.py\n"
            "-x = 1\n"
            "+x = 2\n"
            "*** Delete File: to_delete.py"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        assert (tmp_path / "added.py").exists()
        assert "x = 2" in (tmp_path / "to_update.py").read_text()
        assert not (tmp_path / "to_delete.py").exists()

        # Summary lists every file once.
        assert result.output.count("Created added.py") == 1
        assert result.output.count("Updated to_update.py") == 1
        assert result.output.count("Deleted to_delete.py") == 1


# ---------------------------------------------------------------------------
# Parse / argument errors
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_missing_patch_argument_returns_error(self):
        result = ApplyPatchTool().execute({}, env=None)
        assert result.error is not None
        assert "non-empty string" in result.error

    def test_empty_patch_string_returns_error(self):
        result = ApplyPatchTool().execute({"patch": "   \n  "}, env=None)
        assert result.error is not None
        assert "non-empty string" in result.error

    def test_non_string_patch_argument_returns_error(self):
        # The tool is called by the agent with arbitrary JSON values; the
        # guard ensures we never crash on a misshapen call.
        result = ApplyPatchTool().execute({"patch": 42}, env=None)  # type: ignore[arg-type]
        assert result.error is not None

    def test_empty_envelope_returns_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Begin/End markers but no file hunks inside.
        patch_text = "*** Begin Patch\n*** End Patch"
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is not None
        assert "no file hunks" in result.error


# ---------------------------------------------------------------------------
# Conflict detection + atomic rollback
# ---------------------------------------------------------------------------


class TestConflictAndRollback:
    def test_unmatched_hunk_aborts_with_error(
        self, tmp_path: Path, monkeypatch,
    ):
        """A hunk whose context cannot be located returns a hunk-conflict error."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "stable.py"
        target.write_text("a = 1\nb = 2\n")

        patch_text = _patch(
            "*** Update File: stable.py\n"
            " a = 1\n"
            "-z = 999\n"
            "+z = 0"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is not None
        assert "hunk conflict in 'stable.py'" in result.error
        # File is untouched because validation failed before any write.
        assert target.read_text() == "a = 1\nb = 2\n"

    def test_atomic_rejection_skips_all_writes_when_one_hunk_fails(
        self, tmp_path: Path, monkeypatch,
    ):
        """If any file in a multi-file patch fails validation, none are written."""
        monkeypatch.chdir(tmp_path)
        good = tmp_path / "good.txt"
        good.write_text("a\n")
        bad = tmp_path / "bad.txt"
        bad.write_text("z\n")

        patch_text = _patch(
            "*** Update File: good.txt\n"
            "-a\n"
            "+A\n"
            "*** Update File: bad.txt\n"
            "-this-line-does-not-exist\n"
            "+anything"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is not None
        # First file untouched even though its hunk was valid.
        assert good.read_text() == "a\n"
        assert bad.read_text() == "z\n"

    def test_rollback_on_filesystem_error_mid_apply(
        self, tmp_path: Path, monkeypatch,
    ):
        """Simulate an OSError on the second write and verify the first reverts."""
        monkeypatch.chdir(tmp_path)
        a = tmp_path / "a.txt"
        a.write_text("alpha\n")
        b = tmp_path / "b.txt"
        b.write_text("beta\n")

        patch_text = _patch(
            "*** Update File: a.txt\n"
            "-alpha\n"
            "+ALPHA\n"
            "*** Update File: b.txt\n"
            "-beta\n"
            "+BETA"
        )

        # Patch Path.write_text so the second call raises OSError.
        original_write = Path.write_text
        calls: list[Path] = []

        def flaky(self_path: Path, data: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(self_path)
            if len(calls) == 2:
                raise OSError("disk full")
            return original_write(self_path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", flaky)
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        # Restore so rollback can use a working write.
        monkeypatch.setattr(Path, "write_text", original_write)

        assert result.error is not None
        assert "rolled back" in result.error
        # First file restored to original; second never written.
        assert a.read_text() == "alpha\n"
        assert b.read_text() == "beta\n"

    def test_rollback_unlinks_added_files_on_partial_failure(
        self, tmp_path: Path, monkeypatch,
    ):
        """A successful Add followed by a failing Update is rolled back fully."""
        monkeypatch.chdir(tmp_path)
        existing = tmp_path / "existing.txt"
        existing.write_text("before\n")

        patch_text = _patch(
            "*** Add File: brand_new.txt\n"
            "+hello\n"
            "*** Update File: existing.txt\n"
            "-before\n"
            "+after"
        )

        original_write = Path.write_text
        n = {"calls": 0}

        def flaky(self_path: Path, data: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            n["calls"] += 1
            if n["calls"] == 2:
                raise OSError("simulated failure")
            return original_write(self_path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", flaky)
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        monkeypatch.setattr(Path, "write_text", original_write)

        assert result.error is not None
        # Newly-added file removed by rollback.
        assert not (tmp_path / "brand_new.txt").exists()
        # Existing file untouched.
        assert existing.read_text() == "before\n"


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------


class TestSchema:
    def test_tool_is_marked_destructive(self):
        assert ApplyPatchTool.is_destructive is True

    def test_tool_name_and_required_param(self):
        tool = ApplyPatchTool()
        assert tool.name == "apply_patch"
        assert "patch" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["patch"]

    def test_metadata_lists_files_and_operations(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "u.txt").write_text("u\n")
        patch_text = _patch(
            "*** Update File: u.txt\n"
            "-u\n"
            "+U\n"
            "*** Add File: a.txt\n"
            "+brand new"
        )
        result = ApplyPatchTool().execute({"patch": patch_text}, env=None)
        assert result.error is None, result.error
        files = result.metadata["files"]
        ops = result.metadata["operations"]
        assert len(files) == 2
        assert ops == ["update", "add"]


# ---------------------------------------------------------------------------
# Trademark hygiene
# ---------------------------------------------------------------------------


class TestTrademarkHygiene:
    """The implementation must not reference the upstream brand strings.

    apply_patch is a tool name, not a brand. The chimera implementation
    is grounded in :mod:`chimera.core.patch_parser` primitives and
    references nothing about the upstream coding agent or its vendor.
    """

    @pytest.mark.parametrize("forbidden", ["codex", "openai"])
    def test_implementation_has_no_brand_strings(self, forbidden: str):
        from chimera.tools import apply_patch as module

        source = Path(module.__file__).read_text().lower()
        assert forbidden not in source
