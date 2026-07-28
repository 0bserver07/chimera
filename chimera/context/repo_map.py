"""Repo map context injection: summarize codebase structure for the LLM.

Generates a concise map of repository files, classes, and functions,
then injects it into the system prompt. Gives the model codebase awareness
without reading every file.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from chimera.config.ignore import NOT_SOURCE_DIRS

# The shared non-source set (`chimera/config/ignore.py`). This module carried
# the only copy that knew about `.astro`; that entry moved into the shared set
# rather than being dropped.
_SKIP_DIRS = NOT_SOURCE_DIRS

_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb"}


def _extract_symbols_python(content: str) -> list[str]:
    """Extract class and function names from Python source."""
    symbols = []
    for m in re.finditer(r"^(class|def)\s+(\w+)", content, re.MULTILINE):
        kind = m.group(1)
        name = m.group(2)
        symbols.append(f"{'  ' if kind == 'def' else ''}{kind} {name}")
    return symbols


def _extract_symbols_js(content: str) -> list[str]:
    """Extract function/class/export names from JS/TS source."""
    symbols = []
    for m in re.finditer(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(function|class|const|let|var)\s+(\w+)",
        content,
        re.MULTILINE,
    ):
        symbols.append(f"{m.group(1)} {m.group(2)}")
    return symbols


def generate_repo_map(
    workdir: str | Path,
    max_tokens: int = 4000,
    depth: str = "function",
) -> str:
    """Generate a repo map string.

    Args:
        workdir: Root directory to scan.
        max_tokens: Approximate token budget (chars / 4).
        depth: "file" (just filenames), "class" (classes only),
               or "function" (classes + functions).

    Returns:
        A formatted string suitable for injection into a system prompt.
    """
    root = Path(workdir)
    max_chars = max_tokens * 4
    lines: list[str] = ["Repository structure:"]
    char_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden and build directories
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        dirnames.sort()

        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1]
            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname

            if depth == "file" or ext not in _CODE_EXTS:
                line = f"  {rel_path}"
                lines.append(line)
                char_count += len(line)
            else:
                lines.append(f"  {rel_path}")
                char_count += len(rel_path) + 4

                # Extract symbols
                try:
                    content = Path(dirpath, fname).read_text(
                        encoding="utf-8", errors="ignore"
                    )
                except Exception:
                    continue

                if ext == ".py":
                    symbols = _extract_symbols_python(content)
                elif ext in (".js", ".ts", ".jsx", ".tsx"):
                    symbols = _extract_symbols_js(content)
                else:
                    symbols = []

                if depth == "class":
                    symbols = [s for s in symbols if s.strip().startswith("class")]

                for sym in symbols:
                    line = f"    {sym}"
                    lines.append(line)
                    char_count += len(line)

            if char_count > max_chars:
                lines.append("  [... truncated to fit token budget ...]")
                return "\n".join(lines)

    return "\n".join(lines)


class RepoMapMiddleware:
    """Middleware that injects a repo map into the context before the first LLM call.

    Usage::

        from chimera.core.middleware import LoopMiddleware

        mw = RepoMapMiddleware(workdir="/path/to/project", max_tokens=3000)
        config = LoopConfig(middleware=[mw])
    """

    def __init__(
        self,
        workdir: str,
        max_tokens: int = 3000,
        depth: str = "function",
    ) -> None:
        self._workdir = workdir
        self._max_tokens = max_tokens
        self._depth = depth
        self._injected = False
        self._map: str | None = None

    def before_model(self, context: object, tools: object) -> object:
        """Inject repo map into context on the first call."""
        if self._injected:
            return context

        if self._map is None:
            self._map = generate_repo_map(
                self._workdir, self._max_tokens, self._depth
            )

        # Inject as a system message at the start of context
        from chimera.types import Message

        messages = getattr(context, "messages", None)
        if messages is not None and self._map:
            messages.insert(0, Message.system(self._map))
            self._injected = True

        return context
