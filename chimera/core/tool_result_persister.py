"""Persist large tool results to disk and retrieve them later."""

from __future__ import annotations

from pathlib import Path

try:
    import aiofiles

    _HAS_AIOFILES = True
except ImportError:  # pragma: no cover
    _HAS_AIOFILES = False

import asyncio


class ToolResultPersister:
    """Write and read tool results to/from a session directory.

    Files are stored under ``session_dir / "tool-results" / <tool_use_id>.txt``.
    """

    def __init__(self, session_dir: Path, preview_size: int = 2048) -> None:
        self._dir = session_dir / "tool-results"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._preview_size = preview_size

    async def persist(self, tool_use_id: str, result: str) -> tuple[str, str]:
        """Write *result* to disk and return ``(path, preview)``."""
        path = self._dir / f"{tool_use_id}.json"
        if _HAS_AIOFILES:
            async with aiofiles.open(path, "w") as f:
                await f.write(result)
        else:
            await asyncio.to_thread(path.write_text, result)
        preview = self._generate_preview(result)
        return str(path), preview

    async def read(self, tool_use_id: str) -> str | None:
        """Read a previously-persisted result, or return ``None``."""
        path = self._dir / f"{tool_use_id}.json"
        if not path.exists():
            return None
        if _HAS_AIOFILES:
            async with aiofiles.open(path, "r") as f:
                return await f.read()
        else:
            return await asyncio.to_thread(path.read_text)

    def _generate_preview(self, content: str) -> str:
        """Return first half + '... [truncated] ...' + last half of preview bytes."""
        if len(content) <= self._preview_size:
            return content
        half = self._preview_size // 2
        return content[:half] + "\n... [truncated] ...\n" + content[-half:]
