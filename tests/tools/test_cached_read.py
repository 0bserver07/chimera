"""Tests for chimera.tools.cached_read — CachedReadTool with FileStateCache."""
from __future__ import annotations

import os


from chimera.core.file_state_cache import FileStateCache
from chimera.tools.cached_read import CachedReadTool


# ---------------------------------------------------------------------------
# Tests: cache hit
# ---------------------------------------------------------------------------


class TestCacheHit:
    def test_cache_hit_returns_stub(self, tmp_path):
        """When the file is cached and unchanged, return the stub message."""
        test_file = tmp_path / "cached.txt"
        test_file.write_text("original content")

        cache = FileStateCache()
        mtime = os.path.getmtime(str(test_file))
        cache.put(str(test_file), "original content", mtime, None, None)

        tool = CachedReadTool(cache=cache)
        result = tool.execute({"path": str(test_file)}, env=None)

        assert result.success
        assert "[File unchanged since last read]" in result.output

    def test_cache_miss_reads_file(self, tmp_path):
        """When the file is not cached, read the actual file content."""
        test_file = tmp_path / "fresh.txt"
        test_file.write_text("fresh content")

        cache = FileStateCache()
        tool = CachedReadTool(cache=cache)
        result = tool.execute({"path": str(test_file)}, env=None)

        assert result.success
        assert result.output == "fresh content"

    def test_cache_populated_after_miss(self, tmp_path):
        """After a cache miss, the file should be cached for next time."""
        test_file = tmp_path / "cacheable.txt"
        test_file.write_text("cacheable content")

        cache = FileStateCache()
        tool = CachedReadTool(cache=cache)

        # First read: miss
        result1 = tool.execute({"path": str(test_file)}, env=None)
        assert result1.output == "cacheable content"

        # Second read: hit (file unchanged)
        result2 = tool.execute({"path": str(test_file)}, env=None)
        assert "[File unchanged since last read]" in result2.output


# ---------------------------------------------------------------------------
# Tests: cache invalidation
# ---------------------------------------------------------------------------


class TestCacheInvalidation:
    def test_modified_file_cache_miss(self, tmp_path):
        """When file is modified after caching, it should be a cache miss."""
        test_file = tmp_path / "changing.txt"
        test_file.write_text("v1")

        cache = FileStateCache()
        tool = CachedReadTool(cache=cache)

        # First read: populates cache
        result1 = tool.execute({"path": str(test_file)}, env=None)
        assert result1.output == "v1"

        # Modify file (ensure mtime changes)
        import time
        time.sleep(0.05)
        test_file.write_text("v2")

        # Second read: cache miss because mtime changed
        result2 = tool.execute({"path": str(test_file)}, env=None)
        assert result2.output == "v2"


# ---------------------------------------------------------------------------
# Tests: no cache
# ---------------------------------------------------------------------------


class TestNoCache:
    def test_no_cache_reads_normally(self, tmp_path):
        """CachedReadTool without cache should behave like ReadFileTool."""
        test_file = tmp_path / "normal.txt"
        test_file.write_text("normal content")

        tool = CachedReadTool(cache=None)
        result = tool.execute({"path": str(test_file)}, env=None)

        assert result.success
        assert result.output == "normal content"

    def test_no_cache_file_not_found(self):
        """Missing file should return error."""
        tool = CachedReadTool(cache=None)
        result = tool.execute({"path": "/nonexistent/file.txt"}, env=None)
        assert not result.success
        assert "not found" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Tests: file_path alias
# ---------------------------------------------------------------------------


class TestFilePathAlias:
    def test_file_path_key_works(self, tmp_path):
        """The tool should accept 'file_path' as well as 'path'."""
        test_file = tmp_path / "aliased.txt"
        test_file.write_text("aliased content")

        cache = FileStateCache()
        tool = CachedReadTool(cache=cache)
        result = tool.execute({"file_path": str(test_file)}, env=None)

        assert result.success
        assert result.output == "aliased content"
