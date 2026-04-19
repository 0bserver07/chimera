"""Serialize concurrent file mutations per-path.

Multiple tools can edit different files in parallel,
but edits to the same file are serialized to prevent races.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class FileMutationQueue:
    """Serialize concurrent file mutations per-path.

    Multiple tools can edit different files in parallel,
    but edits to the same file are serialized to prevent races.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, path: str) -> None:
        """Acquire the lock for a file path."""
        await self._locks[path].acquire()

    def release(self, path: str) -> None:
        """Release the lock for a file path."""
        if path in self._locks:
            self._locks[path].release()

    async def __aenter__(self) -> "FileMutationQueue":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def lock(self, path: str) -> "_FileLock":
        """Context manager for a specific file path."""
        return _FileLock(self, path)


class _FileLock:
    def __init__(self, queue: FileMutationQueue, path: str) -> None:
        self._queue = queue
        self._path = path

    async def __aenter__(self) -> "_FileLock":
        await self._queue.acquire(self._path)
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._queue.release(self._path)
