"""Structured context injection via @-mentions.

Supports ``@file:path``, ``@folder:path``, and ``@url:https://...`` syntax
in user messages.  The :class:`MentionResolver` parses these mentions,
resolves their content (reading files, listing directories, or fetching
URLs), and returns both a cleaned version of the text and the resolved
:class:`Mention` objects.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.env.base import Environment


# Match @file:/@folder:/@url: followed by a run of non-whitespace
# characters. The reference group deliberately excludes trailing
# punctuation like ',' '.' ';' ')' ']' so that prose-embedded mentions
# such as "see @file:foo.py, please" resolve to ``foo.py`` rather than
# ``foo.py,``. The character class allows '.' and '/' inside the path
# (e.g. "foo.py", "src/a.py") but not as the final character.
_MENTION_RE = re.compile(r"@(file|folder|url):([^\s,;)\]]*[^\s,.;)\]])")


@dataclass
class Mention:
    """A resolved @-mention.

    Attributes:
        type: The mention kind — ``"file"``, ``"folder"``, or ``"url"``.
        reference: The raw reference string (e.g. ``"utils.py"``,
            ``"src/"``, ``"https://example.com"``).
        content: The resolved content for this mention.
    """

    type: str
    reference: str
    content: str


class MentionResolver:
    """Parse @-mentions from user text and resolve their content.

    Supports:
    - ``@file:path`` — read file content
    - ``@folder:path`` — list directory tree
    - ``@url:https://...`` — fetch URL content (if *httpx* is available)

    Args:
        env: Optional :class:`~chimera.env.base.Environment` to use for
            file and directory operations.  When ``None``, falls back to
            direct filesystem access relative to *workdir*.
        workdir: Working directory for direct filesystem fallback.
            Defaults to ``"."``.
    """

    def __init__(
        self, env: Environment | None = None, workdir: str | None = None
    ) -> None:
        self._env = env
        self._workdir = workdir or "."

    def resolve(self, text: str) -> tuple[str, list[Mention]]:
        """Parse @mentions, resolve content, return (cleaned_text, mentions).

        The cleaned text has @mentions removed.  Mentions are resolved
        to their content.

        Args:
            text: User message that may contain @-mentions.

        Returns:
            A tuple of *(cleaned_text, mentions)* where *cleaned_text*
            has the mention tokens removed and *mentions* is a list of
            resolved :class:`Mention` objects.
        """
        mentions: list[Mention] = []

        for match in _MENTION_RE.finditer(text):
            mention_type = match.group(1)
            reference = match.group(2)

            content = self._resolve_one(mention_type, reference)
            mentions.append(
                Mention(type=mention_type, reference=reference, content=content)
            )

        # Remove @mentions from text
        cleaned = _MENTION_RE.sub("", text).strip()
        # Clean up double spaces
        cleaned = re.sub(r"  +", " ", cleaned)

        return cleaned, mentions

    def inject(self, text: str) -> str:
        """Replace @mentions with their resolved content inline.

        Each mention is expanded to a labelled content block so the LLM
        sees the referenced material directly in the message.

        Args:
            text: User message that may contain @-mentions.

        Returns:
            The text with each @mention replaced by a content block.
        """

        def _replacer(match: re.Match[str]) -> str:
            mention_type = match.group(1)
            reference = match.group(2)
            content = self._resolve_one(mention_type, reference)
            return f"\n--- {mention_type}: {reference} ---\n{content}\n"

        return _MENTION_RE.sub(_replacer, text)

    def _resolve_one(self, mention_type: str, reference: str) -> str:
        """Resolve a single mention to its content.

        Args:
            mention_type: One of ``"file"``, ``"folder"``, ``"url"``.
            reference: The path or URL to resolve.

        Returns:
            The resolved content string, or an error placeholder when
            resolution fails.
        """
        if mention_type == "file":
            return self._resolve_file(reference)
        elif mention_type == "folder":
            return self._resolve_folder(reference)
        elif mention_type == "url":
            return self._resolve_url(reference)
        return f"[unknown mention type: {mention_type}]"

    def _resolve_file(self, path: str) -> str:
        """Read file content.

        Args:
            path: File path (workspace-relative when using an
                :class:`Environment`, otherwise relative to *workdir*).

        Returns:
            The file content, or an error placeholder.
        """
        if self._env:
            try:
                return self._env.read_file(path)
            except (FileNotFoundError, OSError):
                return f"[file not found: {path}]"
        # Fallback to direct filesystem
        full_path = os.path.join(self._workdir, path)
        try:
            with open(full_path) as f:
                return f.read()
        except (FileNotFoundError, OSError):
            return f"[file not found: {path}]"

    def _resolve_folder(self, path: str) -> str:
        """List directory contents.

        Args:
            path: Directory path (workspace-relative when using an
                :class:`Environment`, otherwise relative to *workdir*).

        Returns:
            A newline-separated list of file paths (up to 50 entries),
            or an error placeholder.
        """
        if self._env:
            try:
                files = self._env.list_files(os.path.join(path, "**/*"))
                return "\n".join(files[:50])  # limit to 50 entries
            except (FileNotFoundError, OSError):
                return f"[folder not found: {path}]"
        full_path = os.path.join(self._workdir, path)
        if not os.path.isdir(full_path):
            return f"[folder not found: {path}]"
        try:
            entries: list[str] = []
            for root, _dirs, files in os.walk(full_path):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), self._workdir)
                    entries.append(rel)
                    if len(entries) >= 50:
                        break
                if len(entries) >= 50:
                    break
            return "\n".join(entries) if entries else f"[empty folder: {path}]"
        except (FileNotFoundError, OSError):
            return f"[folder not found: {path}]"

    def _resolve_url(self, url: str) -> str:
        """Fetch URL content.

        Args:
            url: The URL to fetch.

        Returns:
            Up to 5000 characters of the response text, or an error
            placeholder.
        """
        try:
            import httpx  # type: ignore[import-not-found]

            resp = httpx.get(url, timeout=10, follow_redirects=True)
            resp.raise_for_status()
            # Return first 5000 chars of text content
            return resp.text[:5000]
        except ImportError:
            return f"[httpx not installed — cannot fetch {url}]"
        except Exception as exc:
            return f"[error fetching {url}: {exc}]"
