# chimera/tools/list_files.py
from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from chimera.config.ignore import NOT_SOURCE_DIRS
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import SearchOps


# Vendored / generated / VCS / IDE directories that bloat a recursive listing
# without being source the agent works on. A single unfiltered `list_files(".")`
# in a repo with a `.venv` or `node_modules` can return millions of tokens and
# blow past a model's prompt limit. These are skipped only when listing a
# *parent* directory — an explicit `path` INTO one of them still lists it (see
# `_render`), so nothing is truly hidden.
#
# The set itself lives in `chimera/config/ignore.py`, shared with repo_map,
# definition_lookup, and the checkpoint writer. This module's list was the
# richest of the three that existed, including the finding that bare ``env``
# must stay out of it (it collides with real source dirs like ``chimera/env``);
# that reasoning moved with the set and is pinned there by a test.
_IGNORED_DIRS: frozenset[str] = NOT_SOURCE_DIRS

# Cap the number of paths returned; huge listings are unhelpful to the model and
# risk the prompt limit. The truncation note tells the agent how to narrow.
_MAX_ENTRIES = 1000


class ListFilesTool(BaseTool):
    name = "list_files"
    description = (
        "List files in a directory, optionally filtered by glob pattern. "
        "Skips vendored/generated dirs (.git, .venv, node_modules, __pycache__, "
        "dist, build, …) unless you target one explicitly via `path`, and caps "
        "very large listings — narrow with `glob` or a subdirectory `path`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list", "default": "."},
            "glob": {"type": "string", "description": "Glob filter (e.g. '*.py')", "default": None},
        },
        "required": [],
    }

    def __init__(self, ops: SearchOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args.get("path", ".")
        glob_filter = args.get("glob")

        if self._ops is not None:
            files = self._ops.list_files("**/*")
        else:
            assert env is not None
            files = env.list_files("**/*")

        if path != ".":
            files = [f for f in files if f.startswith(path + "/") or f.startswith(path)]
        if glob_filter:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]

        return self._render(files, path)

    @staticmethod
    def _render(files: list[str], path: str) -> ToolResult:
        """Drop ignored-dir paths (relative to *path*), sort, and cap."""
        base = "" if path in (".", "") else path.rstrip("/") + "/"
        kept: list[str] = []
        for f in files:
            rel = f[len(base):] if base and f.startswith(base) else f
            # Check directory segments only (never the filename itself), so a
            # file literally named "build" is fine while a "build/" dir is skipped.
            if any(seg in _IGNORED_DIRS for seg in rel.split("/")[:-1]):
                continue
            kept.append(f)

        if not kept:
            return ToolResult(output="No files found.")

        kept.sort()
        total = len(kept)
        if total > _MAX_ENTRIES:
            shown = "\n".join(kept[:_MAX_ENTRIES])
            note = (
                f"\n\n… {total - _MAX_ENTRIES} more not shown (showing "
                f"{_MAX_ENTRIES} of {total}). Narrow with a glob (e.g. "
                f"glob='*.py') or a subdirectory path."
            )
            return ToolResult(output=shown + note)
        return ToolResult(output="\n".join(kept))
