"""FileWatcher: detect file and cwd changes, emit hook events."""
from __future__ import annotations

import os

from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent


class FileWatcher:
    """Watch for file changes and emit hook events."""

    def __init__(self, emitter: HookEmitter | None = None) -> None:
        self._emitter = emitter
        self._known_mtimes: dict[str, float] = {}
        self._cwd: str | None = None

    async def check_cwd(self, current_cwd: str) -> None:
        """Emit CWD_CHANGED if the working directory changed since last check."""
        if self._cwd is not None and self._cwd != current_cwd:
            if self._emitter:
                await self._emitter.emit(
                    HookEvent.CWD_CHANGED,
                    tool_input={"old": self._cwd, "new": current_cwd},
                )
        self._cwd = current_cwd

    async def check_files(self, paths: list[str]) -> None:
        """Emit FILE_CHANGED for any tracked path whose mtime changed."""
        for path in paths:
            try:
                mtime = os.path.getmtime(path)
                if path in self._known_mtimes and self._known_mtimes[path] != mtime:
                    if self._emitter:
                        await self._emitter.emit(
                            HookEvent.FILE_CHANGED,
                            tool_input={"path": path},
                        )
                self._known_mtimes[path] = mtime
            except OSError:
                pass

    def track(self, path: str) -> None:
        """Start tracking a file's mtime without emitting an event."""
        try:
            self._known_mtimes[path] = os.path.getmtime(path)
        except OSError:
            pass
