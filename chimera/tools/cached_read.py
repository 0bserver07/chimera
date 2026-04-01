"""CachedReadTool: ReadFileTool with FileStateCache integration."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chimera.core.file_state_cache import FileStateCache
from chimera.tools.read import ReadFileTool
from chimera.types import ToolResult


class CachedReadTool(ReadFileTool):
    """ReadFileTool that checks a :class:`FileStateCache` before reading.

    On a cache hit (file unchanged since last read), returns a stub message
    instead of re-reading the file.  On a miss, reads normally and populates
    the cache for future calls.
    """

    def __init__(
        self,
        cache: FileStateCache | None = None,
        ops=None,
    ) -> None:
        super().__init__(ops=ops)
        self._cache = cache

    def execute(self, args: dict[str, Any], env=None) -> ToolResult:
        path = args.get("file_path") or args.get("path", "")

        # Check cache first
        if self._cache:
            content, was_cached = self._cache.check_and_read(path)
            if was_cached:
                return ToolResult(output="[File unchanged since last read]")

        # Cache miss or no cache — try parent execute, falling back to
        # direct filesystem read when neither ops nor env is provided.
        if self._ops is not None or env is not None:
            # Normalise args so the parent always sees "path"
            normalised = dict(args)
            if "path" not in normalised and "file_path" in normalised:
                normalised["path"] = normalised.pop("file_path")
            result = super().execute(normalised, env)
        else:
            # Direct read (no ops/env available)
            try:
                content = Path(path).read_text()
                result = ToolResult(output=content)
            except FileNotFoundError:
                result = ToolResult(output="", error=f"File not found: {path}")
            except OSError as exc:
                result = ToolResult(output="", error=str(exc))

        # Populate cache on successful read
        if self._cache and result.success:
            try:
                mtime = os.path.getmtime(path)
                self._cache.put(path, result.output, mtime, None, None)
            except OSError:
                pass

        return result
