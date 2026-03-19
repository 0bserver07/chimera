"""REPL tab completion for slash commands, file paths, and @mentions.

Provides readline-compatible completion for the chimera code REPL.

Inspired by Cursor/Windsurf's autocomplete UX.
"""
from __future__ import annotations

import os
import readline
from pathlib import Path
from typing import Any

# Slash commands available in the REPL
_SLASH_COMMANDS = [
    "/help", "/model", "/cost", "/clear", "/history", "/tools",
    "/context", "/debug", "/session", "/compact", "/audit",
    "/checkpoint", "/agent", "/init", "/yolo", "/exit",
]


class ReplCompleter:
    """Tab completion for the chimera REPL.

    Completes:
    - Slash commands: /he<TAB> → /help
    - File paths: src/ma<TAB> → src/main.py
    - @mentions: @fil<TAB> → @file:

    Example::

        completer = ReplCompleter(workdir="/path/to/project")
        completer.install()
        # Now readline will use our completer
    """

    def __init__(self, workdir: str = ".") -> None:
        self._workdir = workdir
        self._matches: list[str] = []

    def install(self) -> None:
        """Register this completer with readline."""
        readline.set_completer(self.complete)
        readline.set_completer_delims(" \t\n;")
        readline.parse_and_bind("tab: complete")

    def complete(self, text: str, state: int) -> str | None:
        """Readline completion function."""
        if state == 0:
            self._matches = self._find_matches(text)
        if state < len(self._matches):
            return self._matches[state]
        return None

    def _find_matches(self, text: str) -> list[str]:
        """Find all completions for the given text."""
        if text.startswith("/"):
            return self._complete_commands(text)
        elif text.startswith("@"):
            return self._complete_mentions(text)
        else:
            return self._complete_files(text)

    def _complete_commands(self, text: str) -> list[str]:
        """Complete slash commands."""
        return [cmd for cmd in _SLASH_COMMANDS if cmd.startswith(text)]

    def _complete_mentions(self, text: str) -> list[str]:
        """Complete @mentions."""
        mention_types = ["@file:", "@folder:", "@url:"]
        prefix = text
        # If just "@", show all types
        if prefix == "@":
            return mention_types

        # Complete mention types
        matches = [m for m in mention_types if m.startswith(prefix)]
        if matches:
            return matches

        # If @file:path, complete the path part
        if text.startswith("@file:"):
            path_prefix = text[6:]
            files = self._list_files(path_prefix)
            return [f"@file:{f}" for f in files]

        if text.startswith("@folder:"):
            path_prefix = text[8:]
            dirs = self._list_dirs(path_prefix)
            return [f"@folder:{d}" for d in dirs]

        return []

    def _complete_files(self, text: str) -> list[str]:
        """Complete file paths relative to workdir."""
        return self._list_files(text)

    def _list_files(self, prefix: str) -> list[str]:
        """List files matching a prefix."""
        try:
            if os.path.sep in prefix or "/" in prefix:
                dir_part = os.path.dirname(prefix)
                base_part = os.path.basename(prefix)
                search_dir = os.path.join(self._workdir, dir_part)
            else:
                dir_part = ""
                base_part = prefix
                search_dir = self._workdir

            if not os.path.isdir(search_dir):
                return []

            matches: list[str] = []
            for entry in os.listdir(search_dir):
                if entry.startswith("."):
                    continue
                if entry.lower().startswith(base_part.lower()):
                    full = os.path.join(dir_part, entry) if dir_part else entry
                    if os.path.isdir(os.path.join(search_dir, entry)):
                        full += "/"
                    matches.append(full)
            return sorted(matches)
        except Exception:
            return []

    def _list_dirs(self, prefix: str) -> list[str]:
        """List directories matching a prefix."""
        files = self._list_files(prefix)
        return [f for f in files if f.endswith("/")]
