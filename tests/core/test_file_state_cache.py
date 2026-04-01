"""Tests for FileStateCache."""

import tempfile
import time
from pathlib import Path

from chimera.core.file_state_cache import FileStateCache


def test_cache_hit():
    """get returns cached content when mtime hasn't changed."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.txt"
        p.write_text("hello world")
        mtime = p.stat().st_mtime

        cache = FileStateCache()
        cache.put(str(p), "hello world", mtime, offset=None, limit=None)

        entry = cache.get(str(p), offset=None, limit=None)
        assert entry is not None
        assert entry.content == "hello world"
        assert entry.mtime == mtime


def test_cache_miss_after_modification():
    """get returns None when the file's mtime has changed since caching."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.txt"
        p.write_text("original")
        mtime_orig = p.stat().st_mtime

        cache = FileStateCache()
        cache.put(str(p), "original", mtime_orig, offset=None, limit=None)

        # Modify the file (ensure mtime changes)
        time.sleep(0.05)
        p.write_text("modified")

        entry = cache.get(str(p), offset=None, limit=None)
        assert entry is None


def test_clone_independent():
    """Cloned cache is independent from the original."""
    with tempfile.TemporaryDirectory() as tmp:
        ab = Path(tmp) / "b.txt"
        ab.write_text("content")
        cd = Path(tmp) / "d.txt"
        cd.write_text("other")

        cache = FileStateCache()
        cache.put(str(ab), "content", mtime=ab.stat().st_mtime, offset=None, limit=None)

        cloned = cache.clone()
        cloned.put(str(cd), "other", mtime=cd.stat().st_mtime, offset=None, limit=None)

        assert cloned.get(str(cd), offset=None, limit=None) is not None
        assert cache.get(str(cd), offset=None, limit=None) is None


def test_lru_eviction():
    """Oldest entries are evicted when max_entries is exceeded."""
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
            p = Path(tmp) / name
            p.write_text(name)
            files[name] = p

        cache = FileStateCache(max_entries=3)
        cache.put(str(files["a.txt"]), "a", mtime=files["a.txt"].stat().st_mtime, offset=None, limit=None)
        cache.put(str(files["b.txt"]), "b", mtime=files["b.txt"].stat().st_mtime, offset=None, limit=None)
        cache.put(str(files["c.txt"]), "c", mtime=files["c.txt"].stat().st_mtime, offset=None, limit=None)

        # Adding a 4th should evict a.txt (LRU)
        cache.put(str(files["d.txt"]), "d", mtime=files["d.txt"].stat().st_mtime, offset=None, limit=None)

        assert cache.get(str(files["a.txt"]), offset=None, limit=None) is None
        assert cache.get(str(files["b.txt"]), offset=None, limit=None) is not None
        assert cache.get(str(files["d.txt"]), offset=None, limit=None) is not None
